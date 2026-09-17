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


import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
import json as _json

import httpx

from agent_gateway.core.blackboard import Blackboard
from agent_gateway.orchestrator.schema import Playbook
from agent_gateway.orchestrator.store import new_id
from agent_gateway.storage.sqlite_store import SqliteStore
from agent_gateway.orchestrator.events import EventBus
from agent_gateway.adapters.gemini_adapter import GeminiAdapter
from agent_gateway.adapters.openai_adapter import OpenAIAdapter
from agent_gateway.core.provider_routing import ProviderRegistry, resolve_credential


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_DECISIONS: tuple[str, ...] = ("approve", "edit", "reject")


class InvalidDecisionError(ValueError):
    """The decision string isn't one of approve/edit/reject.

    Anything unrecognised used to fall through to the approve path, which
    silently defeated the whole point of a human-in-the-loop gate.
    """


class RunStateError(ValueError):
    """The run isn't sitting at an approval gate waiting for a decision."""


class CompletionFn(Protocol):
    async def __call__(self, *, model: str, prompt: str, on_token) -> str: ...


@dataclass
class PlaybookRunner:
    store: OrchestratorStore
    events: EventBus
    tier_models: dict[str, str]
    blackboard_store: SqliteStore
    complete: CompletionFn
    # In-flight background executions, keyed by run_id. Not a constructor arg.
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict, init=False, repr=False)

    def _spawn(self, run_id: str, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks[run_id] = task
        task.add_done_callback(
            lambda t: self._tasks.pop(run_id, None) if self._tasks.get(run_id) is t else None
        )

    async def wait(self, run_id: str) -> None:
        """Await the in-flight execution for a run. Test/shutdown helper; a
        no-op if nothing is running."""
        task = self._tasks.get(run_id)
        if task is not None:
            await task

    async def _execute(self, playbook: Playbook, run_id: str) -> None:
        """Background driver for a run. Per-step failures are handled in
        _run_teammate_step; this is the backstop for anything else, so a
        background crash marks the run failed and terminates the SSE stream
        instead of vanishing."""
        try:
            await self._advance(playbook, run_id)
        except Exception as exc:  # noqa: BLE001 - last-resort backstop
            self.store.update_run_status(run_id, "failed")
            run = self.store.get_run(run_id)
            index = run["current_step_index"] if run else 0
            await self.events.publish(run_id, "run_failed", {"step_index": index, "error": str(exc)})

    async def start_run(self, playbook: Playbook, input_text: str) -> str:
        run_id = new_id("run")
        self.store.create_run(id=run_id, playbook_id=playbook.id, input_text=input_text,
                               workspace_id=playbook.workspace_id)
        for index, step in enumerate(playbook.steps):
            self.store.create_step_record(id=new_id("rec"), run_id=run_id,
                                           step_index=index, step_id=step.step_id)

        blackboard = Blackboard(self.blackboard_store, run_id)
        blackboard.goal = f"Run playbook: {playbook.name}"

        # Do NOT await the run here: it would hold the POST /runs request open
        # for the entire run (every teammate step is a live LLM call). Execute
        # in the background and return immediately; clients watch via SSE or by
        # polling the run snapshot.
        self._spawn(run_id, self._execute(playbook, run_id))
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
            # Persist the reason: the run_failed SSE event is transient, so a
            # user who reloads after a failure would otherwise see no reason.
            self.store.update_step_record(record["id"], status="failed",
                                           output_json={"error": str(exc)}, completed_at=_now())
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

    def _previous_step_record(self, run_id: str, gate_index: int) -> dict | None:
        for record in reversed(self.store.list_step_records(run_id)):
            if record["step_index"] < gate_index and record["status"] == "completed":
                return record
        return None

    async def resume_with_decision(self, playbook: Playbook, run_id: str, decision: str,
                                    step_index: int,
                                    edited_output: dict | None = None) -> None:
        if decision not in VALID_DECISIONS:
            raise InvalidDecisionError(
                f"Unknown decision {decision!r}; expected one of {', '.join(VALID_DECISIONS)}"
            )

        run = self.store.get_run(run_id)
        if run is None:
            raise RunStateError(f"Run {run_id} not found")
        if run["status"] != "paused":
            raise RunStateError(
                f"Run {run_id} is not awaiting a gate decision (status={run['status']})"
            )

        index = run["current_step_index"]
        if index >= len(playbook.steps) or playbook.steps[index].type != "approval_gate":
            raise RunStateError(f"Step {index} is not an approval gate")

        record = self.store.get_step_record(run_id, index)
        if record is None:
            raise RunStateError(f"Run {run_id} has no step record at index {index}")

        if step_index != run["current_step_index"]:
            raise RunStateError(
                f"Run {run_id} is not awaiting a decision for step {step_index} "
                f"(currently awaiting step {run['current_step_index']})"
            )

        self.store.record_gate_decision(id=new_id("gd"), run_id=run_id, step_index=index,
                                         decision=decision, edited_output_json=edited_output)

        if decision == "reject":
            self.store.update_step_record(record["id"], status="rejected", completed_at=_now())
            self.store.update_run_status(run_id, "rejected")
            # Terminate any live SSE stream for this run (see routes.py).
            await self.events.publish(run_id, "run_rejected", {"step_index": index})
            return

        output_json = edited_output if decision == "edit" else None
        self.store.update_step_record(record["id"], status="completed",
                                       output_json=output_json, completed_at=_now())

        # Propagate edited output to the preceding step's record so downstream prompts see it
        if decision == "edit" and edited_output is not None:
            prev_record = self._previous_step_record(run_id, index)
            if prev_record is not None:
                self.store.update_step_record(prev_record["id"], output_json=edited_output)

        # Same non-blocking rationale as start_run: don't hold the gate POST
        # open while the rest of the run executes.
        self._spawn(run_id, self._execute_continuation(playbook, run_id, index))

    async def _execute_continuation(self, playbook: Playbook, run_id: str, index: int) -> None:
        try:
            await self._complete_step_and_continue(playbook, run_id, index)
        except Exception as exc:  # noqa: BLE001
            self.store.update_run_status(run_id, "failed")
            await self.events.publish(run_id, "run_failed", {"step_index": index, "error": str(exc)})

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
