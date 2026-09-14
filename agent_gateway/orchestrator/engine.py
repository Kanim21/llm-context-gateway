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
