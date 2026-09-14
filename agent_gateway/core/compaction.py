"""Prefix-preserving hierarchical suffix summarization.

Cache Prefix Immutability (spec Sec 3.1) means the frozen prefix can
never be touched. So when a conversation crosses a token threshold,
compaction only ever rewrites the *suffix* -- everything after the
frozen prefix -- collapsing older turns into a hierarchy of summaries
while leaving the most recent `keep_recent_turns` turns verbatim.

"Hierarchical" means summaries themselves get re-summarized: once the
suffix (summaries + recent turns) crosses the threshold again, the
oldest summaries are merged into a coarser summary one level up, so the
compacted history grows logarithmically rather than linearly with the
number of compaction events.

The actual text summarization is pluggable (`Summarizer` protocol) --
this module owns *when* and *what* to summarize, not the summarization
model call itself. A deterministic extractive fallback is provided for
offline/test use; production wiring should pass a callback that routes
through `routing.py` to a Tier-2 model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agent_gateway.core.cache_boundary import CacheBoundary, TokenizerEngine


class Summarizer(Protocol):
    def __call__(self, turns_text: str, level: int) -> str: ...


_MAX_WORDS_PER_BULLET = 20


def extractive_fallback_summarizer(turns_text: str, level: int) -> str:
    """Deterministic, model-free summarizer: keeps the first and last
    sentence of each paragraph, truncating any single long run-on
    "sentence" to its leading words. Good enough for tests and for a
    proxy running with no Tier-2 model configured; not a substitute
    for real LLM summarization in production."""
    paragraphs = [p.strip() for p in turns_text.split("\n\n") if p.strip()]
    out_lines = [f"[compacted summary L{level}, {len(paragraphs)} paragraph(s)]"]
    for para in paragraphs:
        sentences = [s.strip() for s in para.replace("\n", " ").split(". ") if s.strip()]
        if not sentences:
            continue
        if len(sentences) == 1:
            out_lines.append(f"- {_truncate_words(sentences[0], _MAX_WORDS_PER_BULLET)}")
        else:
            out_lines.append(
                f"- {_truncate_words(sentences[0], _MAX_WORDS_PER_BULLET)}. "
                f"... {_truncate_words(sentences[-1], _MAX_WORDS_PER_BULLET)}"
            )
    return "\n".join(out_lines)


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + f" ...({len(words)} words total)"


@dataclass
class SuffixTurn:
    index: int
    text: str
    is_summary: bool = False
    level: int = 0


@dataclass
class CompactionResult:
    triggered: bool
    prefix_text: str
    suffix_turns: list[SuffixTurn]
    tokens_before: int
    tokens_after: int

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


@dataclass
class HierarchicalCompactor:
    """Owns compaction for one conversation.

    `token_threshold` is measured over the *suffix only* (the frozen
    prefix never counts against the budget, since it's cached and
    effectively free after the first turn). `keep_recent_turns` verbatim
    turns are never summarized, matching observation_masking's trailing
    window so raw content and receipts stay consistent.
    """

    tokenizer_engine: TokenizerEngine
    tokenizer_name: str = "cl100k_base"
    token_threshold: int = 4000
    keep_recent_turns: int = 4
    summarizer: Summarizer = field(default=extractive_fallback_summarizer)
    boundary: CacheBoundary | None = field(default=None)

    def _count(self, text: str) -> int:
        return self.tokenizer_engine.count(text, self.tokenizer_name)  # type: ignore[arg-type]

    def maybe_compact(self, prefix_text: str, suffix_turns: list[SuffixTurn]) -> CompactionResult:
        if self.boundary is not None:
            self.boundary.verify(prefix_text)

        tokens_before = sum(self._count(t.text) for t in suffix_turns)
        if tokens_before <= self.token_threshold or len(suffix_turns) <= self.keep_recent_turns:
            return CompactionResult(
                triggered=False,
                prefix_text=prefix_text,
                suffix_turns=suffix_turns,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        recent = suffix_turns[-self.keep_recent_turns :]
        older = suffix_turns[: -self.keep_recent_turns]

        summary_groups: dict[int, list[SuffixTurn]] = {}
        for turn in older:
            summary_groups.setdefault(turn.level, []).append(turn)

        new_older: list[SuffixTurn] = []
        for level in sorted(summary_groups):
            group = summary_groups[level]
            merged_text = "\n\n".join(t.text for t in group)
            summary_text = self.summarizer(merged_text, level + 1)
            new_older.append(
                SuffixTurn(
                    index=group[0].index,
                    text=summary_text,
                    is_summary=True,
                    level=level + 1,
                )
            )

        new_suffix = new_older + recent
        tokens_after = sum(self._count(t.text) for t in new_suffix)

        return CompactionResult(
            triggered=True,
            prefix_text=prefix_text,
            suffix_turns=new_suffix,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )
