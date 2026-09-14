"""End-to-end tests for /v1/chat/completions provider routing, using a
mocked httpx transport injected into GatewayState.http_client -- no real
network calls anywhere in this file."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_gateway.proxy.config import GatewayConfig, ProviderConfig, ProvidersConfig
from agent_gateway.proxy.server import _config_path_from_env, create_app


def _config_with_providers(**overrides) -> GatewayConfig:
    providers = ProvidersConfig(
        entries=[
            ProviderConfig(name="openai", wire_shape="openai_compatible",
                            base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
            ProviderConfig(name="deepseek", wire_shape="openai_compatible",
                            base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY"),
            ProviderConfig(name="gemini", wire_shape="gemini",
                            base_url="https://generativelanguage.googleapis.com/v1beta",
                            api_key_env="GEMINI_API_KEY"),
        ],
        model_routes={"deepseek-chat": "deepseek", "gemini-1.5-pro": "gemini"},
        default_provider="openai",
    )
    return GatewayConfig(providers=providers, **overrides)


def _client_with_mock_transport(config: GatewayConfig, handler) -> TestClient:
    app = create_app(config)
    client = TestClient(app)
    client.__enter__()  # trigger lifespan startup so app.state.gateway exists
    app.state.gateway.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class TestUnmappedModelRegression:
    def test_unmapped_model_still_hits_openai_base_url(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "1", "choices": [], "model": "gpt-4o"})

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 200
        assert captured["url"] == "https://api.openai.com/v1/chat/completions"
        client.__exit__(None, None, None)


class TestDeepSeekRouting:
    def test_deepseek_model_routes_to_deepseek_base_url_via_openai_adapter(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "1", "choices": [], "model": "deepseek-chat"})

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post("/v1/chat/completions", json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 200
        assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
        client.__exit__(None, None, None)


class TestGeminiToolCallRoundTrip:
    def test_gemini_model_with_tools_returns_openai_shaped_tool_calls(self):
        def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            assert sent["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [
                    {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
                ]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
            })

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "gemini-1.5-pro",
            "messages": [{"role": "user", "content": "Weather in Paris?"}],
            "tools": [{"type": "function", "function": {
                "name": "get_weather", "description": "Get weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            }}],
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["finish_reason"] == "tool_calls"
        assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"
        client.__exit__(None, None, None)


class TestGeminiStreaming:
    def test_gemini_streaming_produces_valid_openai_sse_ending_in_done(self):
        sse_body = (
            'data: {"candidates": [{"content": {"parts": [{"text": "Bonjour"}]}}]}\n\n'
            'data: {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}\n\n'
        ).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "gemini-1.5-pro",
            "messages": [{"role": "user", "content": "Say hi in French"}],
            "stream": True,
        })

        assert resp.status_code == 200
        assert resp.text.endswith("data: [DONE]\n\n")
        assert "Bonjour" in resp.text
        client.__exit__(None, None, None)


class TestBoundaryCheckIsProviderAgnostic:
    def test_mutated_prefix_on_gemini_routed_conversation_still_raises_409(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {},
            })

        client = _client_with_mock_transport(_config_with_providers(), handler)
        headers = {"x-conversation-id": "conv-1"}

        first = client.post("/v1/chat/completions", headers=headers, json={
            "model": "gemini-1.5-pro",
            "messages": [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "hi"}],
        })
        assert first.status_code == 200

        second = client.post("/v1/chat/completions", headers=headers, json={
            "model": "gemini-1.5-pro",
            "messages": [{"role": "system", "content": "Be verbose instead."}, {"role": "user", "content": "hi again"}],
        })
        assert second.status_code == 409
        client.__exit__(None, None, None)


class TestConfigLoading:
    def test_load_reads_providers_block_from_json_file(self, tmp_path):
        config_data = {
            "providers": {
                "entries": [
                    {"name": "openai", "wire_shape": "openai_compatible",
                     "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
                    {"name": "gemini", "wire_shape": "gemini",
                     "base_url": "https://generativelanguage.googleapis.com/v1beta",
                     "api_key_env": "GEMINI_API_KEY"},
                ],
                "model_routes": {"gemini-1.5-pro": "gemini"},
                "default_provider": "openai",
            }
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config_data))

        config = GatewayConfig.load(str(path))

        assert config.providers.model_routes == {"gemini-1.5-pro": "gemini"}
        assert any(e.name == "gemini" for e in config.providers.entries)

    def test_config_path_from_env_reads_env_var(self, monkeypatch, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{}")
        monkeypatch.setenv("AGENT_GATEWAY_CONFIG", str(path))

        assert _config_path_from_env() == str(path)


class TestGeminiMalformedClientInputReturns400:
    def test_tool_result_with_unmatched_tool_call_id_returns_400_not_500(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("upstream should never be called for malformed input")

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "gemini-1.5-pro",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "tool", "tool_call_id": "call_does_not_exist", "content": "{}"},
            ],
        })

        assert resp.status_code == 400
        client.__exit__(None, None, None)


class TestCredentialFallback:
    def test_server_env_var_used_when_client_sends_no_authorization(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-server-secret")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("authorization")
            return httpx.Response(200, json={"id": "1", "choices": [], "model": "gpt-4o"})

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 200
        assert captured["authorization"] == "Bearer sk-server-secret"
        client.__exit__(None, None, None)

    def test_client_authorization_header_overrides_server_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-server-secret")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("authorization")
            return httpx.Response(200, json={"id": "1", "choices": [], "model": "gpt-4o"})

        client = _client_with_mock_transport(_config_with_providers(), handler)
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-client-key"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert resp.status_code == 200
        assert captured["authorization"] == "Bearer sk-client-key"
        client.__exit__(None, None, None)
