"""Branch collapse: fold abandoned exploration paths into one outcome.

Agent loops often try approach A, hit a dead end, backtrack, then try
approach B that succeeds. Once B succeeds, the full transcript of A
(every intermediate tool call and observation) is rarely needed again
-- what matters going forward is that A was tried and why it failed.
This module collapses a finished, abandoned branch into a single
receipt line, analogous to `observation_masking`'s receipts but scoped
to a whole sequence of turns rather than one observation.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_gateway.core.observation_masking import artifact_ref


@dataclass
class Branch:
    label: str
    turns_text: str
    abandoned: bool
    outcome: str = ""


@dataclass
class CollapsedBranch:
    label: str
    receipt: str
    ref: str


class BranchCollapser:
    def __init__(self, artifact_sink=None) -> None:
        self._artifact_sink = artifact_sink

    def collapse(self, branch: Branch) -> CollapsedBranch:
        ref = artifact_ref(branch.turns_text)
        if self._artifact_sink is not None:
            self._artifact_sink.put(ref, branch.turns_text)
        status = "abandoned" if branch.abandoned else "completed"
        outcome = f": {branch.outcome}" if branch.outcome else ""
        receipt = f"[Branch '{branch.label}' {status}{outcome}, full transcript {ref}]"
        return CollapsedBranch(label=branch.label, receipt=receipt, ref=ref)

    def collapse_all(self, branches: list[Branch]) -> list[CollapsedBranch]:
        return [self.collapse(b) for b in branches]
