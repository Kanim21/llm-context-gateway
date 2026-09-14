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
