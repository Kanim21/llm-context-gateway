# Visual Playbook Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a low-code, drag-and-drop "Playbook" builder — a workplace-metaphor UI (Playbooks, Teammate Cards, Roles, Connected Apps, Approval Gates) backed by an in-process orchestrator that compiles a canvas into a linear chain of LLM/human-approval steps and executes it through the existing multi-provider Agent Gateway, with live SSE-driven visual execution on the canvas.

**Architecture:** A new `agent_gateway/orchestrator/` package owns everything backend: a Pydantic schema for the compiled playbook, a SQLite-backed store (reusing `SqliteStore`'s connection pattern), a pure `compile_canvas()` function that turns React-Flow JSON into a validated `Playbook` with plain-language errors, an `asyncio.Queue`-based `EventBus` for per-run SSE, and a `PlaybookRunner` engine that walks steps one at a time, calling out to the existing `ProviderRegistry`/adapters via a `GatewayCompletionFn`, and persisting state so a crashed run resumes cleanly. A FastAPI router (`orchestrator/routes.py`) is mounted into the existing `proxy/server.py` app. The frontend is a new standalone Next.js app (`frontend/`) using `@xyflow/react` for the canvas and a small typed API/SSE client talking to the backend's 7 endpoints.

**Tech Stack:** Backend: Python 3.10+, FastAPI, Pydantic v2, SQLite (stdlib `sqlite3` via existing `SqliteStore`), `asyncio.Queue` for pub/sub, pytest + **pytest-asyncio** (new dev dependency, needed for this sub-project's async engine/route tests — Sub-project 1's suite is entirely synchronous). Frontend: Next.js (App Router) + TypeScript, `@xyflow/react` (React Flow), Tailwind CSS, Zustand, Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-14-visual-playbook-builder-design.md`

## Setup Prerequisite

Sub-project 1 (multi-provider LLM routing — `ProviderRegistry`, `resolve_credential`, `GeminiAdapter`, the `providers`/`playbook_tiers`-adjacent `GatewayConfig` fields) is **not yet merged** into `main` — it is PR #1 (`feature/multi-provider-routing` → `main`), open at the time this plan was written. This plan's engine (Task 11) and server wiring (Task 14) directly import `ProviderRegistry`, `resolve_credential`, `GeminiAdapter`, and `OpenAIAdapter.stream_chat_completions`, none of which exist on `main` yet.

**The implementation worktree/branch for this plan (suggested name `feature/visual-playbook-builder`) MUST be created from `feature/multi-provider-routing`, not `main`.** Once PR #1 merges, rebase this branch onto `main` before opening its own PR. Do not re-implement or duplicate any Sub-project 1 code — it must be inherited via the branch ancestry.

## Global Constraints

The following apply to every task in this plan; each task's own requirements are in addition to these.

1. **Cache prefix immutability** (CLAUDE.md #1) — the orchestrator's assembled prompts flow through the same `/v1/chat/completions`-adjacent dispatch path conceptually, but `GatewayCompletionFn` (Task 11) calls adapters directly, **bypassing** `BoundaryGuard` entirely (each playbook step is a fresh, single-shot completion call, not a multi-turn cached conversation) — this is intentional and must not be worked around by fabricating a conversation id to route through the boundary guard.
2. **Strict metric separation** (CLAUDE.md #2) — do not add any blended efficiency score. The orchestrator does not touch `proxy/metrics.py` in this plan.
3. **Real tokenization only** (CLAUDE.md #3) — the orchestrator does not do its own token counting in this plan; it has no token-budget logic. If a future task adds one, it must use `TokenizerEngine`, never `char_count` as a token proxy.
4. **Lossy passes default to False** (CLAUDE.md #4) — not touched by this plan; no new lossy transform is introduced.
5. **The `.zclaw` codec never reaches the model** (CLAUDE.md #5) — not touched by this plan; `OrchestratorStore` stores plain JSON text, never `.zclaw`-encoded text, and definitely never feeds `.zclaw`-encoded text into a prompt.
6. **Workplace-metaphor mental model** — no developer jargon anywhere in UI copy, error copy returned by the compiler, or template descriptions:

   | Developer term | UI/product term |
   |---|---|
   | DAG / graph | Playbook |
   | Node | Teammate Card / Step |
   | Agent / LLM call | Teammate |
   | System prompt / persona | Role |
   | Tool config / embeddings | Connected Apps / Knowledge |
   | Human-in-the-loop checkpoint | Approval Gate |
   | Model tier | Speed / Balanced / Brain |

7. **Single-user, local-first, no auth for v1** — no login, no per-user scoping beyond the literal string `workspace_id="default"`. Do not add auth middleware or multi-tenant scoping.
8. **Linear-chain only — no branching.** A playbook is a straight sequence of steps. The compiler (Task 4) rejects any node with zero or more than one outgoing edge (except `end`), and rejects cycles.
9. **Blackboard Assembly** — every teammate step's prompt is built by `assemble_prompt()` (Task 11) using exactly this Markdown structure:

   ```
   ## Intake
   {run's original input text}

   ## Previous Teammates
   ### {role of prior completed teammate step 1}
   {that step's output text}

   ### {role of prior completed teammate step 2}
   {that step's output text}

   ## Current Assignment
   **Role:** {this step's role}
   **Objective:** {this step's objective}
   **Knowledge:** {bullet list, one per knowledge item}
   **Connected Apps:** {bullet list, one per connected app, framed as available context/tools}
   ```

   The `## Previous Teammates` section is omitted entirely (not emitted with an empty body) when there are no prior completed teammate steps. The `**Knowledge:**` and `**Connected Apps:**` lines are omitted entirely when their lists are empty.
10. **Crash recovery** — on startup, any run with `status == "running"` whose step record at `current_step_index` has `status == "running"` must have that step record reset to `status == "pending"` **before** the run is resumed. A run already `paused` (awaiting a gate decision) needs no reset — `awaiting_approval` is already the correct state for that step.
11. **SSE termination** — the SSE route's event generator (Task 13) must stop iterating and let the HTTP response close **immediately** after emitting `run_completed`, `run_failed`, or `gate_paused` — no lingering open connection after a terminal event.
12. **Idempotent seed** — the 3 launch templates (Task 12) are (re-)installed via `INSERT ... ON CONFLICT(id) DO UPDATE` every server startup, so the seed is safe to run repeatedly and always reflects the current template files on disk.

---

## Task 1: `SqliteStore.executescript()`

**Files:**
- Modify: `agent_gateway/storage/sqlite_store.py`
- Test: `tests/test_orchestrator_store.py` (new file)

**Interfaces:**
- Consumes: `SqliteStore.__init__(path: str | Path = ":memory:")`, `SqliteStore._connect()`, `SqliteStore._lock` (existing, unmodified).
- Produces: `SqliteStore.executescript(self, sql: str) -> None` — runs an arbitrary multi-statement SQL script under the same lock/connection convention as every other `SqliteStore` method, then commits.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_store.py`:

```python
"""Tests for the orchestrator's SQLite persistence layer."""

from __future__ import annotations

from agent_gateway.storage.sqlite_store import SqliteStore


class TestSqliteStoreExecutescript:
    def test_executescript_creates_table_visible_to_later_queries(self):
        store = SqliteStore(":memory:")
        store.executescript("CREATE TABLE IF NOT EXISTS widgets (id TEXT PRIMARY KEY)")

        with store.cursor() as cur:
            cur.execute("INSERT INTO widgets (id) VALUES ('w1')")

        with store.cursor() as cur:
            cur.execute("SELECT id FROM widgets")
            rows = cur.fetchall()
        assert [r[0] for r in rows] == ["w1"]
        store.close()

    def test_executescript_is_safe_to_call_twice(self):
        store = SqliteStore(":memory:")
        script = "CREATE TABLE IF NOT EXISTS widgets (id TEXT PRIMARY KEY)"
        store.executescript(script)
        store.executescript(script)  # must not raise
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_store.py -v`
Expected: FAIL — `AttributeError: 'SqliteStore' object has no attribute 'executescript'`

- [ ] **Step 3: Write minimal implementation**

In `agent_gateway/storage/sqlite_store.py`, add this method to the `SqliteStore` class (near `close`, using the same `self._lock` / `self._connect()` convention every other method already uses):

```python
    def executescript(self, sql: str) -> None:
        """Run a multi-statement SQL script (e.g. a CREATE TABLE block owned
        by another module) under the store's own lock/connection, leaving
        the existing SCHEMA/_init_schema() untouched."""
        with self._lock:
            conn = self._connect()
            conn.executescript(sql)
            conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_store.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/storage/sqlite_store.py tests/test_orchestrator_store.py
git commit -m "feat: add SqliteStore.executescript() for orchestrator schema install"
```

---

## Task 2: Orchestrator Pydantic schema

**Files:**
- Create: `agent_gateway/orchestrator/__init__.py` (empty)
- Create: `agent_gateway/orchestrator/schema.py`
- Test: `tests/test_orchestrator_schema.py` (new file)

**Interfaces:**
- Produces: `TeammateConfig(role: str, objective: str, tier: Literal["speed","balanced","brain"], connected_apps: list[str] = [], knowledge: list[str] = [])`, `GateConfig(label: str, allow_edit: bool = True)`, `PlaybookStep(step_id: str, type: Literal["teammate","approval_gate"], teammate: TeammateConfig | None = None, gate: GateConfig | None = None)`, `Playbook(schema_version: int = 1, id: str, workspace_id: str = "default", name: str, description: str = "", steps: list[PlaybookStep])`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_schema.py`:

```python
"""Tests for the orchestrator's canonical Pydantic schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_gateway.orchestrator.schema import (
    GateConfig,
    Playbook,
    PlaybookStep,
    TeammateConfig,
)


class TestTeammateConfig:
    def test_defaults(self):
        teammate = TeammateConfig(role="Sales Qualifier", objective="Qualify inbound leads", tier="balanced")
        assert teammate.connected_apps == []
        assert teammate.knowledge == []

    def test_invalid_tier_rejected(self):
        with pytest.raises(ValidationError):
            TeammateConfig(role="X", objective="Y", tier="ultra")


class TestGateConfig:
    def test_allow_edit_defaults_true(self):
        assert GateConfig(label="Review before sending").allow_edit is True


class TestPlaybookStep:
    def test_teammate_step_requires_teammate_config(self):
        with pytest.raises(ValidationError):
            PlaybookStep(step_id="s1", type="teammate", teammate=None)

    def test_approval_gate_step_requires_gate_config(self):
        with pytest.raises(ValidationError):
            PlaybookStep(step_id="s1", type="approval_gate", gate=None)

    def test_valid_teammate_step(self):
        step = PlaybookStep(
            step_id="s1", type="teammate",
            teammate=TeammateConfig(role="R", objective="O", tier="speed"),
        )
        assert step.teammate.role == "R"

    def test_valid_gate_step(self):
        step = PlaybookStep(step_id="s2", type="approval_gate", gate=GateConfig(label="Review"))
        assert step.gate.label == "Review"


class TestPlaybook:
    def test_defaults(self):
        playbook = Playbook(id="pb_1", name="My Playbook", steps=[])
        assert playbook.schema_version == 1
        assert playbook.workspace_id == "default"
        assert playbook.description == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/orchestrator/__init__.py` (empty file).

Create `agent_gateway/orchestrator/schema.py`:

```python
"""Canonical compiled-playbook schema. `compiler.py` produces a `Playbook`
from raw canvas JSON; `engine.py` consumes it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TeammateConfig(BaseModel):
    role: str
    objective: str
    tier: Literal["speed", "balanced", "brain"]
    connected_apps: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)


class GateConfig(BaseModel):
    label: str
    allow_edit: bool = True


class PlaybookStep(BaseModel):
    step_id: str
    type: Literal["teammate", "approval_gate"]
    teammate: TeammateConfig | None = None
    gate: GateConfig | None = None

    @model_validator(mode="after")
    def _check_config_matches_type(self) -> "PlaybookStep":
        if self.type == "teammate" and self.teammate is None:
            raise ValueError("teammate step requires teammate config")
        if self.type == "approval_gate" and self.gate is None:
            raise ValueError("approval_gate step requires gate config")
        return self


class Playbook(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str = "default"
    name: str
    description: str = ""
    steps: list[PlaybookStep]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_schema.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/__init__.py agent_gateway/orchestrator/schema.py tests/test_orchestrator_schema.py
git commit -m "feat: add orchestrator Pydantic schema (TeammateConfig/GateConfig/PlaybookStep/Playbook)"
```

---

## Task 3: `OrchestratorStore`

**Files:**
- Create: `agent_gateway/orchestrator/store.py`
- Test: `tests/test_orchestrator_store.py` (extend)

**Interfaces:**
- Consumes: `SqliteStore.executescript` (Task 1), `SqliteStore.cursor()` (existing).
- Produces: `OrchestratorStore(store: SqliteStore)` with methods: `upsert_playbook(*, id, name, description, schema_version, definition_json: dict, canvas_json: dict, is_template: bool = False, workspace_id: str = "default") -> None`, `get_playbook(id: str) -> dict | None`, `list_playbooks(workspace_id: str = "default") -> list[dict]`, `create_run(*, id, playbook_id, input_text: str, workspace_id: str = "default") -> None`, `get_run(run_id: str) -> dict | None`, `update_run_status(run_id: str, status: str, current_step_index: int | None = None) -> None`, `list_runs_by_status(status: str) -> list[dict]`, `create_step_record(*, id, run_id, step_index: int, step_id: str, status: str = "pending") -> None`, `get_step_record(run_id: str, step_index: int) -> dict | None`, `list_step_records(run_id: str) -> list[dict]`, `update_step_record(id: str, *, status=None, output_json: dict | None = None, started_at=None, completed_at=None) -> None`, `reset_step_to_pending(run_id: str, step_index: int) -> None`, `record_gate_decision(*, id, run_id, step_index: int, decision: str, edited_output_json: dict | None = None) -> None`. All `*_json` parameters/return fields are Python `dict`s at this boundary — JSON encode/decode happens entirely inside this module.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_store.py`:

```python
from agent_gateway.orchestrator.store import OrchestratorStore


class TestOrchestratorStorePlaybooks:
    def _store(self) -> OrchestratorStore:
        return OrchestratorStore(SqliteStore(":memory:"))

    def test_upsert_and_get_playbook_round_trips_json_fields(self):
        store = self._store()
        store.upsert_playbook(
            id="pb_1", name="Test", description="desc", schema_version=1,
            definition_json={"steps": []}, canvas_json={"nodes": [], "edges": []},
        )
        playbook = store.get_playbook("pb_1")
        assert playbook["name"] == "Test"
        assert playbook["definition_json"] == {"steps": []}
        assert playbook["canvas_json"] == {"nodes": [], "edges": []}
        assert playbook["is_template"] is False

    def test_get_playbook_missing_returns_none(self):
        assert self._store().get_playbook("nope") is None

    def test_upsert_playbook_is_idempotent_and_updates_content(self):
        store = self._store()
        store.upsert_playbook(id="pb_1", name="V1", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        store.upsert_playbook(id="pb_1", name="V2", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        playbooks = store.list_playbooks()
        assert len(playbooks) == 1
        assert playbooks[0]["name"] == "V2"

    def test_list_playbooks_filters_by_workspace(self):
        store = self._store()
        store.upsert_playbook(id="pb_1", name="A", description="", schema_version=1,
                               definition_json={}, canvas_json={}, workspace_id="ws1")
        store.upsert_playbook(id="pb_2", name="B", description="", schema_version=1,
                               definition_json={}, canvas_json={}, workspace_id="ws2")
        assert [p["id"] for p in store.list_playbooks(workspace_id="ws1")] == ["pb_1"]


class TestOrchestratorStoreRuns:
    def _store_with_playbook(self) -> OrchestratorStore:
        store = OrchestratorStore(SqliteStore(":memory:"))
        store.upsert_playbook(id="pb_1", name="A", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        return store

    def test_create_and_get_run_round_trips_input_json(self):
        store = self._store_with_playbook()
        store.create_run(id="run_1", playbook_id="pb_1", input_text="Hello world")
        run = store.get_run("run_1")
        assert run["playbook_id"] == "pb_1"
        assert run["status"] == "running"
        assert run["current_step_index"] == 0
        assert run["input_json"] == {"text": "Hello world"}

    def test_update_run_status_and_index(self):
        store = self._store_with_playbook()
        store.create_run(id="run_1", playbook_id="pb_1", input_text="hi")
        store.update_run_status("run_1", "paused", current_step_index=2)
        run = store.get_run("run_1")
        assert run["status"] == "paused"
        assert run["current_step_index"] == 2

    def test_list_runs_by_status(self):
        store = self._store_with_playbook()
        store.create_run(id="run_1", playbook_id="pb_1", input_text="hi")
        store.create_run(id="run_2", playbook_id="pb_1", input_text="hi")
        store.update_run_status("run_2", "completed")
        assert [r["id"] for r in store.list_runs_by_status("running")] == ["run_1"]


class TestOrchestratorStoreStepRecords:
    def _store_with_run(self) -> OrchestratorStore:
        store = OrchestratorStore(SqliteStore(":memory:"))
        store.upsert_playbook(id="pb_1", name="A", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        store.create_run(id="run_1", playbook_id="pb_1", input_text="hi")
        return store

    def test_create_and_get_step_record(self):
        store = self._store_with_run()
        store.create_step_record(id="rec_1", run_id="run_1", step_index=0, step_id="s1")
        record = store.get_step_record("run_1", 0)
        assert record["status"] == "pending"
        assert record["output_json"] is None

    def test_update_step_record_sets_only_given_fields(self):
        store = self._store_with_run()
        store.create_step_record(id="rec_1", run_id="run_1", step_index=0, step_id="s1")
        store.update_step_record("rec_1", status="running", started_at="2026-09-14T00:00:00+00:00")
        record = store.get_step_record("run_1", 0)
        assert record["status"] == "running"
        assert record["started_at"] == "2026-09-14T00:00:00+00:00"
        assert record["completed_at"] is None

        store.update_step_record("rec_1", status="completed", output_json={"text": "done"},
                                  completed_at="2026-09-14T00:01:00+00:00")
        record = store.get_step_record("run_1", 0)
        assert record["status"] == "completed"
        assert record["output_json"] == {"text": "done"}

    def test_list_step_records_ordered_by_index(self):
        store = self._store_with_run()
        store.create_step_record(id="rec_2", run_id="run_1", step_index=1, step_id="s2")
        store.create_step_record(id="rec_1", run_id="run_1", step_index=0, step_id="s1")
        records = store.list_step_records("run_1")
        assert [r["step_index"] for r in records] == [0, 1]

    def test_reset_step_to_pending(self):
        store = self._store_with_run()
        store.create_step_record(id="rec_1", run_id="run_1", step_index=0, step_id="s1")
        store.update_step_record("rec_1", status="running", started_at="2026-09-14T00:00:00+00:00")
        store.reset_step_to_pending("run_1", 0)
        record = store.get_step_record("run_1", 0)
        assert record["status"] == "pending"


class TestOrchestratorStoreGateDecisions:
    def test_record_gate_decision(self):
        store = OrchestratorStore(SqliteStore(":memory:"))
        store.upsert_playbook(id="pb_1", name="A", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        store.create_run(id="run_1", playbook_id="pb_1", input_text="hi")
        store.record_gate_decision(id="gd_1", run_id="run_1", step_index=1, decision="approve")
        # No dedicated getter is needed by the engine; this just proves the insert succeeds
        # and is queryable via the store's own cursor.
        with store.store.cursor() as cur:
            cur.execute("SELECT decision FROM run_gate_decisions WHERE id = ?", ("gd_1",))
            row = cur.fetchone()
        assert row[0] == "approve"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator.store'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/orchestrator/store.py`:

```python
"""SQLite persistence for playbooks, runs, step records, and gate
decisions. Installs its own schema into the shared SqliteStore connection
via SqliteStore.executescript(), leaving the pre-existing SCHEMA untouched."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from agent_gateway.storage.sqlite_store import SqliteStore

ORCHESTRATOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS playbooks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    canvas_json TEXT NOT NULL,
    is_template INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playbook_runs (
    id TEXT PRIMARY KEY,
    playbook_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    input_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_step_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    output_json TEXT,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS run_gate_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    decision TEXT NOT NULL,
    edited_output_json TEXT,
    decided_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrchestratorStore:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store
        self.store.executescript(ORCHESTRATOR_SCHEMA)

    # -- playbooks --------------------------------------------------

    def upsert_playbook(
        self, *, id: str, name: str, description: str, schema_version: int,
        definition_json: dict, canvas_json: dict, is_template: bool = False,
        workspace_id: str = "default",
    ) -> None:
        now = _now()
        with self.store.cursor() as cur:
            cur.execute(
                """
                INSERT INTO playbooks
                    (id, workspace_id, name, description, schema_version,
                     definition_json, canvas_json, is_template, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id=excluded.workspace_id,
                    name=excluded.name,
                    description=excluded.description,
                    schema_version=excluded.schema_version,
                    definition_json=excluded.definition_json,
                    canvas_json=excluded.canvas_json,
                    is_template=excluded.is_template,
                    updated_at=excluded.updated_at
                """,
                (id, workspace_id, name, description, schema_version,
                 json.dumps(definition_json), json.dumps(canvas_json),
                 int(is_template), now, now),
            )

    def get_playbook(self, id: str) -> dict | None:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, workspace_id, name, description, schema_version,
                          definition_json, canvas_json, is_template, created_at, updated_at
                   FROM playbooks WHERE id = ?""",
                (id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._playbook_row_to_dict(row)

    def list_playbooks(self, workspace_id: str = "default") -> list[dict]:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, workspace_id, name, description, schema_version,
                          definition_json, canvas_json, is_template, created_at, updated_at
                   FROM playbooks WHERE workspace_id = ? ORDER BY created_at""",
                (workspace_id,),
            )
            rows = cur.fetchall()
        return [self._playbook_row_to_dict(row) for row in rows]

    @staticmethod
    def _playbook_row_to_dict(row) -> dict:
        return {
            "id": row[0], "workspace_id": row[1], "name": row[2], "description": row[3],
            "schema_version": row[4], "definition_json": json.loads(row[5]),
            "canvas_json": json.loads(row[6]), "is_template": bool(row[7]),
            "created_at": row[8], "updated_at": row[9],
        }

    # -- runs ---------------------------------------------------------

    def create_run(self, *, id: str, playbook_id: str, input_text: str,
                    workspace_id: str = "default") -> None:
        now = _now()
        with self.store.cursor() as cur:
            cur.execute(
                """INSERT INTO playbook_runs
                       (id, playbook_id, workspace_id, status, current_step_index,
                        input_json, created_at, updated_at)
                   VALUES (?, ?, ?, 'running', 0, ?, ?, ?)""",
                (id, playbook_id, workspace_id, json.dumps({"text": input_text}), now, now),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, playbook_id, workspace_id, status, current_step_index,
                          input_json, created_at, updated_at
                   FROM playbook_runs WHERE id = ?""",
                (run_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "playbook_id": row[1], "workspace_id": row[2], "status": row[3],
            "current_step_index": row[4], "input_json": json.loads(row[5]),
            "created_at": row[6], "updated_at": row[7],
        }

    def update_run_status(self, run_id: str, status: str,
                           current_step_index: int | None = None) -> None:
        with self.store.cursor() as cur:
            if current_step_index is None:
                cur.execute(
                    "UPDATE playbook_runs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), run_id),
                )
            else:
                cur.execute(
                    "UPDATE playbook_runs SET status = ?, current_step_index = ?, updated_at = ? WHERE id = ?",
                    (status, current_step_index, _now(), run_id),
                )

    def list_runs_by_status(self, status: str) -> list[dict]:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, playbook_id, workspace_id, status, current_step_index,
                          input_json, created_at, updated_at
                   FROM playbook_runs WHERE status = ?""",
                (status,),
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "playbook_id": r[1], "workspace_id": r[2], "status": r[3],
             "current_step_index": r[4], "input_json": json.loads(r[5]),
             "created_at": r[6], "updated_at": r[7]}
            for r in rows
        ]

    # -- step records ---------------------------------------------------

    def create_step_record(self, *, id: str, run_id: str, step_index: int,
                            step_id: str, status: str = "pending") -> None:
        with self.store.cursor() as cur:
            cur.execute(
                """INSERT INTO run_step_records
                       (id, run_id, step_index, step_id, status, output_json, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)""",
                (id, run_id, step_index, step_id, status),
            )

    def get_step_record(self, run_id: str, step_index: int) -> dict | None:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, run_id, step_index, step_id, status, output_json, started_at, completed_at
                   FROM run_step_records WHERE run_id = ? AND step_index = ?""",
                (run_id, step_index),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._step_row_to_dict(row)

    def list_step_records(self, run_id: str) -> list[dict]:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, run_id, step_index, step_id, status, output_json, started_at, completed_at
                   FROM run_step_records WHERE run_id = ? ORDER BY step_index""",
                (run_id,),
            )
            rows = cur.fetchall()
        return [self._step_row_to_dict(row) for row in rows]

    @staticmethod
    def _step_row_to_dict(row) -> dict:
        return {
            "id": row[0], "run_id": row[1], "step_index": row[2], "step_id": row[3],
            "status": row[4], "output_json": json.loads(row[5]) if row[5] else None,
            "started_at": row[6], "completed_at": row[7],
        }

    def update_step_record(self, id: str, *, status: str | None = None,
                            output_json: dict | None = None,
                            started_at: str | None = None,
                            completed_at: str | None = None) -> None:
        fields, values = [], []
        if status is not None:
            fields.append("status = ?"); values.append(status)
        if output_json is not None:
            fields.append("output_json = ?"); values.append(json.dumps(output_json))
        if started_at is not None:
            fields.append("started_at = ?"); values.append(started_at)
        if completed_at is not None:
            fields.append("completed_at = ?"); values.append(completed_at)
        if not fields:
            return
        values.append(id)
        with self.store.cursor() as cur:
            cur.execute(f"UPDATE run_step_records SET {', '.join(fields)} WHERE id = ?", values)

    def reset_step_to_pending(self, run_id: str, step_index: int) -> None:
        with self.store.cursor() as cur:
            cur.execute(
                """UPDATE run_step_records SET status = 'pending', started_at = NULL, completed_at = NULL
                   WHERE run_id = ? AND step_index = ?""",
                (run_id, step_index),
            )

    # -- gate decisions ---------------------------------------------------

    def record_gate_decision(self, *, id: str, run_id: str, step_index: int,
                              decision: str, edited_output_json: dict | None = None) -> None:
        with self.store.cursor() as cur:
            cur.execute(
                """INSERT INTO run_gate_decisions
                       (id, run_id, step_index, decision, edited_output_json, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (id, run_id, step_index, decision,
                 json.dumps(edited_output_json) if edited_output_json is not None else None,
                 _now()),
            )


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_store.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/store.py tests/test_orchestrator_store.py
git commit -m "feat: add OrchestratorStore for playbooks/runs/step records/gate decisions"
```

---

## Task 4: Canvas compiler (`compile_canvas`)

**Files:**
- Create: `agent_gateway/orchestrator/compiler.py`
- Test: `tests/test_orchestrator_compiler.py` (new file)

**Interfaces:**
- Consumes: `TeammateConfig`, `GateConfig`, `PlaybookStep`, `Playbook` (Task 2).
- Produces: `CompilerError(Exception)` with `.errors: list[dict[str, str]]` (each `{"node_id": str, "message": str}`), `compile_canvas(canvas_json: dict, *, playbook_id: str, name: str, description: str = "", workspace_id: str = "default") -> Playbook`.
- Canvas JSON shape consumed: `{"nodes": [{"id": str, "type": "start"|"end"|"teammate"|"approval_gate", "data": {...}}], "edges": [{"source": str, "target": str}]}`. For a `teammate` node, `data` holds `role, objective, tier, connected_apps, knowledge`. For an `approval_gate` node, `data` holds `label, allow_edit`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_compiler.py`:

```python
"""Tests for compile_canvas()'s 6 validation rules and the happy path."""

from __future__ import annotations

import pytest

from agent_gateway.orchestrator.compiler import CompilerError, compile_canvas


def _node(id, type, **data):
    return {"id": id, "type": type, "data": data}


def _linear_canvas():
    return {
        "nodes": [
            _node("start", "start"),
            _node("t1", "teammate", role="Qualifier", objective="Qualify the lead", tier="balanced"),
            _node("g1", "approval_gate", label="Review before routing"),
            _node("end", "end"),
        ],
        "edges": [
            {"source": "start", "target": "t1"},
            {"source": "t1", "target": "g1"},
            {"source": "g1", "target": "end"},
        ],
    }


class TestHappyPath:
    def test_linear_canvas_compiles_to_playbook_with_two_steps(self):
        playbook = compile_canvas(_linear_canvas(), playbook_id="pb_1", name="Test")
        assert playbook.id == "pb_1"
        assert [s.type for s in playbook.steps] == ["teammate", "approval_gate"]
        assert playbook.steps[0].teammate.role == "Qualifier"
        assert playbook.steps[1].gate.label == "Review before routing"


class TestRuleExactlyOneStart:
    def test_zero_start_nodes_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"] = [n for n in canvas["nodes"] if n["type"] != "start"]
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        assert "A playbook needs exactly one starting point." in [e["message"] for e in exc_info.value.errors]

    def test_two_start_nodes_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("start2", "start"))
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        assert "A playbook needs exactly one starting point." in [e["message"] for e in exc_info.value.errors]


class TestRuleOneOutgoingEdge:
    def test_dead_end_step_rejected(self):
        canvas = _linear_canvas()
        canvas["edges"] = [e for e in canvas["edges"] if e != {"source": "t1", "target": "g1"}]
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This step doesn't lead anywhere — connect it to the next step or mark it as the end." in messages

    def test_branching_step_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("t2", "teammate", role="R", objective="O", tier="speed"))
        canvas["edges"].append({"source": "t1", "target": "t2"})
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This step has two next steps — Playbooks run one step at a time." in messages


class TestRuleNoCycles:
    def test_cycle_rejected(self):
        canvas = _linear_canvas()
        canvas["edges"].append({"source": "g1", "target": "t1"})
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This playbook loops back on itself — playbooks run start to finish, once." in messages


class TestRuleNoOrphans:
    def test_disconnected_node_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("orphan", "teammate", role="R", objective="O", tier="speed"))
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This step isn't connected to the playbook — attach it to the chain or remove it." in messages


class TestRuleGateLabelRequired:
    def test_empty_gate_label_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "g1":
                n["data"]["label"] = ""
        with pytest.raises(CompilerError):
            compile_canvas(canvas, playbook_id="pb_1", name="Test")


class TestRuleTeammateRoleObjectiveRequired:
    def test_empty_role_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "t1":
                n["data"]["role"] = ""
        with pytest.raises(CompilerError):
            compile_canvas(canvas, playbook_id="pb_1", name="Test")

    def test_empty_objective_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "t1":
                n["data"]["objective"] = ""
        with pytest.raises(CompilerError):
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator.compiler'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/orchestrator/compiler.py`:

```python
"""Compiles React-Flow canvas JSON into a validated Playbook. Enforces the
6 linear-chain rules with plain-language (workplace-metaphor) error copy —
no developer jargon reaches the UI."""

from __future__ import annotations

from agent_gateway.orchestrator.schema import GateConfig, Playbook, PlaybookStep, TeammateConfig


class CompilerError(Exception):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("; ".join(e["message"] for e in errors))


def compile_canvas(
    canvas_json: dict, *, playbook_id: str, name: str, description: str = "",
    workspace_id: str = "default",
) -> Playbook:
    nodes = {n["id"]: n for n in canvas_json.get("nodes", [])}
    edges = canvas_json.get("edges", [])

    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        outgoing.setdefault(edge["source"], []).append(edge["target"])

    errors: list[dict[str, str]] = []

    start_nodes = [n for n in nodes.values() if n["type"] == "start"]
    if len(start_nodes) != 1:
        errors.append({"node_id": "", "message": "A playbook needs exactly one starting point."})

    for node_id, node in nodes.items():
        if node["type"] == "end":
            continue
        out_count = len(outgoing.get(node_id, []))
        if out_count == 0:
            errors.append({
                "node_id": node_id,
                "message": "This step doesn't lead anywhere — connect it to the next step or mark it as the end.",
            })
        elif out_count > 1:
            errors.append({
                "node_id": node_id,
                "message": "This step has two next steps — Playbooks run one step at a time.",
            })

    seen: set[str] = set()
    visited_order: list[str] = []
    has_cycle = False
    if start_nodes:
        current = start_nodes[0]["id"]
        while current is not None and current in nodes:
            if current in seen:
                has_cycle = True
                break
            seen.add(current)
            visited_order.append(current)
            next_ids = outgoing.get(current, [])
            current = next_ids[0] if next_ids else None
        if has_cycle:
            errors.append({
                "node_id": "",
                "message": "This playbook loops back on itself — playbooks run start to finish, once.",
            })

    orphans = set(nodes) - seen
    for node_id in orphans:
        errors.append({
            "node_id": node_id,
            "message": "This step isn't connected to the playbook — attach it to the chain or remove it.",
        })

    for node_id in visited_order:
        node = nodes[node_id]
        if node["type"] == "approval_gate":
            if not node["data"].get("label", "").strip():
                errors.append({"node_id": node_id, "message": "This approval gate needs a label."})
        elif node["type"] == "teammate":
            if not node["data"].get("role", "").strip():
                errors.append({"node_id": node_id, "message": "This teammate needs a role."})
            if not node["data"].get("objective", "").strip():
                errors.append({"node_id": node_id, "message": "This teammate needs an objective."})

    if errors:
        raise CompilerError(errors)

    steps: list[PlaybookStep] = []
    for node_id in visited_order:
        node = nodes[node_id]
        if node["type"] == "teammate":
            data = node["data"]
            steps.append(PlaybookStep(
                step_id=node_id, type="teammate",
                teammate=TeammateConfig(
                    role=data["role"], objective=data["objective"], tier=data["tier"],
                    connected_apps=data.get("connected_apps", []),
                    knowledge=data.get("knowledge", []),
                ),
            ))
        elif node["type"] == "approval_gate":
            data = node["data"]
            steps.append(PlaybookStep(
                step_id=node_id, type="approval_gate",
                gate=GateConfig(label=data["label"], allow_edit=data.get("allow_edit", True)),
            ))
        # start/end nodes produce no PlaybookStep

    return Playbook(id=playbook_id, workspace_id=workspace_id, name=name,
                     description=description, steps=steps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_compiler.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/compiler.py tests/test_orchestrator_compiler.py
git commit -m "feat: add compile_canvas() with the 6 linear-chain validation rules"
```

---

## Task 5: `EventBus`

**Files:**
- Create: `agent_gateway/orchestrator/events.py`
- Test: `tests/test_orchestrator_events.py` (new file)

**Interfaces:**
- Produces: `TERMINAL_EVENTS: set[str]` (`{"run_completed", "run_failed", "gate_paused"}`), `EventBus` (dataclass) with `async def publish(self, run_id: str, event_type: str, data: dict) -> None` and `async def subscribe(self, run_id: str)` — an async generator yielding `(event_type: str, data: dict)` tuples, returning immediately after yielding a terminal event.
- This task requires **pytest-asyncio**. Add it now: it is needed by this and every subsequent async test in this plan.

- [ ] **Step 1: Add pytest-asyncio dev dependency**

In `pyproject.toml`, find the `[project.optional-dependencies]` `dev` list (used by Sub-project 1's `pip install -e ".[dev]"`) and add `"pytest-asyncio"` to it. Then create/update `pytest.ini` (or the `[tool.pytest.ini_options]` table in `pyproject.toml` if that's what the repo already uses — check for an existing one first) to set:

```ini
[pytest]
asyncio_mode = auto
```

Run: `pip install -e ".[dev]"` to install it locally.

- [ ] **Step 2: Write the failing test**

Create `tests/test_orchestrator_events.py`:

```python
"""Tests for the per-run asyncio.Queue-based SSE event bus."""

from __future__ import annotations

import asyncio

import pytest

from agent_gateway.orchestrator.events import EventBus


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
        await bus.publish("run_1", "step_started", {"step_index": 0})
        await bus.publish("run_1", "run_completed", {"status": "completed"})
        await bus.publish("run_1", "step_started", {"step_index": 99})  # must never be yielded

        events = [event async for event in bus.subscribe("run_1")]

        assert [e[0] for e in events] == ["step_started", "run_completed"]

    async def test_different_run_ids_are_isolated(self):
        bus = EventBus()
        await bus.publish("run_a", "run_completed", {})
        events = []
        async for event_type, data in bus.subscribe("run_a"):
            events.append(event_type)
        assert events == ["run_completed"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator.events'`

- [ ] **Step 4: Write minimal implementation**

Create `agent_gateway/orchestrator/events.py`:

```python
"""In-process, per-run pub/sub for SSE. One asyncio.Queue per run_id."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

TERMINAL_EVENTS: set[str] = {"run_completed", "run_failed", "gate_paused"}


@dataclass
class EventBus:
    _queues: dict[str, asyncio.Queue] = field(default_factory=dict)

    def _queue_for(self, run_id: str) -> asyncio.Queue:
        if run_id not in self._queues:
            self._queues[run_id] = asyncio.Queue()
        return self._queues[run_id]

    async def publish(self, run_id: str, event_type: str, data: dict) -> None:
        await self._queue_for(run_id).put((event_type, data))

    async def subscribe(self, run_id: str):
        queue = self._queue_for(run_id)
        while True:
            event_type, data = await queue.get()
            yield event_type, data
            if event_type in TERMINAL_EVENTS:
                return
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_events.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml pytest.ini agent_gateway/orchestrator/events.py tests/test_orchestrator_events.py
git commit -m "feat: add EventBus for per-run SSE pub/sub; add pytest-asyncio"
```

---

## Task 6: `playbook_tiers` config field

**Files:**
- Modify: `agent_gateway/proxy/config.py`
- Test: `tests/test_orchestrator_config.py` (new file)

**Interfaces:**
- Consumes: `GatewayConfig` (existing, from `feature/multi-provider-routing`).
- Produces: `GatewayConfig.playbook_tiers: dict[str, str]`, defaulting to `{"speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_config.py`:

```python
"""Tests for the playbook_tiers config field."""

from __future__ import annotations

import json

from agent_gateway.proxy.config import GatewayConfig


class TestPlaybookTiersConfig:
    def test_default_tiers(self):
        config = GatewayConfig()
        assert config.playbook_tiers == {
            "speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro",
        }

    def test_load_overrides_tiers_from_json(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"playbook_tiers": {"speed": "custom-fast-model"}}))
        config = GatewayConfig.load(str(path))
        assert config.playbook_tiers == {"speed": "custom-fast-model"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_config.py -v`
Expected: FAIL — `AttributeError: 'GatewayConfig' object has no attribute 'playbook_tiers'`

- [ ] **Step 3: Write minimal implementation**

In `agent_gateway/proxy/config.py`, add the field to `GatewayConfig` (alongside `providers: ProvidersConfig = Field(default_factory=ProvidersConfig)`):

```python
    playbook_tiers: dict[str, str] = Field(
        default_factory=lambda: {
            "speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro",
        }
    )
```

No change to `.load()` is needed — `cls.model_validate(data)` already picks up a `"playbook_tiers"` key from the raw JSON dict, or falls back to the default if absent, exactly as it already does for `providers`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/proxy/config.py tests/test_orchestrator_config.py
git commit -m "feat: add playbook_tiers config field (speed/balanced/brain -> model)"
```

---

## Task 7: `assemble_prompt()` (Blackboard Assembly)

**Files:**
- Create: `agent_gateway/orchestrator/engine.py`
- Test: `tests/test_orchestrator_engine.py` (new file)

**Interfaces:**
- Consumes: `OrchestratorStore` (Task 3), `PlaybookStep`/`TeammateConfig` (Task 2).
- Produces: `assemble_prompt(store: OrchestratorStore, *, run: dict, steps: list[PlaybookStep], step_index: int) -> str` — exact Markdown format per Global Constraint #9.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_engine.py`:

```python
"""Tests for the orchestrator engine: prompt assembly, PlaybookRunner
execution, gate decisions, and crash recovery."""

from __future__ import annotations

from agent_gateway.orchestrator.schema import GateConfig, PlaybookStep, TeammateConfig
from agent_gateway.orchestrator.store import OrchestratorStore
from agent_gateway.storage.sqlite_store import SqliteStore


def _make_store() -> OrchestratorStore:
    return OrchestratorStore(SqliteStore(":memory:"))


class TestAssemblePromptIntakeOnly:
    def test_first_step_has_no_previous_teammates_section(self):
        from agent_gateway.orchestrator.engine import assemble_prompt

        store = _make_store()
        store.upsert_playbook(id="pb_1", name="A", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        store.create_run(id="run_1", playbook_id="pb_1", input_text="Help this customer.")
        run = store.get_run("run_1")

        steps = [
            PlaybookStep(step_id="t1", type="teammate",
                         teammate=TeammateConfig(role="Qualifier", objective="Qualify the lead", tier="balanced")),
        ]
        store.create_step_record(id="rec_1", run_id="run_1", step_index=0, step_id="t1")

        prompt = assemble_prompt(store, run=run, steps=steps, step_index=0)

        assert "## Intake\nHelp this customer." in prompt
        assert "## Previous Teammates" not in prompt
        assert "## Current Assignment" in prompt
        assert "**Role:** Qualifier" in prompt
        assert "**Objective:** Qualify the lead" in prompt
        assert "**Knowledge:**" not in prompt
        assert "**Connected Apps:**" not in prompt


class TestAssemblePromptWithPriorTeammates:
    def test_includes_previous_teammate_output_and_optional_fields(self):
        from agent_gateway.orchestrator.engine import assemble_prompt

        store = _make_store()
        store.upsert_playbook(id="pb_1", name="A", description="", schema_version=1,
                               definition_json={}, canvas_json={})
        store.create_run(id="run_1", playbook_id="pb_1", input_text="Draft a campaign.")
        run = store.get_run("run_1")

        steps = [
            PlaybookStep(step_id="t1", type="teammate",
                         teammate=TeammateConfig(role="Strategist", objective="Plan the campaign", tier="balanced")),
            PlaybookStep(step_id="t2", type="teammate",
                         teammate=TeammateConfig(role="Copywriter", objective="Write the post", tier="speed",
                                                  connected_apps=["slack"], knowledge=["brand voice guide"])),
        ]
        store.create_step_record(id="rec_1", run_id="run_1", step_index=0, step_id="t1")
        store.update_step_record("rec_1", status="completed", output_json={"text": "Focus on Q4 launch."})
        store.create_step_record(id="rec_2", run_id="run_1", step_index=1, step_id="t2")

        prompt = assemble_prompt(store, run=run, steps=steps, step_index=1)

        assert "## Previous Teammates\n### Strategist\nFocus on Q4 launch." in prompt
        assert "**Role:** Copywriter" in prompt
        assert "**Knowledge:**\n- brand voice guide" in prompt
        assert "**Connected Apps:**\n- slack" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator.engine'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/orchestrator/engine.py`:

```python
"""PlaybookRunner: executes a compiled Playbook step by step, persisting
state via OrchestratorStore and publishing progress via EventBus."""

from __future__ import annotations

from agent_gateway.orchestrator.schema import PlaybookStep
from agent_gateway.orchestrator.store import OrchestratorStore


def assemble_prompt(
    store: OrchestratorStore, *, run: dict, steps: list[PlaybookStep], step_index: int,
) -> str:
    parts = [f"## Intake\n{run['input_json']['text']}"]

    prior_blocks = []
    for record in store.list_step_records(run["id"]):
        if record["step_index"] >= step_index:
            break
        step = steps[record["step_index"]]
        if step.type != "teammate" or record["status"] != "completed":
            continue
        output_text = (record["output_json"] or {}).get("text", "")
        prior_blocks.append(f"### {step.teammate.role}\n{output_text}")
    if prior_blocks:
        parts.append("## Previous Teammates\n" + "\n\n".join(prior_blocks))

    current = steps[step_index].teammate
    assignment = [f"## Current Assignment\n**Role:** {current.role}",
                  f"**Objective:** {current.objective}"]
    if current.knowledge:
        assignment.append("**Knowledge:**\n" + "\n".join(f"- {k}" for k in current.knowledge))
    if current.connected_apps:
        assignment.append("**Connected Apps:**\n" + "\n".join(f"- {a}" for a in current.connected_apps))
    parts.append("\n".join(assignment))

    return "\n\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/engine.py tests/test_orchestrator_engine.py
git commit -m "feat: add assemble_prompt() implementing the Blackboard Assembly format"
```

---

## Task 8: `PlaybookRunner` — linear execution to completion

**Files:**
- Modify: `agent_gateway/orchestrator/engine.py`
- Test: `tests/test_orchestrator_engine.py` (extend)

**Interfaces:**
- Consumes: `EventBus` (Task 5), `OrchestratorStore` (Task 3), `Playbook`/`PlaybookStep` (Task 2), `Blackboard` (existing, `agent_gateway.core.blackboard`).
- Produces: `CompletionFn` (`typing.Protocol`, `async def __call__(self, *, model: str, prompt: str, on_token) -> str`), `PlaybookRunner` (dataclass: `store: OrchestratorStore`, `events: EventBus`, `tier_models: dict[str, str]`, `blackboard_store` — a `SqliteStore`, `complete: CompletionFn`), `async def start_run(self, playbook: Playbook, input_text: str) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_engine.py`:

```python
from agent_gateway.orchestrator.compiler import compile_canvas
from agent_gateway.orchestrator.events import EventBus


def _linear_playbook():
    canvas = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {"id": "t1", "type": "teammate", "data": {
                "role": "Qualifier", "objective": "Qualify the lead", "tier": "speed"}},
            {"id": "t2", "type": "teammate", "data": {
                "role": "Router", "objective": "Route to a rep", "tier": "speed"}},
            {"id": "end", "type": "end", "data": {}},
        ],
        "edges": [
            {"source": "start", "target": "t1"},
            {"source": "t1", "target": "t2"},
            {"source": "t2", "target": "end"},
        ],
    }
    return compile_canvas(canvas, playbook_id="pb_1", name="Linear")


class FakeCompletionFn:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[dict] = []

    async def __call__(self, *, model, prompt, on_token):
        self.calls.append({"model": model, "prompt": prompt})
        text = self.responses.get(model, "default response")
        await on_token(text)
        return text


class TestPlaybookRunnerLinearRun:
    async def test_start_run_executes_all_teammate_steps_to_completion(self):
        from agent_gateway.orchestrator.engine import PlaybookRunner

        store = _make_store()
        blackboard_store = SqliteStore(":memory:")
        playbook = _linear_playbook()
        store.upsert_playbook(id=playbook.id, name=playbook.name, description="",
                               schema_version=1, definition_json=playbook.model_dump(),
                               canvas_json={})

        fake_complete = FakeCompletionFn({"gpt-4o-mini": "Qualified: yes"})
        runner = PlaybookRunner(
            store=store, events=EventBus(),
            tier_models={"speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro"},
            blackboard_store=blackboard_store, complete=fake_complete,
        )

        run_id = await runner.start_run(playbook, "New lead: Acme Corp")

        run = store.get_run(run_id)
        assert run["status"] == "completed"
        records = store.list_step_records(run_id)
        assert [r["status"] for r in records] == ["completed", "completed"]
        assert records[0]["output_json"]["text"] == "Qualified: yes"
        assert len(fake_complete.calls) == 2
        assert fake_complete.calls[0]["model"] == "gpt-4o-mini"


class TestPlaybookRunnerErrorHandling:
    async def test_step_exception_marks_run_and_step_failed(self):
        from agent_gateway.orchestrator.engine import PlaybookRunner

        store = _make_store()
        blackboard_store = SqliteStore(":memory:")
        playbook = _linear_playbook()
        store.upsert_playbook(id=playbook.id, name=playbook.name, description="",
                               schema_version=1, definition_json=playbook.model_dump(),
                               canvas_json={})

        async def failing_complete(*, model, prompt, on_token):
            raise RuntimeError("upstream exploded")

        runner = PlaybookRunner(
            store=store, events=EventBus(),
            tier_models={"speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro"},
            blackboard_store=blackboard_store, complete=failing_complete,
        )

        run_id = await runner.start_run(playbook, "New lead")

        run = store.get_run(run_id)
        assert run["status"] == "failed"
        records = store.list_step_records(run_id)
        assert records[0]["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'PlaybookRunner'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent_gateway/orchestrator/engine.py` (after `assemble_prompt`):

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from agent_gateway.core.blackboard import Blackboard
from agent_gateway.orchestrator.schema import Playbook
from agent_gateway.orchestrator.store import new_id
from agent_gateway.storage.sqlite_store import SqliteStore
from agent_gateway.orchestrator.events import EventBus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompletionFn(Protocol):
    async def __call__(self, *, model: str, prompt: str, on_token) -> str: ...


@dataclass
class PlaybookRunner:
    store: OrchestratorStore
    events: EventBus
    tier_models: dict[str, str]
    blackboard_store: SqliteStore
    complete: CompletionFn

    async def start_run(self, playbook: Playbook, input_text: str) -> str:
        run_id = new_id("run")
        self.store.create_run(id=run_id, playbook_id=playbook.id, input_text=input_text,
                               workspace_id=playbook.workspace_id)
        for index, step in enumerate(playbook.steps):
            self.store.create_step_record(id=new_id("rec"), run_id=run_id,
                                           step_index=index, step_id=step.step_id)

        blackboard = Blackboard(self.blackboard_store, run_id)
        blackboard.goal = f"Run playbook: {playbook.name}"

        await self._advance(playbook, run_id)
        return run_id

    async def _advance(self, playbook: Playbook, run_id: str) -> None:
        run = self.store.get_run(run_id)
        index = run["current_step_index"]
        step = playbook.steps[index]
        record = self.store.get_step_record(run_id, index)

        if step.type == "approval_gate":
            await self._pause_at_gate(playbook, run_id, index, step, record)
        else:
            await self._run_teammate_step(playbook, run_id, index, step, record)

    async def _run_teammate_step(self, playbook: Playbook, run_id: str, index: int,
                                  step, record: dict) -> None:
        self.store.update_step_record(record["id"], status="running", started_at=_now())
        await self.events.publish(run_id, "step_started", {"step_index": index, "step_id": step.step_id})

        model = self.tier_models[step.teammate.tier]
        run = self.store.get_run(run_id)
        prompt = assemble_prompt(self.store, run=run, steps=playbook.steps, step_index=index)

        async def on_token(token: str) -> None:
            await self.events.publish(run_id, "token", {"step_index": index, "token": token})

        try:
            output_text = await self.complete(model=model, prompt=prompt, on_token=on_token)
        except Exception as exc:
            self.store.update_step_record(record["id"], status="failed", completed_at=_now())
            self.store.update_run_status(run_id, "failed")
            await self.events.publish(run_id, "run_failed", {"step_index": index, "error": str(exc)})
            return

        self.store.update_step_record(record["id"], status="completed",
                                       output_json={"text": output_text}, completed_at=_now())
        blackboard = Blackboard(self.blackboard_store, run_id)
        blackboard.add_milestone(f"{step.teammate.role} completed step {index}")
        await self.events.publish(run_id, "step_completed",
                                   {"step_index": index, "output": {"text": output_text}})

        await self._complete_step_and_continue(playbook, run_id, index)

    async def _complete_step_and_continue(self, playbook: Playbook, run_id: str, index: int) -> None:
        if index + 1 >= len(playbook.steps):
            self.store.update_run_status(run_id, "completed", current_step_index=index + 1)
            await self.events.publish(run_id, "run_completed", {})
            return
        self.store.update_run_status(run_id, "running", current_step_index=index + 1)
        await self._advance(playbook, run_id)

    async def _pause_at_gate(self, playbook: Playbook, run_id: str, index: int,
                              step, record: dict) -> None:
        self.store.update_step_record(record["id"], status="awaiting_approval")
        self.store.update_run_status(run_id, "paused")
        proposed_output = self._last_completed_output(run_id, index)
        await self.events.publish(run_id, "gate_paused", {
            "step_index": index, "step_id": step.step_id, "label": step.gate.label,
            "allow_edit": step.gate.allow_edit, "proposed_output": proposed_output,
        })

    def _last_completed_output(self, run_id: str, gate_index: int) -> dict:
        for record in reversed(self.store.list_step_records(run_id)):
            if record["step_index"] < gate_index and record["status"] == "completed":
                return record["output_json"] or {"text": ""}
        return {"text": ""}
```

Note: `OrchestratorStore` is already imported at module scope from Task 7's `from agent_gateway.orchestrator.store import OrchestratorStore` — the `@dataclass` field type annotation `store: OrchestratorStore` in `PlaybookRunner` reuses that same import; do not add a duplicate.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/engine.py tests/test_orchestrator_engine.py
git commit -m "feat: add PlaybookRunner linear execution loop with Blackboard integration"
```

---

## Task 9: `PlaybookRunner` — gate pause and `resume_with_decision`

**Files:**
- Modify: `agent_gateway/orchestrator/engine.py`
- Test: `tests/test_orchestrator_engine.py` (extend)

**Interfaces:**
- Consumes: everything from Task 8.
- Produces: `async def resume_with_decision(self, playbook: Playbook, run_id: str, decision: str, edited_output: dict | None = None) -> None` on `PlaybookRunner`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_engine.py`:

```python
def _playbook_with_gate():
    canvas = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {"id": "t1", "type": "teammate", "data": {
                "role": "Qualifier", "objective": "Qualify the lead", "tier": "speed"}},
            {"id": "g1", "type": "approval_gate", "data": {"label": "Review before routing"}},
            {"id": "t2", "type": "teammate", "data": {
                "role": "Router", "objective": "Route to a rep", "tier": "speed"}},
            {"id": "end", "type": "end", "data": {}},
        ],
        "edges": [
            {"source": "start", "target": "t1"},
            {"source": "t1", "target": "g1"},
            {"source": "g1", "target": "t2"},
            {"source": "t2", "target": "end"},
        ],
    }
    return compile_canvas(canvas, playbook_id="pb_gate", name="Gated")


class TestPlaybookRunnerGate:
    async def _runner(self):
        store = _make_store()
        playbook = _playbook_with_gate()
        store.upsert_playbook(id=playbook.id, name=playbook.name, description="",
                               schema_version=1, definition_json=playbook.model_dump(), canvas_json={})
        from agent_gateway.orchestrator.engine import PlaybookRunner
        fake_complete = FakeCompletionFn({"gpt-4o-mini": "Qualified: yes"})
        runner = PlaybookRunner(
            store=store, events=EventBus(),
            tier_models={"speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro"},
            blackboard_store=SqliteStore(":memory:"), complete=fake_complete,
        )
        return runner, playbook, store

    async def test_run_pauses_at_gate(self):
        runner, playbook, store = await self._runner()
        run_id = await runner.start_run(playbook, "New lead")
        run = store.get_run(run_id)
        assert run["status"] == "paused"
        gate_record = store.get_step_record(run_id, 1)
        assert gate_record["status"] == "awaiting_approval"

    async def test_approve_resumes_and_completes_run(self):
        runner, playbook, store = await self._runner()
        run_id = await runner.start_run(playbook, "New lead")
        await runner.resume_with_decision(playbook, run_id, "approve")
        run = store.get_run(run_id)
        assert run["status"] == "completed"
        gate_record = store.get_step_record(run_id, 1)
        assert gate_record["status"] == "completed"

    async def test_edit_overrides_output_before_resuming(self):
        runner, playbook, store = await self._runner()
        run_id = await runner.start_run(playbook, "New lead")
        await runner.resume_with_decision(runbook := playbook, run_id, "edit",
                                           edited_output={"text": "Edited qualification note"})
        gate_record = store.get_step_record(run_id, 1)
        assert gate_record["output_json"] == {"text": "Edited qualification note"}
        run = store.get_run(run_id)
        assert run["status"] == "completed"

    async def test_reject_stops_run_without_advancing(self):
        runner, playbook, store = await self._runner()
        run_id = await runner.start_run(playbook, "New lead")
        await runner.resume_with_decision(playbook, run_id, "reject")
        run = store.get_run(run_id)
        assert run["status"] == "rejected"
        gate_record = store.get_step_record(run_id, 1)
        assert gate_record["status"] == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: FAIL — `AttributeError: 'PlaybookRunner' object has no attribute 'resume_with_decision'`

- [ ] **Step 3: Write minimal implementation**

Append this method to the `PlaybookRunner` class in `agent_gateway/orchestrator/engine.py`:

```python
    async def resume_with_decision(self, playbook: Playbook, run_id: str, decision: str,
                                    edited_output: dict | None = None) -> None:
        run = self.store.get_run(run_id)
        index = run["current_step_index"]
        record = self.store.get_step_record(run_id, index)

        self.store.record_gate_decision(id=new_id("gd"), run_id=run_id, step_index=index,
                                         decision=decision, edited_output_json=edited_output)

        if decision == "reject":
            self.store.update_step_record(record["id"], status="rejected", completed_at=_now())
            self.store.update_run_status(run_id, "rejected")
            return

        output_json = edited_output if decision == "edit" else None
        if output_json is not None:
            self.store.update_step_record(record["id"], status="completed",
                                           output_json=output_json, completed_at=_now())
        else:
            self.store.update_step_record(record["id"], status="completed", completed_at=_now())

        await self._complete_step_and_continue(playbook, run_id, index)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/engine.py tests/test_orchestrator_engine.py
git commit -m "feat: add PlaybookRunner.resume_with_decision (approve/edit/reject)"
```

---

## Task 10: Crash recovery (`recover_interrupted_runs`)

**Files:**
- Modify: `agent_gateway/orchestrator/engine.py`
- Test: `tests/test_orchestrator_engine.py` (extend)

**Interfaces:**
- Consumes: everything from Tasks 8–9.
- Produces: `async def recover_interrupted_runs(self, playbooks_by_id: dict[str, Playbook]) -> None` on `PlaybookRunner`, implementing Global Constraint #10.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_engine.py`:

```python
class TestCrashRecovery:
    async def test_interrupted_running_step_is_reset_to_pending_then_completed(self):
        store = _make_store()
        playbook = _linear_playbook()
        store.upsert_playbook(id=playbook.id, name=playbook.name, description="",
                               schema_version=1, definition_json=playbook.model_dump(), canvas_json={})

        # Simulate a crash mid-step: run row says "running" at index 0, but the step
        # record was left "running" (never marked completed/failed) rather than "pending".
        store.create_run(id="run_crash", playbook_id=playbook.id, input_text="New lead")
        store.create_step_record(id="rec_0", run_id="run_crash", step_index=0, step_id="t1")
        store.update_step_record("rec_0", status="running", started_at="2026-09-14T00:00:00+00:00")
        store.create_step_record(id="rec_1", run_id="run_crash", step_index=1, step_id="t2")

        from agent_gateway.orchestrator.engine import PlaybookRunner
        fake_complete = FakeCompletionFn({"gpt-4o-mini": "Qualified: yes"})
        runner = PlaybookRunner(
            store=store, events=EventBus(),
            tier_models={"speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro"},
            blackboard_store=SqliteStore(":memory:"), complete=fake_complete,
        )

        await runner.recover_interrupted_runs({playbook.id: playbook})

        run = store.get_run("run_crash")
        assert run["status"] == "completed"
        records = store.list_step_records("run_crash")
        assert [r["status"] for r in records] == ["completed", "completed"]

    async def test_paused_run_awaiting_approval_is_left_untouched(self):
        store = _make_store()
        playbook = _linear_playbook()
        store.upsert_playbook(id=playbook.id, name=playbook.name, description="",
                               schema_version=1, definition_json=playbook.model_dump(), canvas_json={})
        store.create_run(id="run_paused", playbook_id=playbook.id, input_text="hi")
        store.update_run_status("run_paused", "paused", current_step_index=0)
        store.create_step_record(id="rec_0", run_id="run_paused", step_index=0, step_id="t1")
        store.update_step_record("rec_0", status="awaiting_approval")

        from agent_gateway.orchestrator.engine import PlaybookRunner
        runner = PlaybookRunner(
            store=store, events=EventBus(), tier_models={"speed": "m"},
            blackboard_store=SqliteStore(":memory:"), complete=FakeCompletionFn({}),
        )

        await runner.recover_interrupted_runs({playbook.id: playbook})

        record = store.get_step_record("run_paused", 0)
        assert record["status"] == "awaiting_approval"  # untouched, not reset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: FAIL — `AttributeError: 'PlaybookRunner' object has no attribute 'recover_interrupted_runs'`

- [ ] **Step 3: Write minimal implementation**

Append this method to the `PlaybookRunner` class:

```python
    async def recover_interrupted_runs(self, playbooks_by_id: dict[str, Playbook]) -> None:
        for run in self.store.list_runs_by_status("running"):
            playbook = playbooks_by_id.get(run["playbook_id"])
            if playbook is None:
                continue
            index = run["current_step_index"]
            record = self.store.get_step_record(run["id"], index)
            if record is not None and record["status"] == "running":
                self.store.reset_step_to_pending(run["id"], index)
            await self._advance(playbook, run["id"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_engine.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/engine.py tests/test_orchestrator_engine.py
git commit -m "feat: add PlaybookRunner.recover_interrupted_runs for crash recovery"
```

---

## Task 11: `GatewayCompletionFn` — real LLM dispatch via existing adapters

**Files:**
- Modify: `agent_gateway/orchestrator/engine.py`
- Test: `tests/test_orchestrator_completion_fn.py` (new file)

**Interfaces:**
- Consumes: `ProviderRegistry`, `resolve_credential` (`agent_gateway.core.provider_routing`, from `feature/multi-provider-routing`), `GeminiAdapter` (`agent_gateway.adapters.gemini_adapter`), `OpenAIAdapter` (`agent_gateway.adapters.openai_adapter`) — all inherited via this branch's ancestry from `feature/multi-provider-routing` per the Setup Prerequisite.
- Produces: `_consume_openai_sse(chunks, on_token) -> str` (module-level helper), `GatewayCompletionFn` (dataclass: `provider_registry: ProviderRegistry`, `http_client: httpx.AsyncClient`) implementing the `CompletionFn` protocol.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_completion_fn.py`:

```python
"""Tests for GatewayCompletionFn: dispatches a playbook step's prompt
through the existing multi-provider adapters, using httpx.MockTransport --
no real network calls."""

from __future__ import annotations

import httpx

from agent_gateway.core.provider_routing import ProviderRegistry
from agent_gateway.orchestrator.engine import GatewayCompletionFn
from agent_gateway.proxy.config import ProviderConfig, ProvidersConfig


def _registry():
    return ProviderRegistry(ProvidersConfig(
        entries=[
            ProviderConfig(name="openai", wire_shape="openai_compatible",
                            base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
            ProviderConfig(name="gemini", wire_shape="gemini",
                            base_url="https://generativelanguage.googleapis.com/v1beta",
                            api_key_env="GEMINI_API_KEY"),
        ],
        model_routes={"gemini-1.5-pro": "gemini"},
        default_provider="openai",
    ))


class TestGatewayCompletionFnOpenAI:
    async def test_streams_openai_response_and_accumulates_tokens(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        sse_body = (
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n'
            b'data: [DONE]\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fn = GatewayCompletionFn(provider_registry=_registry(), http_client=http_client)

        tokens = []
        async def on_token(token):
            tokens.append(token)

        result = await fn(model="gpt-4o-mini", prompt="Say hello", on_token=on_token)

        assert result == "Hello world"
        assert tokens == ["Hello", " world"]


class TestGatewayCompletionFnGemini:
    async def test_streams_gemini_response_translated_to_openai_shape(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "sk-test")
        sse_body = (
            'data: {"candidates": [{"content": {"parts": [{"text": "Bonjour"}]}}]}\n\n'
            'data: {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}\n\n'
        ).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fn = GatewayCompletionFn(provider_registry=_registry(), http_client=http_client)

        tokens = []
        async def on_token(token):
            tokens.append(token)

        result = await fn(model="gemini-1.5-pro", prompt="Say hi in French", on_token=on_token)

        assert result == "Bonjour"
        assert tokens == ["Bonjour"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_completion_fn.py -v`
Expected: FAIL — `ImportError: cannot import name 'GatewayCompletionFn'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent_gateway/orchestrator/engine.py`:

```python
import json as _json

import httpx

from agent_gateway.adapters.gemini_adapter import GeminiAdapter
from agent_gateway.adapters.openai_adapter import OpenAIAdapter
from agent_gateway.core.provider_routing import ProviderRegistry, resolve_credential


async def _consume_openai_sse(chunks, on_token) -> str:
    buffer = b""
    full_text = ""
    async for chunk in chunks:
        buffer += chunk
        while b"\n\n" in buffer:
            frame, buffer = buffer.split(b"\n\n", 1)
            for line in frame.split(b"\n"):
                if not line.startswith(b"data: "):
                    continue
                payload = line[len(b"data: "):]
                if payload.strip() == b"[DONE]":
                    return full_text
                data = _json.loads(payload)
                delta = data.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    full_text += delta
                    await on_token(delta)
    return full_text


@dataclass
class GatewayCompletionFn:
    provider_registry: ProviderRegistry
    http_client: httpx.AsyncClient

    async def __call__(self, *, model: str, prompt: str, on_token) -> str:
        provider = self.provider_registry.resolve(model)
        api_key = resolve_credential(provider, {})
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True}

        if provider.wire_shape == "gemini":
            gemini_kwargs = {"base_url": provider.base_url, "api_key": api_key}
            if provider.api_key_header:
                gemini_kwargs["api_key_header"] = provider.api_key_header
            adapter = GeminiAdapter(**gemini_kwargs)
            chunks = await adapter.stream_generate_content(body, client=self.http_client)
        else:
            openai_kwargs = {"base_url": provider.base_url, "api_key": api_key}
            if provider.api_key_header:
                openai_kwargs["api_key_header"] = provider.api_key_header
            adapter = OpenAIAdapter(**openai_kwargs)
            chunks = await adapter.stream_chat_completions(body, client=self.http_client)

        return await _consume_openai_sse(chunks, on_token)
```

Add `import httpx` (if not already present from an earlier task's edits) and confirm `from dataclasses import dataclass` is already imported at the top of the file from Task 8.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_completion_fn.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/engine.py tests/test_orchestrator_completion_fn.py
git commit -m "feat: add GatewayCompletionFn dispatching playbook steps through existing adapters"
```

---

## Task 12: Launch templates + idempotent seed

**Files:**
- Create: `agent_gateway/orchestrator/templates/__init__.py` (empty)
- Create: `agent_gateway/orchestrator/templates/inbound_sales_triage.json`
- Create: `agent_gateway/orchestrator/templates/multi_channel_social_content.json`
- Create: `agent_gateway/orchestrator/templates/support_ticket_escalation.json`
- Create: `agent_gateway/orchestrator/seed.py`
- Test: `tests/test_orchestrator_seed.py` (new file)

**Interfaces:**
- Consumes: `compile_canvas` (Task 4), `OrchestratorStore` (Task 3).
- Produces: `seed_templates(store: OrchestratorStore) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_seed.py`:

```python
"""Tests for the idempotent startup seed of the 3 launch templates."""

from __future__ import annotations

from agent_gateway.orchestrator.store import OrchestratorStore
from agent_gateway.storage.sqlite_store import SqliteStore


class TestSeedTemplates:
    def test_seed_installs_three_templates_flagged_is_template(self):
        from agent_gateway.orchestrator.seed import seed_templates

        store = OrchestratorStore(SqliteStore(":memory:"))
        seed_templates(store)

        playbooks = store.list_playbooks()
        assert len(playbooks) == 3
        assert all(p["is_template"] for p in playbooks)
        ids = {p["id"] for p in playbooks}
        assert ids == {
            "tpl_inbound_sales_triage",
            "tpl_multi_channel_social_content",
            "tpl_support_ticket_escalation",
        }

    def test_seed_is_idempotent_no_duplicates_on_second_run(self):
        from agent_gateway.orchestrator.seed import seed_templates

        store = OrchestratorStore(SqliteStore(":memory:"))
        seed_templates(store)
        seed_templates(store)

        assert len(store.list_playbooks()) == 3

    def test_each_template_compiles_to_a_valid_linear_chain_with_a_gate(self):
        from agent_gateway.orchestrator.seed import seed_templates

        store = OrchestratorStore(SqliteStore(":memory:"))
        seed_templates(store)

        for playbook_row in store.list_playbooks():
            steps = playbook_row["definition_json"]["steps"]
            assert len(steps) >= 2
            assert any(s["type"] == "approval_gate" for s in steps)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator.seed'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/orchestrator/templates/__init__.py` (empty).

Create `agent_gateway/orchestrator/templates/inbound_sales_triage.json`:

```json
{
  "id": "tpl_inbound_sales_triage",
  "name": "Inbound Sales Triage",
  "description": "Qualify a new inbound lead and route it to the right rep, with a review step before handoff.",
  "canvas_json": {
    "nodes": [
      {"id": "start", "type": "start", "data": {}},
      {"id": "t1", "type": "teammate", "data": {
        "role": "Sales Qualifier",
        "objective": "Read the lead's message and determine fit against our ideal customer profile and typical deal size, then summarize qualification status.",
        "tier": "balanced",
        "connected_apps": ["gmail", "hubspot"],
        "knowledge": ["Ideal customer profile", "Typical deal size ranges"]
      }},
      {"id": "g1", "type": "approval_gate", "data": {
        "label": "Review qualification before routing to a rep",
        "allow_edit": true
      }},
      {"id": "t2", "type": "teammate", "data": {
        "role": "Routing Coordinator",
        "objective": "Assign the qualified lead to the correct rep based on territory and current pipeline load, and draft the handoff note.",
        "tier": "speed",
        "connected_apps": ["hubspot"],
        "knowledge": []
      }},
      {"id": "end", "type": "end", "data": {}}
    ],
    "edges": [
      {"source": "start", "target": "t1"},
      {"source": "t1", "target": "g1"},
      {"source": "g1", "target": "t2"},
      {"source": "t2", "target": "end"}
    ]
  }
}
```

Create `agent_gateway/orchestrator/templates/multi_channel_social_content.json`:

```json
{
  "id": "tpl_multi_channel_social_content",
  "name": "Multi-Channel Social Content",
  "description": "Turn a content idea into platform-ready drafts, with a review step before anything goes out.",
  "canvas_json": {
    "nodes": [
      {"id": "start", "type": "start", "data": {}},
      {"id": "t1", "type": "teammate", "data": {
        "role": "Content Strategist",
        "objective": "Turn the intake idea into a short content plan: key message, tone, and one angle per platform.",
        "tier": "balanced",
        "connected_apps": [],
        "knowledge": ["Brand voice guide"]
      }},
      {"id": "t2", "type": "teammate", "data": {
        "role": "Platform Copywriter",
        "objective": "Write a ready-to-post draft for each platform in the plan, matching each platform's typical length and tone.",
        "tier": "speed",
        "connected_apps": ["slack"],
        "knowledge": []
      }},
      {"id": "g1", "type": "approval_gate", "data": {
        "label": "Review drafts before publishing",
        "allow_edit": true
      }},
      {"id": "end", "type": "end", "data": {}}
    ],
    "edges": [
      {"source": "start", "target": "t1"},
      {"source": "t1", "target": "t2"},
      {"source": "t2", "target": "g1"},
      {"source": "g1", "target": "end"}
    ]
  }
}
```

Create `agent_gateway/orchestrator/templates/support_ticket_escalation.json`:

```json
{
  "id": "tpl_support_ticket_escalation",
  "name": "Support Ticket Escalation",
  "description": "Triage an incoming support ticket and draft an escalation response, with a review step before it's sent.",
  "canvas_json": {
    "nodes": [
      {"id": "start", "type": "start", "data": {}},
      {"id": "t1", "type": "teammate", "data": {
        "role": "Ticket Triage Specialist",
        "objective": "Read the ticket, classify its severity and category, and summarize the customer's core issue in one paragraph.",
        "tier": "speed",
        "connected_apps": ["hubspot"],
        "knowledge": []
      }},
      {"id": "t2", "type": "teammate", "data": {
        "role": "Escalation Responder",
        "objective": "Draft a clear, empathetic response addressing the triaged issue and proposing next steps or a callback window.",
        "tier": "balanced",
        "connected_apps": ["gmail"],
        "knowledge": ["Standard callback windows"]
      }},
      {"id": "g1", "type": "approval_gate", "data": {
        "label": "Review response before sending",
        "allow_edit": true
      }},
      {"id": "end", "type": "end", "data": {}}
    ],
    "edges": [
      {"source": "start", "target": "t1"},
      {"source": "t1", "target": "t2"},
      {"source": "t2", "target": "g1"},
      {"source": "g1", "target": "end"}
    ]
  }
}
```

Create `agent_gateway/orchestrator/seed.py`:

```python
"""Idempotent startup seed of the 3 out-of-the-box launch templates.
Re-compiles each template file's canvas_json fresh on every call and
upserts it, so the DB is always re-derived from the files on disk --
never hand-duplicated, never drifting."""

from __future__ import annotations

import importlib.resources
import json

from agent_gateway.orchestrator.compiler import compile_canvas
from agent_gateway.orchestrator.store import OrchestratorStore

TEMPLATE_FILES = [
    "inbound_sales_triage.json",
    "multi_channel_social_content.json",
    "support_ticket_escalation.json",
]


def seed_templates(store: OrchestratorStore) -> None:
    package = importlib.resources.files("agent_gateway.orchestrator.templates")
    for filename in TEMPLATE_FILES:
        raw = json.loads(package.joinpath(filename).read_text())
        playbook = compile_canvas(
            raw["canvas_json"], playbook_id=raw["id"], name=raw["name"],
            description=raw["description"],
        )
        store.upsert_playbook(
            id=playbook.id, name=playbook.name, description=playbook.description,
            schema_version=playbook.schema_version, definition_json=playbook.model_dump(),
            canvas_json=raw["canvas_json"], is_template=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_seed.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/templates/ agent_gateway/orchestrator/seed.py tests/test_orchestrator_seed.py
git commit -m "feat: add 3 launch templates and idempotent seed_templates()"
```

---

## Task 13: FastAPI router (`orchestrator/routes.py`) including SSE termination

**Files:**
- Create: `agent_gateway/orchestrator/routes.py`
- Test: `tests/test_orchestrator_routes.py` (new file)

**Interfaces:**
- Consumes: `OrchestratorStore`, `PlaybookRunner`, `EventBus`, `CompilerError`, `compile_canvas`, `Playbook` — all attached to `request.app.state.gateway` as `orchestrator_store`, `playbook_runner`, `event_bus` (wired in Task 14).
- Produces: `build_router() -> APIRouter` mounted at prefix `/v1/playbooks`, implementing the 7 endpoints from the spec.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_routes.py`:

```python
"""End-to-end tests for the orchestrator's FastAPI routes, including the
SSE termination requirement (route must close the stream right after a
terminal event)."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_gateway.core.provider_routing import ProviderRegistry
from agent_gateway.orchestrator.engine import PlaybookRunner
from agent_gateway.orchestrator.events import EventBus
from agent_gateway.orchestrator.routes import build_router
from agent_gateway.orchestrator.store import OrchestratorStore
from agent_gateway.proxy.config import ProviderConfig, ProvidersConfig
from agent_gateway.storage.sqlite_store import SqliteStore
from fastapi import FastAPI


class _FakeGateway:
    def __init__(self):
        self.store = OrchestratorStore(SqliteStore(":memory:"))
        self.orchestrator_store = self.store
        self.event_bus = EventBus()

        async def complete(*, model, prompt, on_token):
            await on_token("Result text")
            return "Result text"

        self.playbook_runner = PlaybookRunner(
            store=self.store, events=self.event_bus,
            tier_models={"speed": "m", "balanced": "m", "brain": "m"},
            blackboard_store=SqliteStore(":memory:"), complete=complete,
        )


def _app():
    app = FastAPI()
    app.state.gateway = _FakeGateway()
    app.include_router(build_router())
    return app


def _client():
    transport = httpx.ASGITransport(app=_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestCreateAndListPlaybooks:
    async def test_create_then_list_then_get(self):
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "t1", "type": "teammate", "data": {
                    "role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks", json={
                "name": "My Playbook", "canvas_json": canvas,
            })
            assert create_resp.status_code == 200
            playbook_id = create_resp.json()["id"]

            list_resp = await client.get("/v1/playbooks")
            assert any(p["id"] == playbook_id for p in list_resp.json())

            get_resp = await client.get(f"/v1/playbooks/{playbook_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["name"] == "My Playbook"

    async def test_invalid_canvas_returns_422_with_plain_language_errors(self):
        canvas = {"nodes": [{"id": "t1", "type": "teammate", "data": {"role": "", "objective": "", "tier": "speed"}}], "edges": []}
        async with _client() as client:
            resp = await client.post("/v1/playbooks", json={"name": "Bad", "canvas_json": canvas})
            assert resp.status_code == 422
            assert "A playbook needs exactly one starting point." in [e["message"] for e in resp.json()["errors"]]


class TestRunLifecycleAndSSETermination:
    async def test_start_run_and_get_snapshot(self):
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks", json={"name": "P", "canvas_json": canvas})
            playbook_id = create_resp.json()["id"]

            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs",
                                          json={"input": {"text": "hello"}})
            assert run_resp.status_code == 200
            run_id = run_resp.json()["id"]

            snapshot_resp = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot_resp.status_code == 200
            assert snapshot_resp.json()["status"] == "completed"

    async def test_sse_stream_closes_after_terminal_event(self):
        app = _app()
        gw = app.state.gateway
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "g1", "type": "approval_gate", "data": {"label": "Review"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "g1"}, {"source": "g1", "target": "end"}],
        }
        from agent_gateway.orchestrator.compiler import compile_canvas
        playbook = compile_canvas(canvas, playbook_id="pb_sse", name="SSE Test")
        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description="", schema_version=1,
            definition_json=playbook.model_dump(), canvas_json=canvas,
        )
        run_id = await gw.playbook_runner.start_run(playbook, "hi")  # pauses at the gate

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/v1/playbooks/runs/{run_id}/events") as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                # aiter_bytes() completing at all (rather than hanging) proves the
                # generator returned right after the terminal event -- this is the
                # concrete proof of the SSE Termination requirement.
                assert b"gate_paused" in body


class TestGateDecision:
    async def test_submit_approve_decision_resumes_run(self):
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "g1", "type": "approval_gate", "data": {"label": "Review"}},
                {"id": "t2", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [
                {"source": "start", "target": "g1"}, {"source": "g1", "target": "t2"},
                {"source": "t2", "target": "end"},
            ],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks", json={"name": "Gated", "canvas_json": canvas})
            playbook_id = create_resp.json()["id"]
            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs", json={"input": {"text": "hi"}})
            run_id = run_resp.json()["id"]

            decision_resp = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                               json={"decision": "approve"})
            assert decision_resp.status_code == 200

            snapshot_resp = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot_resp.json()["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.orchestrator.routes'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/orchestrator/routes.py`:

```python
"""FastAPI router for the Visual Playbook Builder's 7 endpoints. Mounted
into the existing proxy/server.py app in Task 14."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_gateway.orchestrator.compiler import CompilerError, compile_canvas
from agent_gateway.orchestrator.schema import Playbook
from agent_gateway.orchestrator.store import new_id


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/playbooks")

    @router.post("")
    async def create_playbook(request: Request) -> dict:
        gw = request.app.state.gateway
        body = await request.json()
        playbook_id = new_id("pb")
        try:
            playbook = compile_canvas(
                body["canvas_json"], playbook_id=playbook_id,
                name=body["name"], description=body.get("description", ""),
            )
        except CompilerError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc

        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description=playbook.description,
            schema_version=playbook.schema_version, definition_json=playbook.model_dump(),
            canvas_json=body["canvas_json"],
        )
        return gw.orchestrator_store.get_playbook(playbook.id)

    @router.get("")
    async def list_playbooks(request: Request) -> list[dict]:
        gw = request.app.state.gateway
        return gw.orchestrator_store.list_playbooks()

    @router.get("/{playbook_id}")
    async def get_playbook(playbook_id: str, request: Request) -> dict:
        gw = request.app.state.gateway
        playbook = gw.orchestrator_store.get_playbook(playbook_id)
        if playbook is None:
            raise HTTPException(status_code=404, detail="Playbook not found")
        return playbook

    @router.post("/{playbook_id}/runs")
    async def create_run(playbook_id: str, request: Request) -> dict:
        gw = request.app.state.gateway
        body = await request.json()
        playbook_row = gw.orchestrator_store.get_playbook(playbook_id)
        if playbook_row is None:
            raise HTTPException(status_code=404, detail="Playbook not found")
        playbook = Playbook.model_validate(playbook_row["definition_json"])
        run_id = await gw.playbook_runner.start_run(playbook, body["input"]["text"])
        return gw.orchestrator_store.get_run(run_id)

    @router.get("/runs/{run_id}")
    async def get_run_snapshot(run_id: str, request: Request) -> dict:
        gw = request.app.state.gateway
        run = gw.orchestrator_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run["steps"] = gw.orchestrator_store.list_step_records(run_id)
        return run

    @router.get("/runs/{run_id}/events")
    async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
        gw = request.app.state.gateway

        async def event_stream():
            async for event_type, data in gw.event_bus.subscribe(run_id):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.post("/runs/{run_id}/gate")
    async def submit_gate_decision(run_id: str, request: Request) -> dict:
        gw = request.app.state.gateway
        body = await request.json()
        run = gw.orchestrator_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        playbook_row = gw.orchestrator_store.get_playbook(run["playbook_id"])
        playbook = Playbook.model_validate(playbook_row["definition_json"])
        await gw.playbook_runner.resume_with_decision(
            playbook, run_id, body["decision"], edited_output=body.get("edited_output"),
        )
        return gw.orchestrator_store.get_run(run_id)

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_routes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/orchestrator/routes.py tests/test_orchestrator_routes.py
git commit -m "feat: add orchestrator FastAPI router with SSE termination"
```

---

## Task 14: Wire the orchestrator into `proxy/server.py`

**Files:**
- Modify: `agent_gateway/proxy/server.py`
- Test: `tests/test_orchestrator_server_wiring.py` (new file)

**Interfaces:**
- Consumes: `OrchestratorStore` (Task 3), `EventBus` (Task 5), `PlaybookRunner`/`GatewayCompletionFn` (Tasks 8–11), `seed_templates` (Task 12), `build_router` (Task 13).
- Produces: `GatewayState.orchestrator_store`, `GatewayState.event_bus`, `GatewayState.playbook_runner`; `create_app()` now mounts the playbooks router and runs seed + crash-recovery on startup.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_server_wiring.py`:

```python
"""Confirms the orchestrator is wired into the main gateway app: routes
respond, templates are seeded on startup, and gateway state carries the
orchestrator objects."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_gateway.proxy.config import GatewayConfig
from agent_gateway.proxy.server import create_app


class TestOrchestratorWiring:
    def test_gateway_state_has_orchestrator_objects(self):
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            gw = app.state.gateway
            assert gw.orchestrator_store is not None
            assert gw.event_bus is not None
            assert gw.playbook_runner is not None

    def test_templates_are_seeded_on_startup(self):
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            resp = client.get("/v1/playbooks")
            assert resp.status_code == 200
            ids = {p["id"] for p in resp.json()}
            assert "tpl_inbound_sales_triage" in ids
            assert "tpl_multi_channel_social_content" in ids
            assert "tpl_support_ticket_escalation" in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_server_wiring.py -v`
Expected: FAIL — `AttributeError: 'GatewayState' object has no attribute 'orchestrator_store'`

- [ ] **Step 3: Write minimal implementation**

In `agent_gateway/proxy/server.py`, add imports:

```python
from agent_gateway.orchestrator.engine import GatewayCompletionFn, PlaybookRunner
from agent_gateway.orchestrator.events import EventBus
from agent_gateway.orchestrator.routes import build_router
from agent_gateway.orchestrator.schema import Playbook
from agent_gateway.orchestrator.seed import seed_templates
from agent_gateway.orchestrator.store import OrchestratorStore
```

In `GatewayState.__init__`, after `self.http_client = httpx.AsyncClient(...)`:

```python
        self.orchestrator_store = OrchestratorStore(self.store)
        self.event_bus = EventBus()
        completion_fn = GatewayCompletionFn(
            provider_registry=self.provider_registry, http_client=self.http_client,
        )
        self.playbook_runner = PlaybookRunner(
            store=self.orchestrator_store, events=self.event_bus,
            tier_models=config.playbook_tiers, blackboard_store=self.store,
            complete=completion_fn,
        )
```

In `create_app`'s `lifespan`, replace:

```python
        app.state.gateway = GatewayState(config)
        try:
            yield
```

with:

```python
        app.state.gateway = GatewayState(config)
        seed_templates(app.state.gateway.orchestrator_store)
        playbooks_by_id = {
            row["id"]: Playbook.model_validate(row["definition_json"])
            for row in app.state.gateway.orchestrator_store.list_playbooks()
        }
        await app.state.gateway.playbook_runner.recover_interrupted_runs(playbooks_by_id)
        try:
            yield
```

In `create_app`, after `app = FastAPI(...)`:

```python
    app.include_router(build_router())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_server_wiring.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the FULL backend suite to confirm no regressions**

Run: `pytest tests/ -v`
Expected: PASS — all Sub-project 1 tests (137) plus all new orchestrator tests from Tasks 1–14 pass, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add agent_gateway/proxy/server.py tests/test_orchestrator_server_wiring.py
git commit -m "feat: wire orchestrator (routes, seed, crash recovery) into proxy/server.py"
```

---

## Task 15: Frontend scaffold (Next.js + React Flow + Tailwind + Zustand + Vitest)

**Files:**
- Create: `frontend/` (new Next.js app — package.json, tsconfig.json, tailwind config, next config, app/layout.tsx, app/globals.css)
- Create: `frontend/vitest.config.ts`
- Create: `frontend/vitest.setup.ts`

**Interfaces:**
- Produces: a working `frontend/` app that builds and runs `npm run test` with zero test files (an empty pass), ready for later tasks to add components into.

- [ ] **Step 1: Scaffold the Next.js app**

```bash
cd /Users/macbook/agent-gateway
npx create-next-app@latest frontend --typescript --tailwind --app --no-src-dir --import-alias "@/*" --eslint --use-npm
```

- [ ] **Step 2: Install runtime and dev dependencies**

```bash
cd frontend
npm install @xyflow/react zustand
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @vitejs/plugin-react
```

- [ ] **Step 3: Create the Vitest config**

Create `frontend/vitest.config.ts`:

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
});
```

Create `frontend/vitest.setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
```

In `frontend/package.json`, add to `"scripts"`:

```json
"test": "vitest run"
```

- [ ] **Step 4: Verify the scaffold builds and the (empty) test suite passes**

Run: `cd frontend && npm run build`
Expected: build succeeds (default Next.js starter page).

Run: `cd frontend && npm test`
Expected: `No test files found` is acceptable at this step — later tasks add tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/
git commit -m "feat: scaffold frontend/ Next.js app with React Flow, Tailwind, Zustand, Vitest"
```

---

## Task 16: Typed API client (`types/api.ts`, `lib/api/client.ts`)

**Files:**
- Create: `frontend/types/api.ts`
- Create: `frontend/lib/api/client.ts`
- Test: `frontend/lib/api/client.test.ts` (new file)

**Interfaces:**
- Produces: TypeScript types `PlaybookSummary`, `PlaybookDetail`, `StepSnapshot`, `RunSnapshot`, `RunEvent` (discriminated union), `GateDecision`; `api` object with `listPlaybooks()`, `getPlaybook(id)`, `createPlaybook(input)`, `startRun(playbookId, inputText)`, `getRunSnapshot(runId)`, `submitGateDecision(runId, decision)` — each matching the backend's exact 7-endpoint contract from Task 13.

- [ ] **Step 1: Write the failing test**

Create `frontend/lib/api/client.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "./client";

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("listPlaybooks calls GET /v1/playbooks", async () => {
    (fetch as any).mockResolvedValue({ ok: true, json: async () => [] });
    await api.listPlaybooks();
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/playbooks",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("startRun posts input text to the run endpoint", async () => {
    (fetch as any).mockResolvedValue({ ok: true, json: async () => ({ id: "run_1" }) });
    const result = await api.startRun("pb_1", "hello");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/playbooks/pb_1/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: { text: "hello" } }),
      }),
    );
    expect(result.id).toBe("run_1");
  });

  it("submitGateDecision posts to the gate endpoint", async () => {
    (fetch as any).mockResolvedValue({ ok: true, json: async () => ({ id: "run_1", status: "completed" }) });
    await api.submitGateDecision("run_1", { decision: "approve" });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/playbooks/runs/run_1/gate",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "approve" }) }),
    );
  });

  it("throws when the response is not ok", async () => {
    (fetch as any).mockResolvedValue({ ok: false, status: 422, json: async () => ({ errors: [] }) });
    await expect(api.getPlaybook("missing")).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './client'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/types/api.ts`:

```typescript
// Hand-authored to match agent_gateway/orchestrator/routes.py's 7 endpoints.
// Regenerate from the live backend once it's running:
//   npx openapi-typescript http://localhost:8080/openapi.json -o types/api.ts

export type Tier = "speed" | "balanced" | "brain";

export interface TeammateConfig {
  role: string;
  objective: string;
  tier: Tier;
  connected_apps: string[];
  knowledge: string[];
}

export interface GateConfig {
  label: string;
  allow_edit: boolean;
}

export interface PlaybookStep {
  step_id: string;
  type: "teammate" | "approval_gate";
  teammate?: TeammateConfig;
  gate?: GateConfig;
}

export interface PlaybookSummary {
  id: string;
  name: string;
  description: string;
  is_template: boolean;
  created_at: string;
  updated_at: string;
}

export interface PlaybookDetail extends PlaybookSummary {
  schema_version: number;
  definition_json: { steps: PlaybookStep[] };
  canvas_json: { nodes: unknown[]; edges: unknown[] };
}

export type RunStatus = "running" | "paused" | "completed" | "failed" | "rejected";

export interface StepSnapshot {
  step_index: number;
  step_id: string;
  status: "pending" | "running" | "completed" | "awaiting_approval" | "failed" | "rejected";
  output_json: { text: string } | null;
}

export interface RunSnapshot {
  id: string;
  playbook_id: string;
  status: RunStatus;
  current_step_index: number;
  input_json: { text: string };
  steps: StepSnapshot[];
}

export type RunEvent =
  | { type: "step_started"; data: { step_index: number; step_id: string } }
  | { type: "token"; data: { step_index: number; token: string } }
  | { type: "step_completed"; data: { step_index: number; output: { text: string } } }
  | { type: "gate_paused"; data: { step_index: number; step_id: string; label: string; allow_edit: boolean; proposed_output: { text: string } } }
  | { type: "run_completed"; data: Record<string, never> }
  | { type: "run_failed"; data: { step_index: number; error: string } };

export interface GateDecision {
  decision: "approve" | "edit" | "reject";
  edited_output?: { text: string };
}
```

Create `frontend/lib/api/client.ts`:

```typescript
import type { GateDecision, PlaybookDetail, PlaybookSummary, RunSnapshot } from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json();
}

export const api = {
  listPlaybooks: () => request<PlaybookSummary[]>("/v1/playbooks", { method: "GET" }),

  getPlaybook: (id: string) => request<PlaybookDetail>(`/v1/playbooks/${id}`, { method: "GET" }),

  createPlaybook: (input: { name: string; description?: string; canvas_json: unknown }) =>
    request<PlaybookDetail>("/v1/playbooks", { method: "POST", body: JSON.stringify(input) }),

  startRun: (playbookId: string, inputText: string) =>
    request<RunSnapshot>(`/v1/playbooks/${playbookId}/runs`, {
      method: "POST",
      body: JSON.stringify({ input: { text: inputText } }),
    }),

  getRunSnapshot: (runId: string) =>
    request<RunSnapshot>(`/v1/playbooks/runs/${runId}`, { method: "GET" }),

  submitGateDecision: (runId: string, decision: GateDecision) =>
    request<RunSnapshot>(`/v1/playbooks/runs/${runId}/gate`, {
      method: "POST",
      body: JSON.stringify(decision),
    }),
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/types/api.ts frontend/lib/api/client.ts frontend/lib/api/client.test.ts
git commit -m "feat: add typed API client for the orchestrator's 7 endpoints"
```

---

## Task 17: Canvas execution-state store (Zustand) + `TeammateCardNode`

**Files:**
- Create: `frontend/lib/store/canvasStore.ts`
- Create: `frontend/components/canvas/TeammateCardNode.tsx`
- Test: `frontend/components/canvas/TeammateCardNode.test.tsx` (new file)

**Interfaces:**
- Produces: `useCanvasStore` (Zustand hook) holding `executionStateByStepId: Record<string, ExecutionState>` where `ExecutionState = "idle" | "running" | "done" | "awaiting_approval" | "failed"`, with `setExecutionState(stepId, state)` and `resetExecutionState()`; `TeammateCardNode` (React Flow custom node component) rendering `data-testid="teammate-card"` with `data-state` reflecting the store.

- [ ] **Step 1: Write the failing test**

Create `frontend/components/canvas/TeammateCardNode.test.tsx`:

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { TeammateCardNode } from "./TeammateCardNode";
import { useCanvasStore } from "@/lib/store/canvasStore";

describe("TeammateCardNode", () => {
  beforeEach(() => {
    useCanvasStore.getState().resetExecutionState();
  });

  it("renders idle by default", () => {
    render(<TeammateCardNode id="t1" data={{ role: "Qualifier", objective: "Qualify leads" }} />);
    expect(screen.getByTestId("teammate-card")).toHaveAttribute("data-state", "idle");
    expect(screen.getByText("Qualifier")).toBeInTheDocument();
  });

  it("reflects running state from the store", () => {
    useCanvasStore.getState().setExecutionState("t1", "running");
    render(<TeammateCardNode id="t1" data={{ role: "Qualifier", objective: "Qualify leads" }} />);
    expect(screen.getByTestId("teammate-card")).toHaveAttribute("data-state", "running");
  });

  it("reflects each of the 5 execution states", () => {
    const states: Array<"idle" | "running" | "done" | "awaiting_approval" | "failed"> =
      ["idle", "running", "done", "awaiting_approval", "failed"];
    for (const state of states) {
      useCanvasStore.getState().setExecutionState("t1", state);
      const { unmount } = render(<TeammateCardNode id="t1" data={{ role: "R", objective: "O" }} />);
      expect(screen.getByTestId("teammate-card")).toHaveAttribute("data-state", state);
      unmount();
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module '@/lib/store/canvasStore'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/lib/store/canvasStore.ts`:

```typescript
import { create } from "zustand";

export type ExecutionState = "idle" | "running" | "done" | "awaiting_approval" | "failed";

interface CanvasStore {
  executionStateByStepId: Record<string, ExecutionState>;
  setExecutionState: (stepId: string, state: ExecutionState) => void;
  resetExecutionState: () => void;
}

export const useCanvasStore = create<CanvasStore>((set) => ({
  executionStateByStepId: {},
  setExecutionState: (stepId, state) =>
    set((s) => ({ executionStateByStepId: { ...s.executionStateByStepId, [stepId]: state } })),
  resetExecutionState: () => set({ executionStateByStepId: {} }),
}));
```

Create `frontend/components/canvas/TeammateCardNode.tsx`:

```tsx
import { useCanvasStore } from "@/lib/store/canvasStore";

const GLOW_BY_STATE: Record<string, string> = {
  idle: "border-gray-300",
  running: "border-blue-500 shadow-lg shadow-blue-200 animate-pulse",
  done: "border-green-500",
  awaiting_approval: "border-amber-500 shadow-lg shadow-amber-200",
  failed: "border-red-500",
};

interface TeammateCardNodeProps {
  id: string;
  data: { role: string; objective: string };
}

export function TeammateCardNode({ id, data }: TeammateCardNodeProps) {
  const state = useCanvasStore((s) => s.executionStateByStepId[id] ?? "idle");
  return (
    <div
      data-testid="teammate-card"
      data-state={state}
      className={`rounded-lg border-2 bg-white p-3 ${GLOW_BY_STATE[state]}`}
    >
      <div className="font-semibold">{data.role}</div>
      <div className="text-sm text-gray-600">{data.objective}</div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/lib/store/canvasStore.ts frontend/components/canvas/TeammateCardNode.tsx frontend/components/canvas/TeammateCardNode.test.tsx
git commit -m "feat: add canvasStore (execution-state) and TeammateCardNode with glow states"
```

---

## Task 18: `ApprovalGateNode` + `PlaybookCanvas`

**Files:**
- Create: `frontend/components/canvas/ApprovalGateNode.tsx`
- Create: `frontend/components/canvas/PlaybookCanvas.tsx`

**Interfaces:**
- Consumes: `TeammateCardNode` (Task 17), `@xyflow/react`'s `ReactFlow`, `Node`, `Edge`.
- Produces: `ApprovalGateNode` (React Flow custom node), `PlaybookCanvas` (wraps `ReactFlow` with `nodeTypes = { teammate: TeammateCardNode, approval_gate: ApprovalGateNode }`).

- [ ] **Step 1: Create `ApprovalGateNode`**

Create `frontend/components/canvas/ApprovalGateNode.tsx`:

```tsx
import { useCanvasStore } from "@/lib/store/canvasStore";

interface ApprovalGateNodeProps {
  id: string;
  data: { label: string };
}

export function ApprovalGateNode({ id, data }: ApprovalGateNodeProps) {
  const state = useCanvasStore((s) => s.executionStateByStepId[id] ?? "idle");
  const border = state === "awaiting_approval" ? "border-amber-500 shadow-lg shadow-amber-200" : "border-gray-300";
  return (
    <div data-testid="approval-gate-card" data-state={state}
         className={`rounded-lg border-2 border-dashed bg-amber-50 p-3 ${border}`}>
      <div className="text-xs uppercase tracking-wide text-amber-700">Approval Gate</div>
      <div className="font-semibold">{data.label}</div>
    </div>
  );
}
```

- [ ] **Step 2: Create `PlaybookCanvas`**

Create `frontend/components/canvas/PlaybookCanvas.tsx`:

```tsx
"use client";

import { ReactFlow, Background, Controls, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { TeammateCardNode } from "./TeammateCardNode";
import { ApprovalGateNode } from "./ApprovalGateNode";

const nodeTypes = {
  teammate: TeammateCardNode,
  approval_gate: ApprovalGateNode,
};

interface PlaybookCanvasProps {
  nodes: Node[];
  edges: Edge[];
}

export function PlaybookCanvas({ nodes, edges }: PlaybookCanvasProps) {
  return (
    <div style={{ width: "100%", height: "600px" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView>
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 3: Verify the app still builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/components/canvas/ApprovalGateNode.tsx frontend/components/canvas/PlaybookCanvas.tsx
git commit -m "feat: add ApprovalGateNode and PlaybookCanvas (React Flow wrapper)"
```

---

## Task 19: 4-step `TeammateDrawer`

**Files:**
- Create: `frontend/components/drawer/TeammateDrawer.tsx`
- Create: `frontend/components/drawer/steps/RoleStep.tsx`
- Create: `frontend/components/drawer/steps/ObjectiveStep.tsx`
- Create: `frontend/components/drawer/steps/TierStep.tsx`
- Create: `frontend/components/drawer/steps/KnowledgeAppsStep.tsx`
- Test: `frontend/components/drawer/TeammateDrawer.test.tsx` (new file)

**Interfaces:**
- Produces: `TeammateDrawerConfig` type (`{ role: string; objective: string; tier: Tier; knowledge: string[]; connected_apps: string[] }`), `TeammateDrawer({ onSave }: { onSave: (config: TeammateDrawerConfig) => void })` — a 4-step wizard (Role → Objective → Tier → Knowledge & Connected Apps) with Next/Back and a final Save button.

- [ ] **Step 1: Write the failing test**

Create `frontend/components/drawer/TeammateDrawer.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TeammateDrawer } from "./TeammateDrawer";

describe("TeammateDrawer", () => {
  it("walks through all 4 steps and calls onSave with the full config", () => {
    const onSave = vi.fn();
    render(<TeammateDrawer onSave={onSave} />);

    // Step 1: Role
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "Sales Qualifier" } });
    fireEvent.click(screen.getByText("Next"));

    // Step 2: Objective
    fireEvent.change(screen.getByLabelText("Objective"), { target: { value: "Qualify inbound leads" } });
    fireEvent.click(screen.getByText("Next"));

    // Step 3: Tier
    fireEvent.click(screen.getByLabelText("Brain"));
    fireEvent.click(screen.getByText("Next"));

    // Step 4: Knowledge & Connected Apps
    fireEvent.change(screen.getByLabelText("Knowledge"), { target: { value: "ICP doc" } });
    fireEvent.change(screen.getByLabelText("Connected Apps"), { target: { value: "gmail" } });
    fireEvent.click(screen.getByText("Save"));

    expect(onSave).toHaveBeenCalledWith({
      role: "Sales Qualifier",
      objective: "Qualify inbound leads",
      tier: "brain",
      knowledge: ["ICP doc"],
      connected_apps: ["gmail"],
    });
  });

  it("Back returns to the previous step without losing entered data", () => {
    const onSave = vi.fn();
    render(<TeammateDrawer onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "Router" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.click(screen.getByText("Back"));
    expect(screen.getByLabelText("Role")).toHaveValue("Router");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './TeammateDrawer'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/components/drawer/steps/RoleStep.tsx`:

```tsx
interface RoleStepProps {
  value: string;
  onChange: (value: string) => void;
}

export function RoleStep({ value, onChange }: RoleStepProps) {
  return (
    <div>
      <label htmlFor="role-input">Role</label>
      <input id="role-input" aria-label="Role" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
```

Create `frontend/components/drawer/steps/ObjectiveStep.tsx`:

```tsx
interface ObjectiveStepProps {
  value: string;
  onChange: (value: string) => void;
}

export function ObjectiveStep({ value, onChange }: ObjectiveStepProps) {
  return (
    <div>
      <label htmlFor="objective-input">Objective</label>
      <textarea id="objective-input" aria-label="Objective" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
```

Create `frontend/components/drawer/steps/TierStep.tsx`:

```tsx
import type { Tier } from "@/types/api";

interface TierStepProps {
  value: Tier;
  onChange: (value: Tier) => void;
}

const TIERS: { value: Tier; label: string }[] = [
  { value: "speed", label: "Speed" },
  { value: "balanced", label: "Balanced" },
  { value: "brain", label: "Brain" },
];

export function TierStep({ value, onChange }: TierStepProps) {
  return (
    <fieldset>
      <legend>Tier</legend>
      {TIERS.map((tier) => (
        <label key={tier.value}>
          <input
            type="radio"
            name="tier"
            aria-label={tier.label}
            checked={value === tier.value}
            onChange={() => onChange(tier.value)}
          />
          {tier.label}
        </label>
      ))}
    </fieldset>
  );
}
```

Create `frontend/components/drawer/steps/KnowledgeAppsStep.tsx`:

```tsx
interface KnowledgeAppsStepProps {
  knowledge: string;
  connectedApps: string;
  onKnowledgeChange: (value: string) => void;
  onConnectedAppsChange: (value: string) => void;
}

export function KnowledgeAppsStep({
  knowledge, connectedApps, onKnowledgeChange, onConnectedAppsChange,
}: KnowledgeAppsStepProps) {
  return (
    <div>
      <label htmlFor="knowledge-input">Knowledge</label>
      <input id="knowledge-input" aria-label="Knowledge" value={knowledge}
             onChange={(e) => onKnowledgeChange(e.target.value)} />

      <label htmlFor="apps-input">Connected Apps</label>
      <input id="apps-input" aria-label="Connected Apps" value={connectedApps}
             onChange={(e) => onConnectedAppsChange(e.target.value)} />
    </div>
  );
}
```

Create `frontend/components/drawer/TeammateDrawer.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { Tier } from "@/types/api";
import { RoleStep } from "./steps/RoleStep";
import { ObjectiveStep } from "./steps/ObjectiveStep";
import { TierStep } from "./steps/TierStep";
import { KnowledgeAppsStep } from "./steps/KnowledgeAppsStep";

export interface TeammateDrawerConfig {
  role: string;
  objective: string;
  tier: Tier;
  knowledge: string[];
  connected_apps: string[];
}

const STEPS = ["role", "objective", "tier", "knowledge"] as const;

interface TeammateDrawerProps {
  onSave: (config: TeammateDrawerConfig) => void;
}

export function TeammateDrawer({ onSave }: TeammateDrawerProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [role, setRole] = useState("");
  const [objective, setObjective] = useState("");
  const [tier, setTier] = useState<Tier>("balanced");
  const [knowledge, setKnowledge] = useState("");
  const [connectedApps, setConnectedApps] = useState("");

  const step = STEPS[stepIndex];
  const isLast = stepIndex === STEPS.length - 1;

  const handleNext = () => {
    if (isLast) {
      onSave({
        role, objective, tier,
        knowledge: knowledge ? [knowledge] : [],
        connected_apps: connectedApps ? [connectedApps] : [],
      });
      return;
    }
    setStepIndex((i) => i + 1);
  };

  return (
    <div>
      {step === "role" && <RoleStep value={role} onChange={setRole} />}
      {step === "objective" && <ObjectiveStep value={objective} onChange={setObjective} />}
      {step === "tier" && <TierStep value={tier} onChange={setTier} />}
      {step === "knowledge" && (
        <KnowledgeAppsStep
          knowledge={knowledge} connectedApps={connectedApps}
          onKnowledgeChange={setKnowledge} onConnectedAppsChange={setConnectedApps}
        />
      )}

      <div>
        {stepIndex > 0 && <button onClick={() => setStepIndex((i) => i - 1)}>Back</button>}
        <button onClick={handleNext}>{isLast ? "Save" : "Next"}</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/components/drawer/
git commit -m "feat: add 4-step TeammateDrawer (Role/Objective/Tier/Knowledge & Apps)"
```

---

## Task 20: `useRunEvents` (snapshot-then-subscribe SSE hook)

**Files:**
- Create: `frontend/lib/sse/useRunEvents.ts`
- Test: `frontend/lib/sse/useRunEvents.test.ts` (new file)

**Interfaces:**
- Consumes: `api.getRunSnapshot` (Task 16).
- Produces: `useRunEvents(runId: string)` — a hook that fetches the run snapshot first; if `status` is `"running"` or `"paused"`, opens an `EventSource` at `${BASE_URL}/v1/playbooks/runs/${runId}/events` and accumulates events into `{ snapshot, events, latestEvent }`. If the snapshot is already terminal (`completed`/`failed`/`rejected`), it never opens an `EventSource`.

- [ ] **Step 1: Write the failing test**

Create `frontend/lib/sse/useRunEvents.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useRunEvents } from "./useRunEvents";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  api: { getRunSnapshot: vi.fn() },
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    for (const listener of this.listeners[type] ?? []) {
      listener({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
}

describe("useRunEvents", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("does not open an EventSource when the run is already completed", async () => {
    (api.getRunSnapshot as any).mockResolvedValue({ id: "run_1", status: "completed", steps: [] });
    renderHook(() => useRunEvents("run_1"));
    await waitFor(() => expect(api.getRunSnapshot).toHaveBeenCalled());
    expect(FakeEventSource.instances.length).toBe(0);
  });

  it("opens an EventSource and accumulates events when the run is still running", async () => {
    (api.getRunSnapshot as any).mockResolvedValue({ id: "run_1", status: "running", steps: [] });
    const { result } = renderHook(() => useRunEvents("run_1"));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain("/v1/playbooks/runs/run_1/events");

    source.emit("step_started", { step_index: 0, step_id: "t1" });
    await waitFor(() => expect(result.current.events.length).toBe(1));
    expect(result.current.events[0]).toEqual({ type: "step_started", data: { step_index: 0, step_id: "t1" } });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './useRunEvents'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/lib/sse/useRunEvents.ts`:

```typescript
"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { RunEvent, RunSnapshot } from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";
const EVENT_TYPES: RunEvent["type"][] = [
  "step_started", "token", "step_completed", "gate_paused", "run_completed", "run_failed",
];

export function useRunEvents(runId: string) {
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;

    api.getRunSnapshot(runId).then((initialSnapshot) => {
      if (cancelled) return;
      setSnapshot(initialSnapshot);
      if (initialSnapshot.status !== "running" && initialSnapshot.status !== "paused") {
        return;
      }
      source = new EventSource(`${BASE_URL}/v1/playbooks/runs/${runId}/events`);
      for (const type of EVENT_TYPES) {
        source.addEventListener(type, (event) => {
          const data = JSON.parse((event as MessageEvent).data);
          setEvents((prev) => [...prev, { type, data } as RunEvent]);
        });
      }
    });

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [runId]);

  return { snapshot, events, latestEvent: events[events.length - 1] ?? null };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/lib/sse/useRunEvents.ts frontend/lib/sse/useRunEvents.test.ts
git commit -m "feat: add useRunEvents snapshot-then-subscribe SSE hook"
```

---

## Task 21: Gate review UI + run controls

**Files:**
- Create: `frontend/components/run/GateReviewPanel.tsx`
- Create: `frontend/components/run/RunControlBar.tsx`
- Create: `frontend/components/run/StepOutputPane.tsx`
- Test: `frontend/components/run/GateReviewPanel.test.tsx` (new file)

**Interfaces:**
- Consumes: `api.submitGateDecision` (Task 16), `GateDecision` type (Task 16).
- Produces: `GateReviewPanel({ runId, label, allowEdit, proposedOutput, onDecided })` with Approve/Reject/Edit UI; `RunControlBar({ status })` (status pill); `StepOutputPane({ steps })` (renders step outputs).

- [ ] **Step 1: Write the failing test**

Create `frontend/components/run/GateReviewPanel.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GateReviewPanel } from "./GateReviewPanel";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  api: { submitGateDecision: vi.fn() },
}));

describe("GateReviewPanel", () => {
  beforeEach(() => {
    (api.submitGateDecision as any).mockResolvedValue({ id: "run_1", status: "completed" });
  });

  it("Approve calls submitGateDecision with decision approve", async () => {
    const onDecided = vi.fn();
    render(<GateReviewPanel runId="run_1" label="Review before sending" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={onDecided} />);
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(api.submitGateDecision).toHaveBeenCalledWith("run_1", { decision: "approve" }));
    expect(onDecided).toHaveBeenCalled();
  });

  it("Reject calls submitGateDecision with decision reject", async () => {
    render(<GateReviewPanel runId="run_1" label="Review" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={vi.fn()} />);
    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() => expect(api.submitGateDecision).toHaveBeenCalledWith("run_1", { decision: "reject" }));
  });

  it("Edit submits the edited text", async () => {
    render(<GateReviewPanel runId="run_1" label="Review" allowEdit
                             proposedOutput={{ text: "Draft" }} onDecided={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Edit output"), { target: { value: "Edited draft" } });
    fireEvent.click(screen.getByText("Save Edit"));
    await waitFor(() => expect(api.submitGateDecision).toHaveBeenCalledWith(
      "run_1", { decision: "edit", edited_output: { text: "Edited draft" } },
    ));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './GateReviewPanel'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/components/run/GateReviewPanel.tsx`:

```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";

interface GateReviewPanelProps {
  runId: string;
  label: string;
  allowEdit: boolean;
  proposedOutput: { text: string };
  onDecided: () => void;
}

export function GateReviewPanel({ runId, label, allowEdit, proposedOutput, onDecided }: GateReviewPanelProps) {
  const [editedText, setEditedText] = useState(proposedOutput.text);

  const approve = async () => {
    await api.submitGateDecision(runId, { decision: "approve" });
    onDecided();
  };
  const reject = async () => {
    await api.submitGateDecision(runId, { decision: "reject" });
    onDecided();
  };
  const saveEdit = async () => {
    await api.submitGateDecision(runId, { decision: "edit", edited_output: { text: editedText } });
    onDecided();
  };

  return (
    <div>
      <h3>{label}</h3>
      <p>{proposedOutput.text}</p>
      <button onClick={approve}>Approve</button>
      <button onClick={reject}>Reject</button>
      {allowEdit && (
        <div>
          <label htmlFor="edit-output">Edit output</label>
          <textarea id="edit-output" aria-label="Edit output" value={editedText}
                    onChange={(e) => setEditedText(e.target.value)} />
          <button onClick={saveEdit}>Save Edit</button>
        </div>
      )}
    </div>
  );
}
```

Create `frontend/components/run/RunControlBar.tsx`:

```tsx
import type { RunStatus } from "@/types/api";

const LABEL_BY_STATUS: Record<RunStatus, string> = {
  running: "Running", paused: "Waiting for approval", completed: "Completed",
  failed: "Failed", rejected: "Rejected",
};

export function RunControlBar({ status }: { status: RunStatus }) {
  return <div data-testid="run-status-pill">{LABEL_BY_STATUS[status]}</div>;
}
```

Create `frontend/components/run/StepOutputPane.tsx`:

```tsx
import type { StepSnapshot } from "@/types/api";

export function StepOutputPane({ steps }: { steps: StepSnapshot[] }) {
  return (
    <div>
      {steps.map((step) => (
        <div key={step.step_index}>
          <div>{step.step_id} — {step.status}</div>
          {step.output_json && <p>{step.output_json.text}</p>}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/components/run/
git commit -m "feat: add GateReviewPanel, RunControlBar, StepOutputPane"
```

---

## Task 22: `TemplateGallery`

**Files:**
- Create: `frontend/components/templates/TemplateGallery.tsx`
- Test: `frontend/components/templates/TemplateGallery.test.tsx` (new file)

**Interfaces:**
- Consumes: `api.listPlaybooks` (Task 16).
- Produces: `TemplateGallery({ onSelect })` — fetches playbooks, filters `is_template`, renders clickable cards calling `onSelect(playbookId)`.

- [ ] **Step 1: Write the failing test**

Create `frontend/components/templates/TemplateGallery.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TemplateGallery } from "./TemplateGallery";
import { api } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({
  api: { listPlaybooks: vi.fn() },
}));

describe("TemplateGallery", () => {
  it("renders only playbooks flagged is_template, and calls onSelect on click", async () => {
    (api.listPlaybooks as any).mockResolvedValue([
      { id: "tpl_1", name: "Inbound Sales Triage", description: "d", is_template: true },
      { id: "pb_custom", name: "My Custom Playbook", description: "d", is_template: false },
    ]);
    const onSelect = vi.fn();
    render(<TemplateGallery onSelect={onSelect} />);

    await waitFor(() => expect(screen.getByText("Inbound Sales Triage")).toBeInTheDocument());
    expect(screen.queryByText("My Custom Playbook")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Inbound Sales Triage"));
    expect(onSelect).toHaveBeenCalledWith("tpl_1");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `Cannot find module './TemplateGallery'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/components/templates/TemplateGallery.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { PlaybookSummary } from "@/types/api";

interface TemplateGalleryProps {
  onSelect: (playbookId: string) => void;
}

export function TemplateGallery({ onSelect }: TemplateGalleryProps) {
  const [templates, setTemplates] = useState<PlaybookSummary[]>([]);

  useEffect(() => {
    api.listPlaybooks().then((playbooks) => setTemplates(playbooks.filter((p) => p.is_template)));
  }, []);

  return (
    <div>
      {templates.map((template) => (
        <button key={template.id} onClick={() => onSelect(template.id)}>
          <div>{template.name}</div>
          <div>{template.description}</div>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/components/templates/
git commit -m "feat: add TemplateGallery for the 3 out-of-the-box launch templates"
```

---

## Task 23: Page wiring (dashboard, editor, run view)

**Files:**
- Create: `frontend/app/page.tsx`
- Create: `frontend/app/playbooks/[id]/page.tsx`
- Create: `frontend/app/playbooks/[id]/run/[runId]/page.tsx`

**Interfaces:**
- Consumes: `TemplateGallery` (Task 22), `PlaybookCanvas` (Task 18), `useRunEvents` (Task 20), `GateReviewPanel`/`RunControlBar`/`StepOutputPane` (Task 21), `api` (Task 16).
- Produces: 3 routed pages wiring the components above into working screens.

- [ ] **Step 1: Dashboard page**

Create `frontend/app/page.tsx`:

```tsx
"use client";

import { useRouter } from "next/navigation";
import { TemplateGallery } from "@/components/templates/TemplateGallery";

export default function DashboardPage() {
  const router = useRouter();
  return (
    <main>
      <h1>Playbooks</h1>
      <TemplateGallery onSelect={(id) => router.push(`/playbooks/${id}`)} />
    </main>
  );
}
```

- [ ] **Step 2: Playbook editor page**

Create `frontend/app/playbooks/[id]/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { PlaybookCanvas } from "@/components/canvas/PlaybookCanvas";
import type { PlaybookDetail } from "@/types/api";
import type { Node, Edge } from "@xyflow/react";

function toFlowGraph(canvasJson: { nodes: any[]; edges: any[] }): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = canvasJson.nodes
    .filter((n) => n.type === "teammate" || n.type === "approval_gate")
    .map((n) => ({ id: n.id, type: n.type, data: n.data, position: { x: 0, y: 0 } }));
  const edges: Edge[] = canvasJson.edges.map((e: any, i: number) => ({
    id: `e${i}`, source: e.source, target: e.target,
  }));
  return { nodes, edges };
}

export default function PlaybookEditorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [playbook, setPlaybook] = useState<PlaybookDetail | null>(null);
  const [inputText, setInputText] = useState("");

  useEffect(() => {
    api.getPlaybook(id).then(setPlaybook);
  }, [id]);

  if (!playbook) return <p>Loading…</p>;

  const { nodes, edges } = toFlowGraph(playbook.canvas_json as any);

  const runNow = async () => {
    const run = await api.startRun(playbook.id, inputText);
    router.push(`/playbooks/${playbook.id}/run/${run.id}`);
  };

  return (
    <main>
      <h1>{playbook.name}</h1>
      <PlaybookCanvas nodes={nodes} edges={edges} />
      <input aria-label="Run input" value={inputText} onChange={(e) => setInputText(e.target.value)} />
      <button onClick={runNow}>Run</button>
    </main>
  );
}
```

- [ ] **Step 3: Live run page**

Create `frontend/app/playbooks/[id]/run/[runId]/page.tsx`:

```tsx
"use client";

import { useParams } from "next/navigation";
import { useRunEvents } from "@/lib/sse/useRunEvents";
import { RunControlBar } from "@/components/run/RunControlBar";
import { StepOutputPane } from "@/components/run/StepOutputPane";
import { GateReviewPanel } from "@/components/run/GateReviewPanel";

export default function RunPage() {
  const { runId } = useParams<{ id: string; runId: string }>();
  const { snapshot, latestEvent } = useRunEvents(runId);

  if (!snapshot) return <p>Loading…</p>;

  const gateEvent = latestEvent?.type === "gate_paused" ? latestEvent.data : null;

  return (
    <main>
      <RunControlBar status={snapshot.status} />
      <StepOutputPane steps={snapshot.steps} />
      {gateEvent && (
        <GateReviewPanel
          runId={runId}
          label={gateEvent.label}
          allowEdit={gateEvent.allow_edit}
          proposedOutput={gateEvent.proposed_output}
          onDecided={() => window.location.reload()}
        />
      )}
    </main>
  );
}
```

- [ ] **Step 4: Verify the app builds**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /Users/macbook/agent-gateway
git add frontend/app/
git commit -m "feat: wire dashboard, editor, and live run pages"
```

---

## Task 24: Full-suite regression pass (backend + frontend)

**Files:** none (verification-only task).

- [ ] **Step 1: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS — Sub-project 1's 137 tests plus every orchestrator test added in Tasks 1–14, 0 failures.

- [ ] **Step 2: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS — every test added in Tasks 16–22, 0 failures.

- [ ] **Step 3: Run the frontend production build**

Run: `cd frontend && npm run build`
Expected: build succeeds with no type errors.

- [ ] **Step 4: Manual smoke test**

Run: `uvicorn agent_gateway.proxy.server:app --port 8080` in one terminal and `cd frontend && npm run dev` in another. Visit `http://localhost:3000`, confirm the 3 seeded templates render, open one, and confirm the canvas renders Teammate Cards and an Approval Gate card.

- [ ] **Step 5: Commit (only if Steps 1-4 required any fixes)**

```bash
git add -A
git commit -m "fix: address regressions found in full-suite verification pass"
```

---

## Self-Review

**Spec coverage:**
- 4-table SQL schema → Task 3. Pydantic schema → Task 2. Compiler + 6 rules with exact copy → Task 4. Blackboard Assembly exact Markdown → Task 7 + Global Constraint #9. Execution loop (teammate/gate/error/last-step) → Tasks 8–9. `resume_with_decision` approve/edit/reject → Task 9. Crash recovery reset-to-pending → Task 10 + Global Constraint #10. `playbook_tiers` config → Task 6. Real LLM dispatch via existing adapters (shared OpenAI-shaped SSE parsing) → Task 11. 7 API endpoints → Task 13. SSE termination → Task 13 + Global Constraint #11. 3 launch templates + idempotent seed → Task 12 + Global Constraint #12. Server wiring (seed + recovery on startup, router mount) → Task 14. Frontend directory tree (`app/`, `components/canvas|drawer|run|templates`, `lib/api|sse|store`, `types/`) → Tasks 15–23. 4-step Teammate Drawer → Task 19. Real-time glow states on canvas → Tasks 17–18. `useRunEvents` snapshot-then-subscribe reconnect → Task 20. Testing strategy (backend pytest incl. compiler/engine/templates/SSE/seed; frontend Vitest+RTL incl. drawer/glow/reconnect) → covered throughout, verified in Task 24.
- No gaps found.

**Placeholder scan:** No `TBD`/`TODO`/"add appropriate" language anywhere; every step has real, runnable code; every template has real Role/Objective/Knowledge/Connected-Apps content, not lorem-ipsum.

**Type consistency:** `OrchestratorStore` method names/signatures (Task 3) match every call site in `engine.py` (Tasks 7–11) and `routes.py` (Task 13). `PlaybookRunner`'s public methods (`start_run`, `resume_with_decision`, `recover_interrupted_runs`) are named identically everywhere they're called (routes, server wiring, tests). `RunEvent`'s 6 variants (Task 16) match exactly the 6 event types published by `EventBus`/`PlaybookRunner` (Tasks 5, 8–9) and consumed by `useRunEvents` (Task 20). `TeammateDrawerConfig`'s fields match `TeammateConfig`'s fields (Task 2) one-to-one (`connected_apps`, `knowledge` both snake_case on both sides, matching the wire format the compiler expects).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-14-visual-playbook-builder.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
