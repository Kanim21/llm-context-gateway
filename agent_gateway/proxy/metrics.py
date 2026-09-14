"""Unblended metrics engine.

Non-negotiable rule (spec Sec 2.1): Token Reduction %, Cost Reduction %,
and Call Reduction % are three distinct measurements over three
distinct units (tokens, dollars, request counts) and must never be
combined into one headline number. This module computes and reports
each one separately; nothing in this codebase is allowed to average or
otherwise blend them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnblendedMetrics:
    baseline_tokens: int
    actual_tokens: int
    baseline_cost_usd: float
    actual_cost_usd: float
    baseline_calls: int
    actual_calls: int

    @property
    def token_reduction_pct(self) -> float:
        if self.baseline_tokens == 0:
            return 0.0
        return 100.0 * (1 - self.actual_tokens / self.baseline_tokens)

    @property
    def cost_reduction_pct(self) -> float:
        if self.baseline_cost_usd == 0:
            return 0.0
        return 100.0 * (1 - self.actual_cost_usd / self.baseline_cost_usd)

    @property
    def call_reduction_pct(self) -> float:
        if self.baseline_calls == 0:
            return 0.0
        return 100.0 * (1 - self.actual_calls / self.baseline_calls)

    def as_dict(self) -> dict:
        """Returns the three metrics as separate, clearly-labeled keys.
        Deliberately does not include any combined/averaged figure."""
        return {
            "token_reduction_pct": round(self.token_reduction_pct, 2),
            "cost_reduction_pct": round(self.cost_reduction_pct, 2),
            "call_reduction_pct": round(self.call_reduction_pct, 2),
            "baseline_tokens": self.baseline_tokens,
            "actual_tokens": self.actual_tokens,
            "baseline_cost_usd": round(self.baseline_cost_usd, 6),
            "actual_cost_usd": round(self.actual_cost_usd, 6),
            "baseline_calls": self.baseline_calls,
            "actual_calls": self.actual_calls,
        }


class MetricsAccumulator:
    """Accumulates unblended metrics across many requests/turns."""

    def __init__(self) -> None:
        self.baseline_tokens = 0
        self.actual_tokens = 0
        self.baseline_cost_usd = 0.0
        self.actual_cost_usd = 0.0
        self.baseline_calls = 0
        self.actual_calls = 0

    def record(
        self,
        *,
        baseline_tokens: int = 0,
        actual_tokens: int = 0,
        baseline_cost_usd: float = 0.0,
        actual_cost_usd: float = 0.0,
        baseline_calls: int = 0,
        actual_calls: int = 0,
    ) -> None:
        self.baseline_tokens += baseline_tokens
        self.actual_tokens += actual_tokens
        self.baseline_cost_usd += baseline_cost_usd
        self.actual_cost_usd += actual_cost_usd
        self.baseline_calls += baseline_calls
        self.actual_calls += actual_calls

    def snapshot(self) -> UnblendedMetrics:
        return UnblendedMetrics(
            baseline_tokens=self.baseline_tokens,
            actual_tokens=self.actual_tokens,
            baseline_cost_usd=self.baseline_cost_usd,
            actual_cost_usd=self.actual_cost_usd,
            baseline_calls=self.baseline_calls,
            actual_calls=self.actual_calls,
        )
