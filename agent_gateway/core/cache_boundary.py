"""Cache boundary verification and real tokenization.

Enforces Non-Negotiable Rule #1 (Cache Prefix Immutability, spec Sec 3.1):
the frozen prefix of a conversation (system instructions, static tool
schemas, few-shot examples) is hashed with SHA-256 before and after every
turn. If the hash changes, the prefix was mutated and provider prompt
caching (Anthropic ephemeral cache_control blocks, OpenAI automatic
prefix caching) silently breaks -- callers MUST treat a mismatch as a
hard error, not a warning.

Also enforces Non-Negotiable Rule #3 (Real Tokenization, spec Sec 1):
all claim-bearing token counts must come from a named tokenizer
(tiktoken cl100k_base for OpenAI, a Claude BPE vocab for Anthropic).
Character/byte counts are exposed only as a secondary, clearly-labeled
storage metric and must never be reported as "tokens".
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import tiktoken
from tokenizers import Tokenizer as _HFTokenizer

TokenizerName = Literal["cl100k_base", "claude-bpe"]

_VOCAB_DIR = Path(__file__).parent / "vocab"
_CLAUDE_VOCAB_PATH = _VOCAB_DIR / "claude-bpe-tokenizer.json"


class CachePrefixMutationError(RuntimeError):
    """Raised when the frozen cache prefix's SHA-256 checksum changes.

    This is a hard failure: a mutated prefix invalidates provider-side
    prompt caching for the rest of the run, silently reintroducing the
    O(N^2) cost/latency growth this proxy exists to prevent.
    """


def sha256_hexdigest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CacheBoundary:
    """An immutable snapshot of a conversation's frozen prefix.

    `prefix_text` is the canonical serialization (system prompt + static
    tool schemas + few-shot examples) that must stay byte-identical for
    provider prompt caching to hit. `checksum` is its SHA-256 hex digest,
    computed once at creation time.
    """

    prefix_text: str
    checksum: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "checksum", sha256_hexdigest(self.prefix_text))

    def verify(self, current_prefix_text: str) -> None:
        """Verify `current_prefix_text` still matches this boundary.

        Raises CachePrefixMutationError on any mismatch, including
        length changes, whitespace changes, or reordering -- prompt
        caches are exact-prefix-match, so any of these breaks the cache.
        """
        current_checksum = sha256_hexdigest(current_prefix_text)
        if current_checksum != self.checksum:
            raise CachePrefixMutationError(
                "Cache prefix mutated: expected checksum "
                f"{self.checksum}, got {current_checksum}. "
                "The frozen prefix (system instructions, static tool "
                "schemas, few-shot examples) must never change after "
                "the first turn."
            )


class BoundaryGuard:
    """Tracks a CacheBoundary per conversation and enforces immutability.

    One BoundaryGuard is created per proxy process; conversations are
    keyed by an opaque `conversation_id` supplied by the caller (e.g. a
    hash of the client-supplied session identifier).
    """

    def __init__(self) -> None:
        self._boundaries: dict[str, CacheBoundary] = {}
        self._lock = threading.Lock()

    def register_or_verify(self, conversation_id: str, prefix_text: str) -> CacheBoundary:
        """On first call for `conversation_id`, freezes the boundary.

        On every subsequent call, verifies `prefix_text` still matches
        the frozen boundary (raising CachePrefixMutationError if not)
        and returns the original boundary.
        """
        with self._lock:
            existing = self._boundaries.get(conversation_id)
            if existing is None:
                boundary = CacheBoundary(prefix_text=prefix_text)
                self._boundaries[conversation_id] = boundary
                return boundary
            existing.verify(prefix_text)
            return existing

    def forget(self, conversation_id: str) -> None:
        with self._lock:
            self._boundaries.pop(conversation_id, None)

    def __len__(self) -> int:
        return len(self._boundaries)


class TokenizerEngine:
    """Real, named tokenizers -- never a character-count proxy.

    cl100k_base: exact, via OpenAI's `tiktoken` (offline, official vocab).

    claude-bpe: Anthropic's SDK no longer ships a local tokenizer (token
    counting moved to the `/v1/messages/count_tokens` API), so this uses
    the community-mirrored BPE vocab (`Xenova/claude-tokenizer`, bundled
    at `core/vocab/claude-bpe-tokenizer.json`) that reproduces Claude's
    tokenization closely offline. This is a close approximation, not the
    literal production vocabulary -- it must never be described as
    "exact" in reports. When `exact_claude_counter` is supplied (e.g. a
    callback wrapping the live count_tokens API), it is preferred when
    available.
    """

    def __init__(self) -> None:
        self._cl100k = tiktoken.get_encoding("cl100k_base")
        self._claude_bpe: _HFTokenizer | None = None
        self._exact_claude_counter = None

    def set_exact_claude_counter(self, fn) -> None:
        """Register a callback `str -> int` for exact Anthropic API counts."""
        self._exact_claude_counter = fn

    def _claude_tokenizer(self) -> _HFTokenizer:
        if self._claude_bpe is None:
            if not _CLAUDE_VOCAB_PATH.exists():
                raise FileNotFoundError(
                    f"Claude BPE vocab not found at {_CLAUDE_VOCAB_PATH}. "
                    "This proxy requires a named tokenizer for all "
                    "claim-bearing measurements; character counts alone "
                    "are not an acceptable substitute."
                )
            self._claude_bpe = _HFTokenizer.from_file(str(_CLAUDE_VOCAB_PATH))
        return self._claude_bpe

    def count(self, text: str, tokenizer: TokenizerName) -> int:
        if tokenizer == "cl100k_base":
            return len(self._cl100k.encode(text, disallowed_special=()))
        if tokenizer == "claude-bpe":
            if self._exact_claude_counter is not None:
                return self._exact_claude_counter(text)
            return len(self._claude_tokenizer().encode(text).ids)
        raise ValueError(f"Unknown tokenizer: {tokenizer!r}")

    @staticmethod
    def char_count(text: str) -> int:
        """Secondary storage metric only. Never report this as 'tokens'."""
        return len(text)


_default_engine: TokenizerEngine | None = None
_default_engine_lock = threading.Lock()


def get_default_tokenizer_engine() -> TokenizerEngine:
    global _default_engine
    with _default_engine_lock:
        if _default_engine is None:
            _default_engine = TokenizerEngine()
        return _default_engine
