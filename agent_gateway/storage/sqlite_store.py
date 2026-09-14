"""Local SQLite artifact store and blackboard tables.

Everything the gateway needs to persist -- offloaded tool observations,
blackboard state (goal/milestones/artifact refs/do-not-retry registry),
and metrics -- lives in one local SQLite file. There is no network
dependency for storage: this is a local-first proxy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    ref TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    codec TEXT NOT NULL DEFAULT 'raw',
    created_at REAL NOT NULL,
    byte_len INTEGER NOT NULL,
    rehydrate_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blackboard_state (
    conversation_id TEXT PRIMARY KEY,
    goal TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS blackboard_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS blackboard_artifact_refs (
    conversation_id TEXT NOT NULL,
    ref TEXT NOT NULL,
    label TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (conversation_id, ref)
);

CREATE TABLE IF NOT EXISTS blackboard_do_not_retry (
    conversation_id TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (conversation_id, action_fingerprint)
);

CREATE TABLE IF NOT EXISTS metrics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class SqliteStore:
    """Thread-safe wrapper around one SQLite database file.

    Use `:memory:` for tests; a real path for a running gateway. A
    single `threading.Lock` serializes writes -- SQLite already
    serializes at the file level, but this avoids "database is locked"
    errors under concurrent request handling in the FastAPI server.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def cursor(self):
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # -- artifacts -----------------------------------------------------

    def put_artifact(self, ref: str, content: bytes, codec: str = "raw") -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO artifacts (ref, content, codec, created_at, byte_len, "
                "rehydrate_count) VALUES (?, ?, ?, ?, ?, COALESCE("
                "(SELECT rehydrate_count FROM artifacts WHERE ref = ?), 0))",
                (ref, content, codec, time.time(), len(content), ref),
            )

    def get_artifact(self, ref: str) -> tuple[bytes, str] | None:
        with self.cursor() as cur:
            cur.execute("SELECT content, codec FROM artifacts WHERE ref = ?", (ref,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "UPDATE artifacts SET rehydrate_count = rehydrate_count + 1 WHERE ref = ?",
                (ref,),
            )
            return bytes(row["content"]), row["codec"]

    def artifact_stats(self) -> dict:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(rehydrate_count = 0), 0) AS never_read FROM artifacts")
            row = cur.fetchone()
            total = row["n"] or 0
            never_read = row["never_read"] or 0
            return {
                "total_artifacts": total,
                "never_rehydrated": never_read,
                "offload_hit_rate": (never_read / total) if total else 0.0,
            }

    # -- blackboard ------------------------------------------------------

    def set_goal(self, conversation_id: str, goal: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO blackboard_state (conversation_id, goal, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET goal = excluded.goal, updated_at = excluded.updated_at",
                (conversation_id, goal, time.time()),
            )

    def get_goal(self, conversation_id: str) -> str | None:
        with self.cursor() as cur:
            cur.execute("SELECT goal FROM blackboard_state WHERE conversation_id = ?", (conversation_id,))
            row = cur.fetchone()
            return row["goal"] if row else None

    def add_milestone(self, conversation_id: str, description: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO blackboard_milestones (conversation_id, description, created_at) VALUES (?, ?, ?)",
                (conversation_id, description, time.time()),
            )

    def list_milestones(self, conversation_id: str) -> list[str]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT description FROM blackboard_milestones WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            )
            return [row["description"] for row in cur.fetchall()]

    def add_artifact_ref(self, conversation_id: str, ref: str, label: str | None = None) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO blackboard_artifact_refs (conversation_id, ref, label, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, ref, label, time.time()),
            )

    def list_artifact_refs(self, conversation_id: str) -> list[dict]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT ref, label FROM blackboard_artifact_refs WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def mark_do_not_retry(self, conversation_id: str, action_fingerprint: str, reason: str = "") -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO blackboard_do_not_retry "
                "(conversation_id, action_fingerprint, reason, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, action_fingerprint, reason, time.time()),
            )

    def is_do_not_retry(self, conversation_id: str, action_fingerprint: str) -> bool:
        with self.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM blackboard_do_not_retry WHERE conversation_id = ? AND action_fingerprint = ?",
                (conversation_id, action_fingerprint),
            )
            return cur.fetchone() is not None

    # -- metrics ---------------------------------------------------------

    def record_metric_event(self, event_type: str, payload: dict, conversation_id: str | None = None) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO metrics_events (conversation_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, event_type, json.dumps(payload), time.time()),
            )

    def list_metric_events(self, event_type: str | None = None) -> list[dict]:
        with self.cursor() as cur:
            if event_type is None:
                cur.execute("SELECT * FROM metrics_events ORDER BY id")
            else:
                cur.execute("SELECT * FROM metrics_events WHERE event_type = ? ORDER BY id", (event_type,))
            return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
