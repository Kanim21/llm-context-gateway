"""SQLite-backed blackboard: externalized agent loop state.

Instead of re-deriving "what am I trying to do" and "what have I already
tried" from the entire transcript every turn, the loop's goal,
milestones, active artifact refs, and a do-not-retry registry are read
and written through this thin façade over `storage.sqlite_store`. This
is what lets `compaction.py` aggressively summarize the transcript
without losing the state the loop actually needs to keep making
progress.
"""

from __future__ import annotations

import hashlib

from agent_gateway.storage.sqlite_store import SqliteStore


def action_fingerprint(tool_name: str, arguments: dict) -> str:
    """A stable id for "this exact tool call", used by the do-not-retry
    registry and by `guardrails.py` for duplicate-action blocking."""
    import json

    canonical = json.dumps({"tool": tool_name, "arguments": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class Blackboard:
    """Per-conversation externalized state, backed by SQLite."""

    def __init__(self, store: SqliteStore, conversation_id: str) -> None:
        self.store = store
        self.conversation_id = conversation_id

    @property
    def goal(self) -> str | None:
        return self.store.get_goal(self.conversation_id)

    @goal.setter
    def goal(self, value: str) -> None:
        self.store.set_goal(self.conversation_id, value)

    def add_milestone(self, description: str) -> None:
        self.store.add_milestone(self.conversation_id, description)

    @property
    def milestones(self) -> list[str]:
        return self.store.list_milestones(self.conversation_id)

    def add_artifact_ref(self, ref: str, label: str | None = None) -> None:
        self.store.add_artifact_ref(self.conversation_id, ref, label)

    @property
    def artifact_refs(self) -> list[dict]:
        return self.store.list_artifact_refs(self.conversation_id)

    def block_retry(self, tool_name: str, arguments: dict, reason: str = "") -> str:
        fingerprint = action_fingerprint(tool_name, arguments)
        self.store.mark_do_not_retry(self.conversation_id, fingerprint, reason)
        return fingerprint

    def is_blocked(self, tool_name: str, arguments: dict) -> bool:
        fingerprint = action_fingerprint(tool_name, arguments)
        return self.store.is_do_not_retry(self.conversation_id, fingerprint)

    def render_summary(self) -> str:
        """A compact textual snapshot suitable for inclusion in a
        compacted prompt -- this is what survives suffix summarization
        (see `compaction.py`) even when raw transcript turns don't."""
        lines = []
        if self.goal:
            lines.append(f"Goal: {self.goal}")
        milestones = self.milestones
        if milestones:
            lines.append("Milestones:")
            lines.extend(f"  - {m}" for m in milestones)
        refs = self.artifact_refs
        if refs:
            lines.append("Active artifact refs:")
            for r in refs:
                label = f" ({r['label']})" if r["label"] else ""
                lines.append(f"  - {r['ref']}{label}")
        return "\n".join(lines)
