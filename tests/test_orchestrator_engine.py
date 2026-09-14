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
