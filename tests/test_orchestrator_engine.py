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


def _gate_runner():
    store = _make_store()
    playbook = _playbook_with_gate()
    store.upsert_playbook(id=playbook.id, name=playbook.name, description="",
                           schema_version=1, definition_json=playbook.model_dump(), canvas_json={})
    from agent_gateway.orchestrator.engine import PlaybookRunner
    runner = PlaybookRunner(
        store=store, events=EventBus(),
        tier_models={"speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro"},
        blackboard_store=SqliteStore(":memory:"),
        complete=FakeCompletionFn({"gpt-4o-mini": "Qualified: yes"}),
    )
    return runner, playbook, store


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

    async def test_edit_propagates_to_downstream_prompt(self):
        from agent_gateway.orchestrator.engine import assemble_prompt
        runner, playbook, store = await self._runner()
        run_id = await runner.start_run(playbook, "New lead")
        await runner.resume_with_decision(playbook, run_id, "edit",
                                           edited_output={"text": "Edited qualification note"})
        run = store.get_run(run_id)
        prompt = assemble_prompt(store, run=run, steps=playbook.steps, step_index=2)
        assert "Edited qualification note" in prompt
        assert "Qualified: yes" not in prompt

    async def test_reject_stops_run_without_advancing(self):
        runner, playbook, store = await self._runner()
        run_id = await runner.start_run(playbook, "New lead")
        await runner.resume_with_decision(playbook, run_id, "reject")
        run = store.get_run(run_id)
        assert run["status"] == "rejected"
        gate_record = store.get_step_record(run_id, 1)
        assert gate_record["status"] == "rejected"
        t2_record = store.get_step_record(run_id, 2)
        assert t2_record["status"] == "pending"


class TestGateDecisionGuards:
    async def test_unknown_decision_raises_and_leaves_run_paused(self):
        """Previously any string that wasn't "reject"/"edit" fell through to
        the approve path, which defeated the point of the gate."""
        import pytest
        from agent_gateway.orchestrator.engine import InvalidDecisionError

        runner, playbook, store = _gate_runner()
        run_id = await runner.start_run(playbook, "New lead")

        with pytest.raises(InvalidDecisionError):
            await runner.resume_with_decision(playbook, run_id, "banana")

        run = store.get_run(run_id)
        assert run["status"] == "paused"
        assert store.get_step_record(run_id, 1)["status"] == "awaiting_approval"

    async def test_second_decision_on_a_finished_run_raises_run_state_error(self):
        import pytest
        from agent_gateway.orchestrator.engine import RunStateError

        runner, playbook, store = _gate_runner()
        run_id = await runner.start_run(playbook, "New lead")
        await runner.resume_with_decision(playbook, run_id, "approve")
        assert store.get_run(run_id)["status"] == "completed"

        with pytest.raises(RunStateError):
            await runner.resume_with_decision(playbook, run_id, "reject")

        assert store.get_run(run_id)["status"] == "completed"

    async def test_decision_on_a_running_non_gate_step_raises(self):
        import pytest
        from agent_gateway.orchestrator.engine import RunStateError

        runner, playbook, store = _gate_runner()
        run_id = await runner.start_run(playbook, "New lead")
        # Force the run back to a non-gate step while still "paused".
        store.update_run_status(run_id, "paused", current_step_index=0)

        with pytest.raises(RunStateError):
            await runner.resume_with_decision(playbook, run_id, "approve")


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
