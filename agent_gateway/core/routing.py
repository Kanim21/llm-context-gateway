"""Model-tier router: send routine extraction work to a cheaper model.

Not every call in an agent loop needs the flagship model. Summarizing a
tool observation, extracting a field from JSON, or classifying an
intent is routine work that a Tier-2 model handles adequately at a
fraction of the cost. This router is a pure classification function --
it does not call any model itself, it only decides which configured
model name a given request should be sent to. The actual dispatch
happens in `adapters/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    FLAGSHIP = "flagship"
    TIER2 = "tier2"


@dataclass
class RoutingRule:
    """Matches on task_kind (an explicit hint from the caller) and/or a
    max input size, whichever is more specific wins."""

    task_kind: str
    tier: Tier
    max_input_tokens: int | None = None


DEFAULT_ROUTINE_TASK_KINDS = frozenset(
    {
        "summarize_observation",
        "extract_field",
        "classify_intent",
        "format_receipt",
        "dedup_check",
    }
)


@dataclass
class ModelTierRouter:
    flagship_model: str
    tier2_model: str
    rules: list[RoutingRule] = field(default_factory=list)
    routine_task_kinds: frozenset[str] = DEFAULT_ROUTINE_TASK_KINDS

    def add_rule(self, rule: RoutingRule) -> None:
        self.rules.append(rule)

    def route(self, task_kind: str, input_tokens: int = 0) -> tuple[Tier, str]:
        for rule in self.rules:
            if rule.task_kind != task_kind:
                continue
            if rule.max_input_tokens is not None and input_tokens > rule.max_input_tokens:
                continue
            model = self.tier2_model if rule.tier is Tier.TIER2 else self.flagship_model
            return rule.tier, model

        if task_kind in self.routine_task_kinds:
            return Tier.TIER2, self.tier2_model
        return Tier.FLAGSHIP, self.flagship_model
