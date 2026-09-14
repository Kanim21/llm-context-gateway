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
