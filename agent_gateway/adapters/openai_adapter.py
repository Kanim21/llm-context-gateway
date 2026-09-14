"""OpenAI upstream relay: /v1/chat/completions.

OpenAI's automatic prefix caching (as of the Chat Completions API)
requires no explicit cache-control markers -- it caches on exact
prefix match server-side. This adapter's job is therefore just to
relay the (already gateway-processed) request body unchanged and pass
through the response, while never mutating the frozen prefix portion
of `messages` that `cache_boundary.BoundaryGuard` is tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class OpenAIAdapter:
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    timeout_s: float = 60.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat_completions(self, body: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def extract_frozen_prefix(messages: list[dict[str, Any]], static_tool_schemas: list[dict] | None = None) -> str:
        """Canonical text of the frozen prefix: the leading run of
        `system` messages plus static tool schemas. Used to feed
        `cache_boundary.BoundaryGuard` -- must be identical in shape to
        `AnthropicAdapter.extract_frozen_prefix` so the two providers'
        boundary checks are comparable in tests."""
        import json

        parts = []
        for msg in messages:
            if msg.get("role") != "system":
                break
            parts.append(str(msg.get("content", "")))
        if static_tool_schemas:
            parts.append(json.dumps(static_tool_schemas, sort_keys=True))
        return "\n".join(parts)
