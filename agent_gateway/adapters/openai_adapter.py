"""OpenAI upstream relay: /v1/chat/completions.

OpenAI's automatic prefix caching (as of the Chat Completions API)
requires no explicit cache-control markers -- it caches on exact
prefix match server-side. This adapter's job is therefore just to
relay the (already gateway-processed) request body unchanged and pass
through the response, while never mutating the frozen prefix portion
of `messages` that `cache_boundary.BoundaryGuard` is tracking.
Also serves DeepSeek and any other OpenAI-wire-compatible upstream (local Ollama/vLLM/llama.cpp/LM Studio servers) via the same relay logic, and provides a streaming variant for all of them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class OpenAIAdapter:
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    timeout_s: float = 60.0
    api_key_header: str = "Authorization"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            if self.api_key_header == "Authorization":
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                headers[self.api_key_header] = self.api_key
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

    async def stream_chat_completions(self, body: dict[str, Any], client: httpx.AsyncClient) -> AsyncIterator[bytes]:
        """Raw SSE passthrough for any OpenAI-wire-compatible upstream
        (OpenAI, DeepSeek, local engines) -- no transformation, since
        the wire shape already matches. Opens the connection and checks
        its status eagerly, so an upstream error raises here rather than
        after a 200 has already been sent downstream."""
        req = client.build_request(
            "POST", f"{self.base_url}/chat/completions", json=body, headers=self._headers()
        )
        resp = await client.send(req, stream=True)
        if not resp.is_success:
            await resp.aread()
            await resp.aclose()
            resp.raise_for_status()

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        return _iter()

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
