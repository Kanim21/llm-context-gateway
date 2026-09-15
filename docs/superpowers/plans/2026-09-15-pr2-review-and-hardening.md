# PR #2 (Visual Playbook Builder) — Review Fixes & Security Hardening

**Branch:** `feature/visual-playbook-builder` · **Repo:** `Kanim21/llm-context-gateway`
**Scope:** 6 review items + security hardening, with exact diffs, tests, and verification commands.

---

## Read this first — what the code already does

Two of the requested items are already in place on the branch. I'm flagging that up front rather than writing diffs that pretend otherwise:

- **Parameterized SQLite queries — already done.** Every query in `agent_gateway/orchestrator/store.py` and `agent_gateway/storage/sqlite_store.py` uses `?` placeholders with value tuples. The *only* dynamically-built SQL is `store.py:252` (`UPDATE run_step_records SET {', '.join(fields)}`), and its fragments are hardcoded column names (`"status = ?"`, …), **not** user input — so it is not injectable today. The item below hardens it against future regressions with an allowlist; it is not fixing a live vulnerability.
- **Explicit CORS for `localhost:3000` — already done.** `agent_gateway/proxy/server.py:104` sets `allow_origins=["http://localhost:3000"]`, and `test_orchestrator_server_wiring.py` already asserts it. The item below only tightens wildcards and makes the origin configurable.

Items **1, 2, 3 are interdependent** and must land together:

- Fixing the **EventBus queue leak (1)** turns the bus *live-only* (no buffering/replay).
- That means a subscriber connecting to an already-finished/paused run would never see a terminal event — so the **SSE endpoint (3)** must serve late/finished subscribers from the run snapshot.
- Making **`start_run` non-blocking (2)** changes what `POST /runs` returns (now `running`, not the final state), so several route/engine tests move from "assert on the POST response" to "poll the snapshot / await the run."

Because of this, the plan sequences them together and lists every existing test that changes.

> **Verification caveat (sandbox only):** in the current cloud/VM sandbox, 23 backend tests fail because `tiktoken` cannot download its `cl100k_base` vocab (egress to `openaipublic.blob.core.windows.net` is blocked, 403). These pass on your Mac where the vocab is cached. Pre-rebase and post-rebase both show **195 passed / 23 env-blocked**, so this is environmental, not a regression. All "expected: green" claims below assume your Mac (or any host that can reach the tiktoken CDN / has `TIKTOKEN_CACHE_DIR` populated).

---

## Item 1 — EventBus queue leak on unsubscribed runs

### Problem
`agent_gateway/orchestrator/events.py`: `publish()` calls `_queue_for()`, which **creates** a queue on first publish. The queue is only ever removed in `subscribe()`'s `finally`. So any run that publishes events but is **never subscribed** (user starts a run, never opens its page) leaves a queue — holding every buffered event/token — in `_queues` for the lifetime of the process. That is the leak.

### Fix
Make the bus **live-only**: the *subscriber* owns the queue (created on `subscribe`, dropped on exit); `publish` delivers only if a subscriber is attached and never creates a queue. Late/finished subscribers are served from the snapshot by the SSE endpoint (Item 3). This removes the leak by construction — an unsubscribed run can never create a queue.

```diff
--- a/agent_gateway/orchestrator/events.py
+++ b/agent_gateway/orchestrator/events.py
@@
-"""In-process, per-run pub/sub for SSE. One asyncio.Queue per run_id.
-
-Known v1 limitation: there is exactly one queue per run, so each event is
-delivered to exactly one subscriber. Two simultaneous viewers of the same
-run (two tabs, or a dev-mode double-mount) will steal events from each
-other rather than each seeing the full stream. The run page is built to
-render from the run snapshot rather than depend on live events for
-correctness, so this degrades the live glow, not the ability to act on a
-run.
-"""
+"""In-process, per-run pub/sub for SSE. Live-only: one asyncio.Queue per
+*attached subscriber*, created on subscribe and dropped when it leaves.
+
+The bus does not buffer or replay. A run that finishes before anyone
+watches, or that nobody ever watches, leaves no queue behind — that is
+what prevents the per-run queue leak (publish never creates a queue). A
+late or already-finished subscriber is served a single synthetic frame
+from the run snapshot by routes.stream_run_events, not from a buffer.
+
+Known v1 limitation: one queue per run, so two simultaneous viewers steal
+each other's live events. Correctness comes from the snapshot, so this
+degrades the live glow only.
+"""
@@
-TERMINAL_EVENTS: set[str] = {"run_completed", "run_failed", "gate_paused"}
+TERMINAL_EVENTS: set[str] = {"run_completed", "run_failed", "run_rejected", "gate_paused"}
@@
 @dataclass
 class EventBus:
     _queues: dict[str, asyncio.Queue] = field(default_factory=dict)
 
-    def _queue_for(self, run_id: str) -> asyncio.Queue:
-        if run_id not in self._queues:
-            self._queues[run_id] = asyncio.Queue()
-        return self._queues[run_id]
-
     def queue_count(self) -> int:
         """How many run queues are currently held in memory (for tests)."""
         return len(self._queues)
 
     def has_queue(self, run_id: str) -> bool:
         return run_id in self._queues
 
     async def publish(self, run_id: str, event_type: str, data: dict) -> None:
-        await self._queue_for(run_id).put((event_type, data))
+        # Live-only: deliver to the attached subscriber if there is one, and
+        # drop otherwise. Never create a queue here — a queue created by
+        # publish for a run nobody subscribes to is exactly the leak.
+        queue = self._queues.get(run_id)
+        if queue is not None:
+            await queue.put((event_type, data))
 
     async def subscribe(self, run_id: str):
-        queue = self._queue_for(run_id)
+        # The subscriber owns the queue for its lifetime.
+        queue: asyncio.Queue = asyncio.Queue()
+        self._queues[run_id] = queue
         try:
             while True:
                 event_type, data = await queue.get()
                 yield event_type, data
                 if event_type in TERMINAL_EVENTS:
                     return
         finally:
-            # Drop the queue once this stream ends, otherwise every run a
-            # process ever saw (and every buffered token) stays in memory
-            # for the lifetime of the process.
-            self._queues.pop(run_id, None)
+            # Only drop our own queue — a second subscriber may have replaced
+            # the dict entry, and its finally will drop that one.
+            if self._queues.get(run_id) is queue:
+                self._queues.pop(run_id, None)
```

> `run_rejected` is added to `TERMINAL_EVENTS` for Item 3's reject-termination gap (see there). Harmless to Item 1 in isolation.

### Tests (`tests/test_orchestrator_events.py`)
The 3 existing tests that publish **before** subscribing encode the old buffer-and-replay behavior. Under live-only they must publish **concurrently with** an attached subscriber. Rewrite:

```python
class TestEventBus:
    async def test_subscribe_receives_published_event(self):
        bus = EventBus()

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "step_started", {"step_index": 0})

        asyncio.create_task(publisher())
        received = []
        async for event_type, data in bus.subscribe("run_1"):
            received.append((event_type, data))
            break
        assert received == [("step_started", {"step_index": 0})]

    async def test_subscribe_stops_iterating_after_terminal_event(self):
        bus = EventBus()

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "step_started", {"step_index": 0})
            await bus.publish("run_1", "run_completed", {"status": "completed"})
            await bus.publish("run_1", "step_started", {"step_index": 99})  # never yielded

        asyncio.create_task(publisher())
        events = [e async for e in bus.subscribe("run_1")]
        assert [e[0] for e in events] == ["step_started", "run_completed"]

    async def test_publish_with_no_subscriber_is_dropped_and_leaks_nothing(self):
        """The leak fix: a run that publishes to completion with nobody
        watching must leave no queue behind."""
        bus = EventBus()
        await bus.publish("run_1", "step_started", {"step_index": 0})
        await bus.publish("run_1", "run_completed", {})
        assert not bus.has_queue("run_1")
        assert bus.queue_count() == 0

    async def test_queue_is_released_when_the_subscriber_goes_away_early(self):
        bus = EventBus()
        stream = bus.subscribe("run_1")

        async def publisher():
            await asyncio.sleep(0.01)
            await bus.publish("run_1", "step_started", {"step_index": 0})

        asyncio.create_task(publisher())
        async for _ in stream:
            break
        await stream.aclose()
        assert bus.queue_count() == 0
```

- **Delete** `test_queue_is_released_after_a_terminal_event` (it asserted the old buffering: `has_queue` True after an unsubscribed terminal publish — the exact behavior we're removing). It's replaced by `test_publish_with_no_subscriber_is_dropped_and_leaks_nothing`.
- Keep `test_different_run_ids_are_isolated` but make it concurrent (publish inside a task after subscribe attaches), same pattern as above.

---

## Item 2 — `start_run` must not synchronously block

### Problem
`engine.py:87` `start_run()` creates the run rows and then `await self._advance(...)`, which runs **every teammate step (each a live LLM call)** until the first gate or completion. `POST /v1/playbooks/{id}/runs` therefore does not return until the whole run finishes/pauses — an HTTP request that hangs for the full run duration.

### Fix
Execute the run in a tracked background task; `start_run` returns `run_id` immediately. Add a `wait(run_id)` helper (for tests and graceful shutdown) and a background-exception backstop so a crash marks the run failed and terminates the SSE stream. Apply the same treatment to `resume_with_decision`'s continuation (identical blocking on the gate endpoint).

```diff
--- a/agent_gateway/orchestrator/engine.py
+++ b/agent_gateway/orchestrator/engine.py
@@
-from dataclasses import dataclass
+import asyncio
+from dataclasses import dataclass, field
 from datetime import datetime, timezone
 from typing import Protocol
@@
 @dataclass
 class PlaybookRunner:
     store: OrchestratorStore
     events: EventBus
     tier_models: dict[str, str]
     blackboard_store: SqliteStore
     complete: CompletionFn
+    # In-flight background executions, keyed by run_id. Not a constructor arg.
+    _tasks: dict[str, "asyncio.Task"] = field(default_factory=dict, init=False, repr=False)
+
+    def _spawn(self, run_id: str, coro) -> None:
+        task = asyncio.create_task(coro)
+        self._tasks[run_id] = task
+        task.add_done_callback(
+            lambda t: self._tasks.pop(run_id, None) if self._tasks.get(run_id) is t else None
+        )
+
+    async def wait(self, run_id: str) -> None:
+        """Await the in-flight execution for a run. Test/shutdown helper; a
+        no-op if nothing is running."""
+        task = self._tasks.get(run_id)
+        if task is not None:
+            await task
+
+    async def _execute(self, playbook: Playbook, run_id: str) -> None:
+        """Background driver. Per-step failures are already handled in
+        _run_teammate_step; this is the backstop for anything else so a
+        background crash marks the run failed and terminates the SSE stream
+        instead of vanishing."""
+        try:
+            await self._advance(playbook, run_id)
+        except Exception as exc:  # noqa: BLE001 - last-resort backstop
+            self.store.update_run_status(run_id, "failed")
+            await self.events.publish(run_id, "run_failed",
+                                      {"step_index": self.store.get_run(run_id)["current_step_index"],
+                                       "error": str(exc)})
 
     async def start_run(self, playbook: Playbook, input_text: str) -> str:
         run_id = new_id("run")
         self.store.create_run(id=run_id, playbook_id=playbook.id, input_text=input_text,
                                workspace_id=playbook.workspace_id)
         for index, step in enumerate(playbook.steps):
             self.store.create_step_record(id=new_id("rec"), run_id=run_id,
                                            step_index=index, step_id=step.step_id)
 
         blackboard = Blackboard(self.blackboard_store, run_id)
         blackboard.goal = f"Run playbook: {playbook.name}"
 
-        await self._advance(playbook, run_id)
+        # Do NOT await the run here: it would hold the POST /runs request open
+        # for the entire run (every teammate step is a live LLM call). Execute
+        # in the background and return immediately; clients watch via SSE or
+        # by polling the run snapshot.
+        self._spawn(run_id, self._execute(playbook, run_id))
         return run_id
@@
         self.store.record_gate_decision(id=new_id("gd"), run_id=run_id, step_index=index,
                                          decision=decision, edited_output_json=edited_output)
 
         if decision == "reject":
             self.store.update_step_record(record["id"], status="rejected", completed_at=_now())
             self.store.update_run_status(run_id, "rejected")
+            # Terminate any live SSE stream for this run (see Item 3).
+            await self.events.publish(run_id, "run_rejected", {"step_index": index})
             return
 
         output_json = edited_output if decision == "edit" else None
         self.store.update_step_record(record["id"], status="completed",
                                        output_json=output_json, completed_at=_now())
 
         # Propagate edited output to the preceding step's record so downstream prompts see it
         if decision == "edit" and edited_output is not None:
             prev_record = self._previous_step_record(run_id, index)
             if prev_record is not None:
                 self.store.update_step_record(prev_record["id"], output_json=edited_output)
 
-        await self._complete_step_and_continue(playbook, run_id, index)
+        # Same non-blocking rationale as start_run: don't hold the gate POST
+        # open while the rest of the run executes.
+        self._spawn(run_id, self._execute_continuation(playbook, run_id, index))
+
+    async def _execute_continuation(self, playbook: Playbook, run_id: str, index: int) -> None:
+        try:
+            await self._complete_step_and_continue(playbook, run_id, index)
+        except Exception as exc:  # noqa: BLE001
+            self.store.update_run_status(run_id, "failed")
+            await self.events.publish(run_id, "run_failed",
+                                      {"step_index": index, "error": str(exc)})
```

> `recover_interrupted_runs` is intentionally left awaiting `_advance` — it's already backgrounded at the app level (`server.py:89` `asyncio.create_task(...)`), and `test_startup_does_not_block_on_interrupted_run_recovery` already covers non-blocking startup.

### Contract change & test updates
`POST /runs` now returns `status: "running"` immediately. Update tests that assumed synchronous completion:

**`tests/test_orchestrator_engine.py`** — add `await runner.wait(run_id)` after each `start_run`, and again after each `resume_with_decision` that expects the run to advance:

```python
# TestPlaybookRunnerLinearRun.test_start_run_executes_all_teammate_steps_to_completion
run_id = await runner.start_run(playbook, "New lead: Acme Corp")
await runner.wait(run_id)                         # <-- add
run = store.get_run(run_id)
assert run["status"] == "completed"

# TestPlaybookRunnerErrorHandling.test_step_exception_marks_run_and_step_failed
run_id = await runner.start_run(playbook, "New lead")
await runner.wait(run_id)                         # <-- add

# TestPlaybookRunnerGate.* and TestGateDecisionGuards.* — after start_run:
run_id = await runner.start_run(playbook, "New lead")
await runner.wait(run_id)                         # reaches the gate pause
# ...and after a resume that should advance/complete:
await runner.resume_with_decision(playbook, run_id, "approve", 1)
await runner.wait(run_id)                          # <-- add before asserting "completed"
```

Validation errors (`InvalidDecisionError`, `RunStateError`) still raise **synchronously** inside `resume_with_decision` (before `_spawn`), so `test_unknown_decision_raises...`, `test_second_decision...`, `test_decision_on_a_running_non_gate_step_raises`, and `test_step_index_mismatch...` only need the `wait()` after the initial `start_run`, not after the raising call.

**`tests/test_orchestrator_routes.py`** — replace synchronous assertions on the `POST /runs` response with a poll helper:

```python
async def _wait_for_status(client, run_id, target, tries=50):
    for _ in range(tries):
        snap = (await client.get(f"/v1/playbooks/runs/{run_id}")).json()
        if snap["status"] == target:
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached {target}; last={snap['status']}")
```

- `test_start_run_and_get_snapshot`: after POST, `snap = await _wait_for_status(client, run_id, "completed")`.
- `test_unknown_decision_is_rejected...`, `test_wrong_step_index...`, `TestGateStepIndexMismatch`: replace `assert run_resp.json()["status"] == "paused"` with `await _wait_for_status(client, run_id, "paused")` (and re-read `current_step_index` from that snapshot). After a valid `approve` that advances to the next gate, `await _wait_for_status(client, run_id, "paused")` again; after the final approve, `"completed"`.
- `TestGateDoubleSubmit` / `TestGateDecision`: `await _wait_for_status(..., "paused")` before submitting the decision; the double-submit 409 assertion is unchanged (validation is synchronous).

Add `import asyncio` to the test module.

---

## Item 3 — SSE endpoint termination on completion / gate pause

### Problem
Two gaps:
1. With the live-only bus (Item 1), a subscriber that connects **after** the run already completed/paused will `await queue.get()` forever — no terminal event will ever be published to it. The stream never closes.
2. A **rejected** run publishes no terminal event today, so even a live subscriber hangs on reject (Item 2's diff adds `run_rejected`; this item consumes it).

### Fix
On connect, read the run snapshot. If the run is already in a terminal/paused state, emit **one synthetic frame reconstructed from the snapshot** and close. Otherwise subscribe live (the bus already returns after a terminal event, so live streams still close correctly).

```diff
--- a/agent_gateway/orchestrator/routes.py
+++ b/agent_gateway/orchestrator/routes.py
@@
 from agent_gateway.orchestrator.compiler import CompilerError, compile_canvas
 from agent_gateway.orchestrator.engine import InvalidDecisionError, RunStateError
 from agent_gateway.orchestrator.schema import Playbook
 from agent_gateway.orchestrator.store import new_id
+
+
+def _last_completed_output(records: list[dict], before_index: int) -> dict:
+    for record in reversed(records):
+        if record["step_index"] < before_index and record["status"] == "completed":
+            return record["output_json"] or {"text": ""}
+    return {"text": ""}
+
+
+def _terminal_frame_from_snapshot(gw, run: dict) -> tuple[str, dict] | None:
+    """If the run already reached a terminal/paused state, return the SSE
+    (event_type, data) a live subscriber would have received. None means the
+    run is still running -> subscribe live."""
+    status = run["status"]
+    run_id, idx = run["id"], run["current_step_index"]
+    if status == "completed":
+        return "run_completed", {}
+    if status == "rejected":
+        return "run_rejected", {"step_index": idx}
+    if status == "failed":
+        err = ""
+        for r in gw.orchestrator_store.list_step_records(run_id):
+            if r["status"] == "failed" and r["output_json"]:
+                err = r["output_json"].get("error", "")
+                break
+        return "run_failed", {"step_index": idx, "error": err}
+    if status == "paused":
+        row = gw.orchestrator_store.get_playbook(run["playbook_id"])
+        if row is not None:
+            pb = Playbook.model_validate(row["definition_json"])
+            if 0 <= idx < len(pb.steps) and pb.steps[idx].type == "approval_gate":
+                gate = pb.steps[idx].gate
+                records = gw.orchestrator_store.list_step_records(run_id)
+                return "gate_paused", {
+                    "step_index": idx, "step_id": pb.steps[idx].step_id,
+                    "label": gate.label, "allow_edit": gate.allow_edit,
+                    "proposed_output": _last_completed_output(records, idx),
+                }
+        return "gate_paused", {"step_index": idx}
+    return None
@@
     @router.get("/runs/{run_id}/events")
     async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
         gw = request.app.state.gateway
 
+        def _frame(event_type: str, data: dict) -> bytes:
+            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
+
         async def event_stream():
-            async for event_type, data in gw.event_bus.subscribe(run_id):
-                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
+            run = gw.orchestrator_store.get_run(run_id)
+            if run is None:
+                return  # unknown run -> empty stream, closes immediately
+            terminal = _terminal_frame_from_snapshot(gw, run)
+            if terminal is not None:
+                # Already finished/paused before this subscriber connected: the
+                # live bus will never deliver a terminal event, so serve one
+                # synthetic frame from the snapshot and close.
+                yield _frame(*terminal)
+                return
+            async for event_type, data in gw.event_bus.subscribe(run_id):
+                yield _frame(event_type, data)
 
         return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### Residual race (documented, low-risk)
There is a millisecond window between the snapshot read and `subscribe()` attaching where a transition (e.g. run pauses) could publish to a not-yet-created queue and be dropped, leaving the live branch waiting. With Item 2, clients connect while the run is `running` and the real events flow; the snapshot branch is for reconnects/late viewers, where the run is already settled and the race does not apply. If you want it fully closed, add explicit `attach(run_id) -> Queue` / `detach()` methods to the bus, attach first, then re-read the snapshot before draining — noted as an optional refinement, not required for the tests below.

### Tests (`tests/test_orchestrator_routes.py`)
`test_sse_stream_closes_after_terminal_event` keeps working (run is paused before subscribe → snapshot emits `gate_paused` → stream closes) but needs `await gw.playbook_runner.wait(run_id)` after `start_run` (Item 2). Add coverage for the late-completed and reject cases:

```python
class TestSSETerminationSnapshot:
    async def test_stream_of_already_completed_run_emits_run_completed_and_closes(self):
        app = _app(); gw = app.state.gateway
        from agent_gateway.orchestrator.compiler import compile_canvas
        canvas = {  # start -> t1 -> end (no gate: runs to completion)
            "nodes": [{"id": "start", "type": "start", "data": {}},
                      {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                      {"id": "end", "type": "end", "data": {}}],
            "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}]}
        pb = compile_canvas(canvas, playbook_id="pb_done", name="Done")
        gw.orchestrator_store.upsert_playbook(id=pb.id, name=pb.name, description="",
            schema_version=1, definition_json=pb.model_dump(), canvas_json=canvas)
        run_id = await gw.playbook_runner.start_run(pb, "hi")
        await gw.playbook_runner.wait(run_id)                 # run finished, no subscriber
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/v1/playbooks/runs/{run_id}/events") as resp:
                body = b"".join([c async for c in resp.aiter_bytes()])
        assert b"run_completed" in body                       # completed, and stream closed

    async def test_stream_of_rejected_run_emits_run_rejected_and_closes(self):
        # start a gated run via the fake gateway, reject it, then stream:
        # expect b"run_rejected" in body and the stream to close.
        ...
```

---

## Item 4 — Canvas "Add Teammate" state persistence

### Problem
`frontend/app/playbooks/[id]/page.tsx`: `addTeammate` pushes nodes into local React state (`extraNodes`) only. The code itself documents this (lines 28–31): there is **no endpoint to save a teammate onto an existing playbook**, so added teammates live in the tab, `Run` is disabled, and a warning is shown. Reload loses them; runs ignore them. The drawer even collects `tier`/`knowledge`/`connected_apps` that `addTeammate` throws away.

### Fix
Persist to the backend. Add `PUT /v1/playbooks/{id}` (recompile canvas + upsert under the same id), a pure canvas-mutation helper (unit-testable), an API client method, and make `addTeammate` save + refetch. Then the tab-local warning and disabled `Run` go away.

**Backend — `agent_gateway/orchestrator/routes.py`** (reuses `CreatePlaybookRequest`):

```diff
@@ def build_router() -> APIRouter:
     @router.get("/{playbook_id}")
     async def get_playbook(playbook_id: str, request: Request) -> dict:
         ...
 
+    @router.put("/{playbook_id}")
+    async def update_playbook(playbook_id: str, body: CreatePlaybookRequest, request: Request):
+        gw = request.app.state.gateway
+        if gw.orchestrator_store.get_playbook(playbook_id) is None:
+            raise HTTPException(status_code=404, detail="Playbook not found")
+        try:
+            playbook = compile_canvas(
+                body.canvas_json, playbook_id=playbook_id,
+                name=body.name, description=body.description,
+            )
+        except CompilerError as exc:
+            return JSONResponse(status_code=422, content={"errors": exc.errors})
+        gw.orchestrator_store.upsert_playbook(
+            id=playbook.id, name=playbook.name, description=playbook.description,
+            schema_version=playbook.schema_version, definition_json=playbook.model_dump(),
+            canvas_json=body.canvas_json,
+        )
+        return gw.orchestrator_store.get_playbook(playbook.id)
```

> `upsert_playbook`'s `ON CONFLICT(id) DO UPDATE` preserves `created_at` (it's not in the update set) and refreshes `updated_at`. Templates stay templates only if you also pass `is_template`; the editor path deliberately doesn't, matching create.

**Frontend — new `frontend/lib/canvas/addTeammateToCanvas.ts`** (pure, deterministic id injectable for tests):

```ts
import type { CanvasNode, CanvasEdge } from "@/types/api";
import type { TeammateDrawerConfig } from "@/components/drawer/TeammateDrawer";

type Canvas = { nodes: CanvasNode[]; edges: CanvasEdge[] };

/** Insert a teammate immediately before the end node, preserving the single
 *  linear chain: whoever pointed at `end` now points at the new teammate, and
 *  the new teammate points at `end`. */
export function addTeammateToCanvas(
  canvas: Canvas, config: TeammateDrawerConfig,
  newId: string = `teammate_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
): Canvas {
  const end = canvas.nodes.find((n) => n.type === "end");
  if (!end) throw new Error("Canvas has no end node");
  const node: CanvasNode = {
    id: newId, type: "teammate",
    data: {
      role: config.role, objective: config.objective, tier: config.tier,
      knowledge: config.knowledge, connected_apps: config.connected_apps,
    },
  };
  const edges = canvas.edges.map((e) => (e.target === end.id ? { ...e, target: newId } : e));
  edges.push({ source: newId, target: end.id });
  return { nodes: [...canvas.nodes, node], edges };
}
```

**Frontend — `frontend/lib/api/client.ts`**:

```diff
   createPlaybook: (input: { name: string; description?: string; canvas_json: unknown }) =>
     request<PlaybookDetail>("/v1/playbooks", { method: "POST", body: JSON.stringify(input) }),
+
+  updatePlaybook: (id: string, input: { name: string; description?: string; canvas_json: unknown }) =>
+    request<PlaybookDetail>(`/v1/playbooks/${id}`, { method: "PUT", body: JSON.stringify(input) }),
```

**Frontend — `frontend/app/playbooks/[id]/page.tsx`** (persist + refetch; drop tab-local state and the warning/disable):

```diff
-  const [extraNodes, setExtraNodes] = useState<Node[]>([]);
   const [drawerOpen, setDrawerOpen] = useState(false);
   ...
   const { nodes, edges } = toFlowGraph(playbook.canvas_json);
-  const allNodes = [...nodes, ...extraNodes];
-  // There's no endpoint to save a teammate onto an existing playbook yet, so
-  // anything added here lives in this tab only ...
-  const hasUnsavedTeammates = extraNodes.length > 0;
 
   const runNow = async () => {
     const run = await api.startRun(playbook.id, inputText);
     router.push(`/playbooks/${playbook.id}/run/${run.id}`);
   };
 
-  const addTeammate = (config: TeammateDrawerConfig) => {
-    setExtraNodes((prev) => [ ...prev, { id: `teammate_...`, type: "teammate",
-      data: { role: config.role, objective: config.objective }, position: {...} } ]);
-    setDrawerOpen(false);
-  };
+  const addTeammate = async (config: TeammateDrawerConfig) => {
+    const canvas_json = addTeammateToCanvas(playbook.canvas_json, config);
+    const updated = await api.updatePlaybook(playbook.id, {
+      name: playbook.name, description: playbook.description, canvas_json,
+    });
+    setPlaybook(updated);   // now persisted: survives reload, included in runs
+    setDrawerOpen(false);
+  };
 
   return (
     <main>
       <h1>{playbook.name}</h1>
-      <PlaybookCanvas nodes={allNodes} edges={edges} />
+      <PlaybookCanvas nodes={nodes} edges={edges} />
       <button onClick={() => setDrawerOpen(true)}>Add Teammate</button>
-      {hasUnsavedTeammates && (<p role="status"> ...not saved yet... </p>)}
       {drawerOpen && <TeammateDrawer onSave={addTeammate} />}
       <input aria-label="Run input" value={inputText} onChange={(e) => setInputText(e.target.value)} />
-      <button onClick={runNow} disabled={hasUnsavedTeammates} title={...}>Run</button>
+      <button onClick={runNow}>Run</button>
     </main>
   );
```

Add `import { addTeammateToCanvas } from "@/lib/canvas/addTeammateToCanvas";` and drop the now-unused `Node`/`NODE_SPACING_X` imports.

### Tests
**New `frontend/lib/canvas/addTeammateToCanvas.test.ts`** (vitest):

```ts
import { describe, it, expect } from "vitest";
import { addTeammateToCanvas } from "./addTeammateToCanvas";

const base = {
  nodes: [{ id: "start", type: "start", data: {} },
          { id: "t1", type: "teammate", data: { role: "Q", objective: "O", tier: "speed" } },
          { id: "end", type: "end", data: {} }],
  edges: [{ source: "start", target: "t1" }, { source: "t1", target: "end" }],
};
const config = { role: "Router", objective: "Route", tier: "speed" as const, knowledge: [], connected_apps: [] };

describe("addTeammateToCanvas", () => {
  it("inserts before end and rewires the chain (t1 -> new -> end)", () => {
    const out = addTeammateToCanvas(base, config, "teammate_new");
    expect(out.nodes.map((n) => n.id)).toContain("teammate_new");
    expect(out.edges).toContainEqual({ source: "t1", target: "teammate_new" });
    expect(out.edges).toContainEqual({ source: "teammate_new", target: "end" });
    expect(out.edges.filter((e) => e.target === "end")).toHaveLength(1);
  });
  it("persists tier/knowledge/apps the drawer collected", () => {
    const out = addTeammateToCanvas(base, { ...config, knowledge: ["kb"], connected_apps: ["slack"] }, "n");
    const node = out.nodes.find((n) => n.id === "n")!;
    expect(node.data).toMatchObject({ tier: "speed", knowledge: ["kb"], connected_apps: ["slack"] });
  });
  it("throws if there is no end node", () => {
    expect(() => addTeammateToCanvas({ nodes: [], edges: [] }, config)).toThrow(/end node/);
  });
});
```

**Rewrite `frontend/app/playbooks/[id]/page.test.tsx`** — the "unsaved teammates" tests no longer apply. Mock `api.updatePlaybook`, assert save-and-refetch on add:

```tsx
vi.mock("@/lib/api/client", () => ({
  api: { getPlaybook: vi.fn(), startRun: vi.fn(), updatePlaybook: vi.fn() },
}));
// base playbook must include start + end so addTeammateToCanvas can rewire.
it("persists an added teammate via updatePlaybook and re-renders the returned canvas", async () => {
  vi.mocked(api.getPlaybook).mockResolvedValue(playbookWithStartEnd);
  vi.mocked(api.updatePlaybook).mockImplementation(async (_id, input) => ({
    ...playbookWithStartEnd, canvas_json: input.canvas_json as any,
  }));
  render(<PlaybookEditorPage />);
  await screen.findByText(playbookWithStartEnd.name);
  await addATeammate();
  expect(api.updatePlaybook).toHaveBeenCalledTimes(1);
  const saved = vi.mocked(api.updatePlaybook).mock.calls[0][1].canvas_json as any;
  expect(saved.nodes.some((n: any) => n.type === "teammate" && n.data.role)).toBe(true);
});
it("Run is enabled and calls startRun", async () => { /* no longer disabled */ });
```

**New backend test in `tests/test_orchestrator_routes.py`** — `PUT` round-trips and 404s:

```python
class TestUpdatePlaybook:
    async def test_put_adds_a_teammate_and_persists(self):
        async with _client() as client:
            canvas = {"nodes": [{"id":"start","type":"start","data":{}},
                                {"id":"t1","type":"teammate","data":{"role":"R","objective":"O","tier":"speed"}},
                                {"id":"end","type":"end","data":{}}],
                      "edges": [{"source":"start","target":"t1"},{"source":"t1","target":"end"}]}
            pid = (await client.post("/v1/playbooks", json={"name":"P","canvas_json":canvas})).json()["id"]
            canvas["nodes"].insert(2, {"id":"t2","type":"teammate","data":{"role":"R2","objective":"O2","tier":"speed"}})
            canvas["edges"] = [{"source":"start","target":"t1"},{"source":"t1","target":"t2"},{"source":"t2","target":"end"}]
            resp = await client.put(f"/v1/playbooks/{pid}", json={"name":"P","canvas_json":canvas})
            assert resp.status_code == 200
            assert len(resp.json()["definition_json"]["steps"]) == 2
            got = await client.get(f"/v1/playbooks/{pid}")
            assert len(got.json()["definition_json"]["steps"]) == 2  # survived, not tab-local

    async def test_put_unknown_playbook_returns_404(self):
        async with _client() as client:
            resp = await client.put("/v1/playbooks/pb_missing", json={"name":"x","canvas_json":{"nodes":[],"edges":[]}})
            assert resp.status_code == 404
```

Also add `updatePlaybook` to the hand-written `frontend/types/api.ts` client surface if you type the `api` object (optional; the client is structurally typed today).

---

## Item 5 — Security hardening

### 5a. Parameterized SQLite queries (defensive; not a live vuln)
Everything is already parameterized. The only dynamic SQL is the `UPDATE run_step_records` field list. Harden it so a future edit can't turn it into interpolation:

```diff
--- a/agent_gateway/orchestrator/store.py
+++ b/agent_gateway/orchestrator/store.py
@@ def update_step_record(self, id: str, *, status=None, output_json=None, started_at=None, completed_at=None):
-        fields, values = [], []
-        if status is not None:
-            fields.append("status = ?"); values.append(status)
-        if output_json is not None:
-            fields.append("output_json = ?"); values.append(json.dumps(output_json))
-        if started_at is not None:
-            fields.append("started_at = ?"); values.append(started_at)
-        if completed_at is not None:
-            fields.append("completed_at = ?"); values.append(completed_at)
-        if not fields:
-            return
-        values.append(id)
-        with self.store.cursor() as cur:
-            cur.execute(f"UPDATE run_step_records SET {', '.join(fields)} WHERE id = ?", values)
+        # Column names come only from this fixed allowlist — never from input —
+        # so the generated "col = ?" fragments are safe and values stay bound.
+        _ALLOWED = ("status", "output_json", "started_at", "completed_at")
+        supplied = {"status": status, "output_json": output_json,
+                    "started_at": started_at, "completed_at": completed_at}
+        assignments, values = [], []
+        for col in _ALLOWED:                 # deterministic order, allowlisted keys only
+            val = supplied[col]
+            if val is None:
+                continue
+            assignments.append(f"{col} = ?")
+            values.append(json.dumps(val) if col == "output_json" else val)
+        if not assignments:
+            return
+        values.append(id)
+        with self.store.cursor() as cur:
+            cur.execute(f"UPDATE run_step_records SET {', '.join(assignments)} WHERE id = ?", values)
```

**Test (`tests/test_orchestrator_store.py`)** — a table-name-y / injection-y payload in a *value* is stored literally, not executed:

```python
def test_update_step_record_treats_values_as_data_not_sql():
    store = OrchestratorStore(SqliteStore(":memory:"))
    store.create_run(id="r", playbook_id="p", input_text="x")
    store.create_step_record(id="rec", run_id="r", step_index=0, step_id="s")
    nasty = {"text": "'; DROP TABLE run_step_records; --"}
    store.update_step_record("rec", status="completed", output_json=nasty)
    rec = store.get_step_record("r", 0)
    assert rec["output_json"] == nasty          # stored verbatim
    assert rec["status"] == "completed"         # table still exists / query ran
```

### 5b. Explicit CORS (already `localhost:3000`) — tighten + make configurable
Today's config is correct but wildcards methods/headers and hardcodes the origin. Make origins config-driven (default `["http://localhost:3000"]`) and list methods/headers explicitly.

```diff
--- a/agent_gateway/proxy/config.py
+++ b/agent_gateway/proxy/config.py
@@   # in the server/config section that holds host/port/etc.
+    # CORS: browser origins allowed to call the API. Default is the Next.js
+    # dev server; override for real deployments. "*" is intentionally not the
+    # default — the API resolves per-request credentials from headers.
+    cors_allow_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
```

```diff
--- a/agent_gateway/proxy/server.py
+++ b/agent_gateway/proxy/server.py
@@
-    app.add_middleware(
-        CORSMiddleware,
-        allow_origins=["http://localhost:3000"],
-        allow_methods=["*"],
-        allow_headers=["*"],
-    )
+    app.add_middleware(
+        CORSMiddleware,
+        allow_origins=config.cors_allow_origins,
+        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
+        allow_headers=["Content-Type", "Authorization", "x-api-key", "x-conversation-id"],
+        # allow_credentials stays False: the browser sends API keys as explicit
+        # headers, not cookies, so cookie-credentialed CORS is not needed and
+        # would forbid a wildcard origin anyway.
+    )
```

> Note the `PUT` method — required by Item 4's new endpoint. The existing `test_cors_allows_the_frontend_dev_origin` / `test_cors_preflight_is_answered` still pass. Add one test asserting a non-allowlisted origin gets no `access-control-allow-origin`, and that a custom `cors_allow_origins` is honored.

```python
def test_cors_rejects_unlisted_origin():
    app = create_app(GatewayConfig())
    with TestClient(app) as client:
        r = client.get("/v1/playbooks", headers={"Origin": "http://evil.example"})
        assert r.headers.get("access-control-allow-origin") != "http://evil.example"
```

### 5c. Credential scrubbing from logs (preventive)
There is **no logging in the app today**, so nothing currently leaks — this adds a redaction layer so future logging (or uvicorn/root logs) can't emit bearer tokens / API keys. Two pieces: a logging filter installed on the root + uvicorn loggers, and a `scrub_headers()` helper to use at any log site.

**New `agent_gateway/proxy/logging_config.py`:**

```python
"""Redaction for logs. No credential (Authorization/x-api-key/Bearer tokens)
may ever reach a log sink. Install once at app startup."""
from __future__ import annotations
import logging, re

_PATTERNS = [
    re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)(bearer\s+)?[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(x-api-key\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]+"),
]
_REDACTED = "***REDACTED***"

def _redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub(lambda m: (m.group(1) or "") + _REDACTED, text)
    return text

class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True

_SENSITIVE = {"authorization", "x-api-key", "api-key", "api_key", "cookie"}

def scrub_headers(headers: dict) -> dict:
    """Return a copy safe to log: sensitive header values replaced."""
    return {k: (_REDACTED if k.lower() in _SENSITIVE else v) for k, v in headers.items()}

def install_log_redaction() -> None:
    f = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(f)
```

**Wire it in `create_app` (`server.py`)** and scrub the two spots that could echo secrets — the upstream error passthrough uses `exc.response.text`, which can include upstream auth error detail:

```diff
+from agent_gateway.proxy.logging_config import install_log_redaction, scrub_headers
@@ def create_app(config=None):
     config = config or GatewayConfig()
+    install_log_redaction()
```

> Optional (recommended): stop returning raw upstream bodies to clients — `HTTPException(status_code=exc.response.status_code, detail=exc.response.text)` at `server.py:158/174/199` can surface upstream provider error text. Replace `detail=exc.response.text` with a fixed `detail="upstream provider error"` (and log the real text through the redacting logger if you want it captured). This is a small, separate change; call it out in the PR description.

**Test (new `tests/test_logging_redaction.py`):**

```python
import logging
from agent_gateway.proxy.logging_config import install_log_redaction, scrub_headers, RedactingFilter

def test_scrub_headers_masks_credentials():
    out = scrub_headers({"Authorization": "Bearer sk-abc123", "x-api-key": "k-xyz", "Accept": "application/json"})
    assert out["Authorization"] == "***REDACTED***"
    assert out["x-api-key"] == "***REDACTED***"
    assert out["Accept"] == "application/json"

def test_filter_redacts_bearer_token_in_message(caplog):
    logger = logging.getLogger("test.redact"); logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.redact"):
        logger.info("calling upstream with Authorization: Bearer sk-supersecret-123")
    assert "sk-supersecret-123" not in caplog.text
    assert "***REDACTED***" in caplog.text
```

---

## Verification

### Backend (run on your Mac / a host that can reach the tiktoken CDN)
```bash
cd /Users/macbook/agent-gateway
source .venv/bin/activate                # the macOS venv (python3.14)

# Full suite — expect all green on your Mac (0 failed).
python -m pytest -q

# The files this plan touches:
python -m pytest -q \
  tests/test_orchestrator_events.py \
  tests/test_orchestrator_engine.py \
  tests/test_orchestrator_routes.py \
  tests/test_orchestrator_server_wiring.py \
  tests/test_orchestrator_store.py \
  tests/test_logging_redaction.py

# Async-heavy files verbosely (SSE termination, non-blocking start_run):
python -m pytest -v tests/test_orchestrator_routes.py -k "SSE or Termination or start_run or gate"
```
> If you ever need it green in a sandbox without CDN egress, pre-populate tiktoken's cache: download `cl100k_base.tiktoken` on a networked machine, place it in a dir named by `sha1("https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken")` = `9b5ad71b2ce5302211f9c61530b329a4922fc6a4`, and `export TIKTOKEN_CACHE_DIR=/that/dir`.

### Frontend
```bash
cd /Users/macbook/agent-gateway/frontend
npm test                                  # vitest run
npx vitest run lib/canvas/addTeammateToCanvas.test.ts \
               app/playbooks/[id]/page.test.tsx \
               lib/api/client.test.ts
```

### Manual smoke (SSE termination + persistence), backend running on :8080
```bash
uvicorn agent_gateway.proxy.server:app --port 8080 &

# 1) Create a gated playbook, start a run -> POST returns immediately (running).
PID=$(curl -s localhost:8080/v1/playbooks -H 'content-type: application/json' \
  -d '{"name":"Smoke","canvas_json":{"nodes":[{"id":"start","type":"start","data":{}},{"id":"g1","type":"approval_gate","data":{"label":"Review"}},{"id":"end","type":"end","data":{}}],"edges":[{"source":"start","target":"g1"},{"source":"g1","target":"end"}]}}' | jq -r .id)
RID=$(curl -s localhost:8080/v1/playbooks/$PID/runs -H 'content-type: application/json' -d '{"input":{"text":"hi"}}' | jq -r .id)

# 2) SSE stream must EMIT gate_paused and then CLOSE (curl returns, not hang):
curl -N -s --max-time 5 localhost:8080/v1/playbooks/runs/$RID/events   # expect: event: gate_paused ... then EOF

# 3) PUT persists an added teammate (Item 4):
curl -s -X PUT localhost:8080/v1/playbooks/$PID -H 'content-type: application/json' \
  -d '{"name":"Smoke","canvas_json":{...canvas with an extra teammate before end...}}' | jq '.definition_json.steps | length'

# 4) CORS: allowed vs rejected origin
curl -s -D- -o/dev/null localhost:8080/v1/playbooks -H 'Origin: http://localhost:3000' | grep -i access-control-allow-origin   # present
curl -s -D- -o/dev/null localhost:8080/v1/playbooks -H 'Origin: http://evil.example'   | grep -i access-control-allow-origin   # absent
```

### Static / type checks
```bash
cd frontend && npx tsc --noEmit && npx eslint .
```

---

## Suggested landing order (one PR, or a stack)
1. **Items 1 + 3 + 2 together** (`events.py`, `routes.py`, `engine.py` + their test updates) — they share the live-only/snapshot/non-blocking contract; splitting them leaves the suite red in between.
2. **Item 4** (`routes.py` PUT + frontend) — depends on nothing above but adds the `PUT` that Item 5b's CORS method list should include.
3. **Item 5** (store allowlist, CORS tighten, logging redaction) — independent, low-risk; land last so the CORS `PUT` matches Item 4.

## Risk notes
- The **API contract change** (POST `/runs` returns `running`, not the final state) is the highest-touch part: it's correct behavior for long runs but any other client/test asserting synchronous completion must move to polling/SSE. Grep for `"status"] == "completed"` / `== "paused"` right after a run POST before merging.
- The SSE snapshot path has a documented **millisecond race** (Item 3); acceptable with Item 2, closable with an explicit bus `attach()`/`detach()` if you want zero exposure.
- `run_rejected` is a **new SSE event type** — add it to `frontend/types/api.ts`'s `RunEvent` union and handle it in `useRunEvents` so the run page reacts to rejection over SSE, not just on reload.
```
