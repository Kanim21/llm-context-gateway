"""Tests for OpenAIAdapter.stream_chat_completions -- raw SSE passthrough
used for any OpenAI-wire-compatible upstream (OpenAI, DeepSeek, local
engines)."""

from __future__ import annotations

import httpx
import pytest

from agent_gateway.adapters.openai_adapter import OpenAIAdapter


class TestStreamChatCompletions:
    async def test_relays_sse_body_byte_for_byte(self):
        sse_body = (
            b'data: {"id":"1","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(base_url="https://api.openai.com/v1", api_key="sk-test")

        gen = await adapter.stream_chat_completions({"model": "gpt-4o", "messages": [], "stream": True}, client=client)
        relayed = b"".join([chunk async for chunk in gen])

        assert relayed == sse_body
        await client.aclose()

    async def test_sends_authorization_header(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(base_url="https://api.deepseek.com/v1", api_key="sk-deepseek")

        gen = await adapter.stream_chat_completions({"model": "deepseek-chat", "messages": []}, client=client)
        _ = [chunk async for chunk in gen]

        assert captured["headers"]["authorization"] == "Bearer sk-deepseek"
        await client.aclose()

    async def test_respects_custom_api_key_header(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(base_url="http://localhost:11434/v1", api_key="local-key",
                                 api_key_header="X-Custom-Key")

        gen = await adapter.stream_chat_completions({"model": "local-model", "messages": []}, client=client)
        _ = [chunk async for chunk in gen]

        assert captured["headers"]["x-custom-key"] == "local-key"
        assert "authorization" not in captured["headers"]
        await client.aclose()

    async def test_raises_before_yielding_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = OpenAIAdapter(base_url="https://api.openai.com/v1", api_key="bad-key")

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.stream_chat_completions({"model": "gpt-4o", "messages": []}, client=client)

        await client.aclose()
