"""Semantic near-duplicate detection for tool observations.

Agent loops frequently re-run the same read-only tool call (e.g. `ls`,
`cat` on a file that hasn't changed, a repeated search) and get back
output that is identical or near-identical to something already in the
transcript. This module flags those cases so a caller can collapse the
repeat into a short pointer to the earlier occurrence instead of paying
for the tokens twice. Detection is lossless with respect to what is
kept: the *first* occurrence of any duplicate is always kept verbatim.

Similarity uses a cheap, dependency-free shingled-Jaccard estimate
rather than embeddings, so this module has no model dependency and no
network call -- it needs to run inline in the hot request path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def _shingles(text: str, k: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard_similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@dataclass
class DedupMatch:
    index: int
    matched_index: int
    similarity: float


class SemanticDeduplicator:
    def __init__(self, threshold: float = 0.9) -> None:
        self.threshold = threshold

    def find_duplicates(self, texts: list[str]) -> list[DedupMatch]:
        """For each text, finds the closest *earlier* text at or above
        `threshold`. Returns one DedupMatch per duplicate found (texts
        with no sufficiently-similar predecessor are omitted)."""
        matches: list[DedupMatch] = []
        for i, text in enumerate(texts):
            best_j, best_sim = -1, 0.0
            for j in range(i):
                sim = jaccard_similarity(text, texts[j])
                if sim > best_sim:
                    best_j, best_sim = j, sim
            if best_j >= 0 and best_sim >= self.threshold:
                matches.append(DedupMatch(index=i, matched_index=best_j, similarity=best_sim))
        return matches

    def collapse(self, texts: list[str]) -> list[str]:
        """Returns a copy of `texts` where each duplicate is replaced
        with a pointer to the earlier occurrence it matched."""
        matches = {m.index: m for m in self.find_duplicates(texts)}
        out = []
        for i, text in enumerate(texts):
            if i in matches:
                m = matches[i]
                out.append(f"[duplicate of turn {m.matched_index}, similarity={m.similarity:.2f}]")
            else:
                out.append(text)
        return out
