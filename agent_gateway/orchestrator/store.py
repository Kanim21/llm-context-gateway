"""SQLite persistence for playbooks, runs, step records, and gate
decisions. Installs its own schema into the shared SqliteStore connection
via SqliteStore.executescript(), leaving the pre-existing SCHEMA untouched."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from agent_gateway.storage.sqlite_store import SqliteStore

ORCHESTRATOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS playbooks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    canvas_json TEXT NOT NULL,
    is_template INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playbook_runs (
    id TEXT PRIMARY KEY,
    playbook_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    input_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_step_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    output_json TEXT,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS run_gate_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    decision TEXT NOT NULL,
    edited_output_json TEXT,
    decided_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrchestratorStore:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store
        self.store.executescript(ORCHESTRATOR_SCHEMA)

    # -- playbooks --------------------------------------------------

    def upsert_playbook(
        self, *, id: str, name: str, description: str, schema_version: int,
        definition_json: dict, canvas_json: dict, is_template: bool = False,
        workspace_id: str = "default",
    ) -> None:
        now = _now()
        with self.store.cursor() as cur:
            cur.execute(
                """
                INSERT INTO playbooks
                    (id, workspace_id, name, description, schema_version,
                     definition_json, canvas_json, is_template, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id=excluded.workspace_id,
                    name=excluded.name,
                    description=excluded.description,
                    schema_version=excluded.schema_version,
                    definition_json=excluded.definition_json,
                    canvas_json=excluded.canvas_json,
                    is_template=excluded.is_template,
                    updated_at=excluded.updated_at
                """,
                (id, workspace_id, name, description, schema_version,
                 json.dumps(definition_json), json.dumps(canvas_json),
                 int(is_template), now, now),
            )

    def get_playbook(self, id: str) -> dict | None:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, workspace_id, name, description, schema_version,
                          definition_json, canvas_json, is_template, created_at, updated_at
                   FROM playbooks WHERE id = ?""",
                (id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._playbook_row_to_dict(row)

    def list_playbooks(self, workspace_id: str = "default") -> list[dict]:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, workspace_id, name, description, schema_version,
                          definition_json, canvas_json, is_template, created_at, updated_at
                   FROM playbooks WHERE workspace_id = ? ORDER BY created_at""",
                (workspace_id,),
            )
            rows = cur.fetchall()
        return [self._playbook_row_to_dict(row) for row in rows]

    @staticmethod
    def _playbook_row_to_dict(row) -> dict:
        return {
            "id": row[0], "workspace_id": row[1], "name": row[2], "description": row[3],
            "schema_version": row[4], "definition_json": json.loads(row[5]),
            "canvas_json": json.loads(row[6]), "is_template": bool(row[7]),
            "created_at": row[8], "updated_at": row[9],
        }

    # -- runs ---------------------------------------------------------

    def create_run(self, *, id: str, playbook_id: str, input_text: str,
                    workspace_id: str = "default") -> None:
        now = _now()
        with self.store.cursor() as cur:
            cur.execute(
                """INSERT INTO playbook_runs
                       (id, playbook_id, workspace_id, status, current_step_index,
                        input_json, created_at, updated_at)
                   VALUES (?, ?, ?, 'running', 0, ?, ?, ?)""",
                (id, playbook_id, workspace_id, json.dumps({"text": input_text}), now, now),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, playbook_id, workspace_id, status, current_step_index,
                          input_json, created_at, updated_at
                   FROM playbook_runs WHERE id = ?""",
                (run_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "playbook_id": row[1], "workspace_id": row[2], "status": row[3],
            "current_step_index": row[4], "input_json": json.loads(row[5]),
            "created_at": row[6], "updated_at": row[7],
        }

    def update_run_status(self, run_id: str, status: str,
                           current_step_index: int | None = None) -> None:
        with self.store.cursor() as cur:
            if current_step_index is None:
                cur.execute(
                    "UPDATE playbook_runs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), run_id),
                )
            else:
                cur.execute(
                    "UPDATE playbook_runs SET status = ?, current_step_index = ?, updated_at = ? WHERE id = ?",
                    (status, current_step_index, _now(), run_id),
                )

    def list_runs_by_status(self, status: str) -> list[dict]:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, playbook_id, workspace_id, status, current_step_index,
                          input_json, created_at, updated_at
                   FROM playbook_runs WHERE status = ?""",
                (status,),
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "playbook_id": r[1], "workspace_id": r[2], "status": r[3],
             "current_step_index": r[4], "input_json": json.loads(r[5]),
             "created_at": r[6], "updated_at": r[7]}
            for r in rows
        ]

    # -- step records ---------------------------------------------------

    def create_step_record(self, *, id: str, run_id: str, step_index: int,
                            step_id: str, status: str = "pending") -> None:
        with self.store.cursor() as cur:
            cur.execute(
                """INSERT INTO run_step_records
                       (id, run_id, step_index, step_id, status, output_json, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)""",
                (id, run_id, step_index, step_id, status),
            )

    def get_step_record(self, run_id: str, step_index: int) -> dict | None:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, run_id, step_index, step_id, status, output_json, started_at, completed_at
                   FROM run_step_records WHERE run_id = ? AND step_index = ?""",
                (run_id, step_index),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._step_row_to_dict(row)

    def list_step_records(self, run_id: str) -> list[dict]:
        with self.store.cursor() as cur:
            cur.execute(
                """SELECT id, run_id, step_index, step_id, status, output_json, started_at, completed_at
                   FROM run_step_records WHERE run_id = ? ORDER BY step_index""",
                (run_id,),
            )
            rows = cur.fetchall()
        return [self._step_row_to_dict(row) for row in rows]

    @staticmethod
    def _step_row_to_dict(row) -> dict:
        return {
            "id": row[0], "run_id": row[1], "step_index": row[2], "step_id": row[3],
            "status": row[4], "output_json": json.loads(row[5]) if row[5] else None,
            "started_at": row[6], "completed_at": row[7],
        }

    def update_step_record(self, id: str, *, status: str | None = None,
                            output_json: dict | None = None,
                            started_at: str | None = None,
                            completed_at: str | None = None) -> None:
        fields, values = [], []
        if status is not None:
            fields.append("status = ?"); values.append(status)
        if output_json is not None:
            fields.append("output_json = ?"); values.append(json.dumps(output_json))
        if started_at is not None:
            fields.append("started_at = ?"); values.append(started_at)
        if completed_at is not None:
            fields.append("completed_at = ?"); values.append(completed_at)
        if not fields:
            return
        values.append(id)
        with self.store.cursor() as cur:
            cur.execute(f"UPDATE run_step_records SET {', '.join(fields)} WHERE id = ?", values)

    def reset_step_to_pending(self, run_id: str, step_index: int) -> None:
        with self.store.cursor() as cur:
            cur.execute(
                """UPDATE run_step_records SET status = 'pending', started_at = NULL, completed_at = NULL
                   WHERE run_id = ? AND step_index = ?""",
                (run_id, step_index),
            )

    # -- gate decisions ---------------------------------------------------

    def record_gate_decision(self, *, id: str, run_id: str, step_index: int,
                              decision: str, edited_output_json: dict | None = None) -> None:
        with self.store.cursor() as cur:
            cur.execute(
                """INSERT INTO run_gate_decisions
                       (id, run_id, step_index, decision, edited_output_json, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (id, run_id, step_index, decision,
                 json.dumps(edited_output_json) if edited_output_json is not None else None,
                 _now()),
            )


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
