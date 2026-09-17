"""Tests for GatewayCompletionFn: dispatches a playbook step's prompt
through the existing multi-provider adapters, using httpx.MockTransport --
no real network calls."""

from __future__ import annotations

import httpx

from agent_gateway.core.provider_routing import ProviderRegistry
from agent_gateway.orchestrator.engine import GatewayCompletionFn
from agent_gateway.proxy.config import ProviderConfig, ProvidersConfig


def _registry():
    return ProviderRegistry(ProvidersConfig(
        entries=[
            ProviderConfig(name="openai", wire_shape="openai_compatible",
                            base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
            ProviderConfig(name="gemini", wire_shape="gemini",
                            base_url="https://generativelanguage.googleapis.com/v1beta",
                            api_key_env="GEMINI_API_KEY"),
        ],
        model_routes={"gemini-1.5-pro": "gemini"},
        default_provider="openai",
    ))


class TestGatewayCompletionFnOpenAI:
    async def test_streams_openai_response_and_accumulates_tokens(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        sse_body = (
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n'
            b'data: [DONE]\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fn = GatewayCompletionFn(provider_registry=_registry(), http_client=http_client)

        tokens = []
        async def on_token(token):
            tokens.append(token)

        result = await fn(model="gpt-4o-mini", prompt="Say hello", on_token=on_token)

        assert result == "Hello world"
        assert tokens == ["Hello", " world"]


class TestGatewayCompletionFnGemini:
    async def test_streams_gemini_response_translated_to_openai_shape(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "sk-test")
        sse_body = (
            'data: {"candidates": [{"content": {"parts": [{"text": "Bonjour"}]}}]}\n\n'
            'data: {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}\n\n'
        ).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fn = GatewayCompletionFn(provider_registry=_registry(), http_client=http_client)

        tokens = []
        async def on_token(token):
            tokens.append(token)

        result = await fn(model="gemini-1.5-pro", prompt="Say hi in French", on_token=on_token)

        assert result == "Bonjour"
        assert tokens == ["Bonjour"]
