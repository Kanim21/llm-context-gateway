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
