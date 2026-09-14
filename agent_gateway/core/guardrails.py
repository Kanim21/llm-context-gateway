"""Pre-LLM interception: guardrails that run before a request reaches
the provider.

Three independent checks, all cheap and deterministic:

1. Duplicate action blocking -- refuses to let the loop re-issue a tool
   call that the blackboard's do-not-retry registry has already marked
   as tried-and-failed (spec: blackboard integration).
2. AST/syntax validation -- for tool calls that carry a code payload
   (e.g. a Python `exec`-style tool), reject syntactically invalid code
   before it burns a model turn.
3. Turn/token circuit breakers -- hard stop once a conversation exceeds
   a configured turn count or cumulative token budget, so a runaway
   loop cannot silently spend without bound.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from agent_gateway.core.blackboard import Blackboard, action_fingerprint


class GuardrailViolation(Exception):
    """Raised by `GuardrailChain.check` when a request should be blocked."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class CircuitBreakerConfig:
    max_turns: int | None = None
    max_cumulative_tokens: int | None = None


@dataclass
class GuardrailChain:
    blackboard: Blackboard | None = None
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _turn_count: int = field(default=0, init=False)
    _cumulative_tokens: int = field(default=0, init=False)

    def check_duplicate_action(self, tool_name: str, arguments: dict) -> None:
        if self.blackboard is None:
            return
        if self.blackboard.is_blocked(tool_name, arguments):
            fingerprint = action_fingerprint(tool_name, arguments)
            raise GuardrailViolation(
                "duplicate_action_blocked",
                f"Action {tool_name}({arguments!r}) [{fingerprint}] is registered "
                "do-not-retry and was blocked before reaching the model.",
            )

    def check_code_syntax(self, code: str, language: str = "python") -> None:
        if language != "python":
            return
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise GuardrailViolation(
                "invalid_syntax",
                f"Rejected tool payload with invalid {language} syntax: {exc}",
            ) from exc

    def record_turn(self, tokens_used: int) -> None:
        self._turn_count += 1
        self._cumulative_tokens += tokens_used

    def check_circuit_breakers(self) -> None:
        cb = self.circuit_breaker
        if cb.max_turns is not None and self._turn_count >= cb.max_turns:
            raise GuardrailViolation(
                "turn_limit_exceeded",
                f"Conversation exceeded max_turns={cb.max_turns} "
                f"(current: {self._turn_count}).",
            )
        if (
            cb.max_cumulative_tokens is not None
            and self._cumulative_tokens >= cb.max_cumulative_tokens
        ):
            raise GuardrailViolation(
                "token_budget_exceeded",
                f"Conversation exceeded max_cumulative_tokens={cb.max_cumulative_tokens} "
                f"(current: {self._cumulative_tokens}).",
            )

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def cumulative_tokens(self) -> int:
        return self._cumulative_tokens

    def check_all(
        self,
        *,
        tool_name: str | None = None,
        arguments: dict | None = None,
        code: str | None = None,
        language: str = "python",
    ) -> None:
        """Run every applicable check; raises GuardrailViolation on the
        first failure. Call `check_circuit_breakers` separately after
        `record_turn` once per completed turn."""
        if tool_name is not None and arguments is not None:
            self.check_duplicate_action(tool_name, arguments)
        if code is not None:
            self.check_code_syntax(code, language)
        self.check_circuit_breakers()
