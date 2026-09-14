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
