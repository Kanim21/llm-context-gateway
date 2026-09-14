"""Tool schema pruning: only send the model tool definitions it can
plausibly need for the current turn.

This is a lossless space saving when the caller correctly declares
which tools are actually relevant (e.g. from a task-kind hint or a
prior turn's tool usage) -- it does not remove any *content* the model
sees for a tool it may call, it only stops resending schemas for tools
that are provably out of scope for this turn. If `always_include` names
are supplied (e.g. a core "finish_task" tool), they are never pruned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolPruner:
    always_include: frozenset[str] = field(default_factory=frozenset)

    def prune(
        self,
        tool_schemas: list[dict[str, Any]],
        relevant_tool_names: set[str] | None,
    ) -> list[dict[str, Any]]:
        """`relevant_tool_names=None` means "no pruning signal available"
        -- returns all schemas unchanged (fail open, never fail closed
        and silently hide a tool the model actually needs)."""
        if relevant_tool_names is None:
            return tool_schemas
        keep = relevant_tool_names | set(self.always_include)
        return [schema for schema in tool_schemas if _tool_name(schema) in keep]


def _tool_name(schema: dict[str, Any]) -> str:
    if "name" in schema:
        return schema["name"]
    function = schema.get("function", {})
    return function.get("name", "")
