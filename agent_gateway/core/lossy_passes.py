"""Lossy passes: structural stripping, eco trimming, pruning, numeric
quantization, and query filtering.

Non-negotiable rule (spec Sec 3.3): every pass in this module is lossy
-- it can change facts, numbers, dates, or negations in the text -- so
every one of them defaults to OFF (`LossyPassConfig()` with all flags
False and aggression at NONE runs as a no-op passthrough). A caller
must explicitly opt in per pass and pick an aggression tier; there is
no global "just make it smaller" switch that silently enables these.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum


class Aggression(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class LossyPassConfig:
    structural_stripping: bool = False
    eco_trimming: bool = False
    pruning: bool = False
    numeric_quantization: bool = False
    query_filtering: bool = False
    aggression: Aggression = Aggression.NONE

    def any_enabled(self) -> bool:
        return any(
            [
                self.structural_stripping,
                self.eco_trimming,
                self.pruning,
                self.numeric_quantization,
                self.query_filtering,
            ]
        )


_FILLER_WORDS = {
    Aggression.LOW: {"basically", "actually", "just", "really"},
    Aggression.MEDIUM: {"basically", "actually", "just", "really", "very", "quite", "simply"},
    Aggression.HIGH: {
        "basically", "actually", "just", "really", "very", "quite", "simply",
        "in order to", "it should be noted that", "please note that",
    },
}


def structural_stripping(text: str, aggression: Aggression) -> str:
    """Removes markdown/HTML structural decoration (headers, bold,
    emphasis markers, HTML tags) while leaving the underlying words
    intact. Lossy: layout and emphasis information is discarded."""
    if aggression is Aggression.NONE:
        return text
    out = re.sub(r"<[^>]+>", "", text)
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.MULTILINE)
    out = re.sub(r"(\*\*|__|\*|_|`)", "", out)
    if aggression >= Aggression.MEDIUM:
        out = re.sub(r"^[-*+]\s+", "", out, flags=re.MULTILINE)
    return out


def eco_trimming(text: str, aggression: Aggression) -> str:
    """Strips filler words/phrases scaled by aggression tier. Lossy:
    can alter emphasis and, at HIGH, remove hedging language that
    changes the perceived certainty of a claim."""
    if aggression is Aggression.NONE:
        return text
    words = _FILLER_WORDS.get(aggression, set())
    out = text
    for phrase in sorted(words, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(phrase)}\b\s*", "", out, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", out)


def pruning(text: str, aggression: Aggression, keep_ratio_by_tier: dict[Aggression, float] | None = None) -> str:
    """Drops whole sentences, keeping only a leading fraction per
    paragraph. Lossy: can drop facts, numbers, dates, or negations
    that happen to live in a dropped sentence -- this is the most
    aggressive pass in this module."""
    if aggression is Aggression.NONE:
        return text
    ratios = keep_ratio_by_tier or {Aggression.LOW: 0.8, Aggression.MEDIUM: 0.6, Aggression.HIGH: 0.4}
    ratio = ratios.get(aggression, 1.0)
    paragraphs = text.split("\n\n")
    kept_paragraphs = []
    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        sentences = [s for s in sentences if s]
        if not sentences:
            kept_paragraphs.append(para)
            continue
        n_keep = max(1, int(len(sentences) * ratio))
        kept_paragraphs.append(" ".join(sentences[:n_keep]))
    return "\n\n".join(kept_paragraphs)


_NUMBER_RE = re.compile(r"-?\d+\.\d+")


def numeric_quantization(text: str, aggression: Aggression) -> str:
    """Rounds floating-point numbers to a coarser precision. Lossy by
    definition: 3.14159 -> 3.1 is not the same number, so any
    faithfulness check on exact numeric values must treat this pass as
    a known-lossy transform, not a bug in the checker."""
    if aggression is Aggression.NONE:
        return text
    decimals = {Aggression.LOW: 2, Aggression.MEDIUM: 1, Aggression.HIGH: 0}[aggression]

    def _round(match: re.Match) -> str:
        value = float(match.group(0))
        if decimals == 0:
            return str(int(round(value)))
        return f"{value:.{decimals}f}"

    return _NUMBER_RE.sub(_round, text)


_STOPWORDS = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are"}


def query_filtering(text: str, query: str, aggression: Aggression) -> str:
    """Keeps only sentences relevant to `query` (keyword overlap).
    Lossy: sentences with no keyword overlap are dropped outright,
    even if they contain facts a later turn needs."""
    if aggression is Aggression.NONE or not query.strip():
        return text
    keywords = {w.lower() for w in re.findall(r"\w+", query) if w.lower() not in _STOPWORDS}
    if not keywords:
        return text
    min_overlap = {Aggression.LOW: 1, Aggression.MEDIUM: 1, Aggression.HIGH: 2}[aggression]
    paragraphs = text.split("\n\n")
    kept = []
    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        keep_sentences = []
        for s in sentences:
            s_words = {w.lower() for w in re.findall(r"\w+", s)}
            if len(s_words & keywords) >= min_overlap:
                keep_sentences.append(s)
        if keep_sentences:
            kept.append(" ".join(keep_sentences))
    return "\n\n".join(kept) if kept else text


def apply_lossy_passes(text: str, config: LossyPassConfig, query: str = "") -> str:
    """Applies enabled passes in a fixed, documented order. All passes
    default off via `LossyPassConfig()`, per spec Sec 3.3."""
    out = text
    if config.structural_stripping:
        out = structural_stripping(out, config.aggression)
    if config.eco_trimming:
        out = eco_trimming(out, config.aggression)
    if config.pruning:
        out = pruning(out, config.aggression)
    if config.numeric_quantization:
        out = numeric_quantization(out, config.aggression)
    if config.query_filtering:
        out = query_filtering(out, query, config.aggression)
    return out
