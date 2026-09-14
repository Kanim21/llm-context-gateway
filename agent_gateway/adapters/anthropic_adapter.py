"""Anthropic upstream relay: /v1/messages, with ephemeral prompt caching.

Unlike OpenAI, Anthropic's prompt caching is opt-in and explicit: a
content block must carry `"cache_control": {"type": "ephemeral"}` to be
eligible. This adapter marks the frozen prefix (system prompt blocks
and static tool schemas) with ephemeral cache_control breakpoints, and
leaves everything after the frozen prefix unmarked so it is never
mistakenly cached as if it were stable.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import httpx

ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class AnthropicAdapter:
    base_url: str = "https://api.anthropic.com/v1"
    api_key: str | None = None
    timeout_s: float = 60.0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def messages(self, body: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            resp = await client.post(
                f"{self.base_url}/messages",
                json=body,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def apply_ephemeral_cache_control(body: dict[str, Any], tools_are_static: bool = True) -> dict[str, Any]:
        """Returns a copy of `body` with cache_control breakpoints on
        the frozen prefix only: the last block of the system prompt,
        and the last static tool schema (if `tools_are_static`).
        Anthropic charges for cache writes but discounts cache reads,
        so breakpoints should sit at the END of the frozen region --
        marking anything in the mutable suffix would either fail to
        cache (content changes every turn) or waste a write on content
        that will never be read back."""
        out = copy.deepcopy(body)

        system = out.get("system")
        if isinstance(system, str) and system:
            out["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(system, list) and system:
            system[-1] = {**system[-1], "cache_control": {"type": "ephemeral"}}

        tools = out.get("tools")
        if tools_are_static and isinstance(tools, list) and tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

        return out

    @staticmethod
    def extract_frozen_prefix(body: dict[str, Any]) -> str:
        """Canonical text of the frozen prefix, mirroring
        `OpenAIAdapter.extract_frozen_prefix` in shape so both
        providers' boundary checks are comparable in tests."""
        import json

        parts = []
        system = body.get("system")
        if isinstance(system, str):
            parts.append(system)
        elif isinstance(system, list):
            for block in system:
                parts.append(str(block.get("text", "")))
        tools = body.get("tools")
        if tools:
            parts.append(json.dumps(tools, sort_keys=True))
        return "\n".join(parts)
