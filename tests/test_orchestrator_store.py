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


def test_update_step_record_treats_values_as_data_not_sql():
    """Defensive: a SQL-injection-shaped string in a *value* is stored
    literally and the table still exists (values are bound, not interpolated)."""
    from agent_gateway.orchestrator.store import OrchestratorStore
    from agent_gateway.storage.sqlite_store import SqliteStore
    store = OrchestratorStore(SqliteStore(":memory:"))
    store.create_run(id="r", playbook_id="p", input_text="x")
    store.create_step_record(id="rec", run_id="r", step_index=0, step_id="s")
    nasty = {"text": "'; DROP TABLE run_step_records; --"}
    store.update_step_record("rec", status="completed", output_json=nasty)
    rec = store.get_step_record("r", 0)
    assert rec["output_json"] == nasty
    assert rec["status"] == "completed"
