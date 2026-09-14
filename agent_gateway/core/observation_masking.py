"""Observation masking: keep raw tool output for trailing K turns only.

Spec module 2. Long agent loops re-send every prior tool observation on
every turn, which is the dominant driver of O(N^2) token growth. This
module preserves raw output for the trailing K=2 turns (configurable)
and collapses everything older into a short, deterministic receipt:

    [Step N: completed, M lines output, artifact ref://<hash>]

The full raw output is never discarded -- it is written to the artifact
store (see `agent_gateway.storage.sqlite_store`) and addressable by the
`ref://` id in the receipt, so a caller (or a later guardrail) can
explicitly re-hydrate it if the loop genuinely needs to re-read it. This
module only decides what goes into the *next prompt*, never deletes data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


def artifact_ref(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"ref://art-{digest}"


class ArtifactSink(Protocol):
    """Minimal interface this module needs from the artifact store."""

    def put(self, ref: str, content: str) -> None: ...


@dataclass
class Observation:
    """One tool-call observation in the running transcript."""

    step: int
    tool_name: str
    raw_output: str
    completed: bool = True


@dataclass
class MaskedObservation:
    step: int
    tool_name: str
    text: str
    is_receipt: bool
    ref: str | None


class ObservationMasker:
    """Collapses all but the trailing K observations into receipts.

    K counts *observations*, not wall-clock turns -- an "older" turn is
    simply one further than K steps back from the current end of the
    transcript. Receipts are pure functions of the observation, so
    masking the same transcript twice yields byte-identical receipts
    (required for cache-prefix stability across turns, see
    `cache_boundary.CacheBoundary`).
    """

    def __init__(self, trailing_k: int = 2, artifact_sink: ArtifactSink | None = None) -> None:
        if trailing_k < 0:
            raise ValueError("trailing_k must be >= 0")
        self.trailing_k = trailing_k
        self._artifact_sink = artifact_sink
        self._rehydrated_refs: set[str] = set()

    def mask(self, observations: list[Observation]) -> list[MaskedObservation]:
        n = len(observations)
        cutoff = max(0, n - self.trailing_k)
        results: list[MaskedObservation] = []
        for i, obs in enumerate(observations):
            if i >= cutoff:
                results.append(
                    MaskedObservation(
                        step=obs.step,
                        tool_name=obs.tool_name,
                        text=obs.raw_output,
                        is_receipt=False,
                        ref=None,
                    )
                )
            else:
                ref = artifact_ref(obs.raw_output)
                if self._artifact_sink is not None:
                    self._artifact_sink.put(ref, obs.raw_output)
                line_count = obs.raw_output.count("\n") + (1 if obs.raw_output else 0)
                status = "completed" if obs.completed else "incomplete"
                receipt = f"[Step {obs.step}: {status}, {line_count} lines output, artifact {ref}]"
                results.append(
                    MaskedObservation(
                        step=obs.step,
                        tool_name=obs.tool_name,
                        text=receipt,
                        is_receipt=True,
                        ref=ref,
                    )
                )
        return results

    def mark_rehydrated(self, ref: str) -> None:
        """Record that a receipt's artifact was explicitly re-read.

        Used by the benchmark runner to compute the offload hit rate:
        the fraction of collapsed receipts that are *never* rehydrated
        (i.e. the collapse was safe and saved tokens for free).
        """
        self._rehydrated_refs.add(ref)

    def was_rehydrated(self, ref: str) -> bool:
        return ref in self._rehydrated_refs
