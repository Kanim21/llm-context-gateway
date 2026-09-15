# Multi-Provider Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `/v1/chat/completions` to transparently route OpenAI-shaped requests to OpenAI, DeepSeek, local OpenAI-compatible servers (all passthrough), and Gemini (translated), with server-side credentials, streaming, and tool-call support — without touching `/v1/messages`.

**Architecture:** A pure-logic `ProviderRegistry` (in `core/provider_routing.py`) resolves `model` → `ProviderConfig`; the `/v1/chat/completions` handler branches on `provider.wire_shape`. `openai_compatible` providers reuse `OpenAIAdapter` unchanged (new streaming method added). `gemini` requests go through a new `GeminiAdapter` plus pure translation functions in `adapters/gemini_adapter.py`. The cache-boundary check always runs against the canonical OpenAI-shaped body first, before any provider branching, so it stays provider-agnostic.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, httpx (async, `httpx.MockTransport` for tests), pytest + pytest-asyncio (`asyncio_mode = "auto"`, already configured).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-provider-routing-design.md`

## Global Constraints

- No real network calls or live API keys anywhere in the test suite (spec §9). All HTTP is via `httpx.MockTransport`.
- `/v1/messages`, `anthropic_adapter.py`, `cache_boundary.py`, `metrics.py`, and every `core/` module other than the new `provider_routing.py` are unchanged (spec §2, §8).
- An unmapped `model` must keep hitting real OpenAI unchanged — this is a regression test, not just a default (spec §4, §9).
- Three approved wire-detail clarifications (from spec review) are mandatory, not optional:
  1. OpenAI `system` messages map to Gemini's top-level `systemInstruction`, never a `contents` turn.
  2. `functionResponse.response` must always be a JSON object. If the OpenAI tool message's `content` is not itself a JSON object (bare string, JSON array/scalar, or unparseable text), wrap it as `{"result": content}`.
  3. `finish_reason` is `"tool_calls"` whenever Gemini emits a `functionCall` part, **except** `MAX_TOKENS` outranks it (a truncated response reports `"length"` even if a partial function call is present).
- **Field-casing clarification (resolving an inconsistency in the approved spec's prose):** all Gemini wire field names use Google's actual documented REST casing throughout the implementation — `systemInstruction`, `toolConfig`, `functionCallingConfig`, `allowedFunctionNames`, `functionDeclarations`, `functionCall`, `functionResponse`. The spec prose mixed this with snake_case (`tool_config`, `function_calling_config`) in a few places; camelCase is authoritative since it's what a live Gemini endpoint actually requires.
- Tool-call function names are recovered by scanning the current request's own `messages` array (no persistent registry) — spec §5.
- Every new pure-logic function lives with no I/O dependency so it is unit-testable directly (spec §5–§7, §9).

---

## Task 1: Provider config schema

**Files:**
- Modify: `agent_gateway/proxy/config.py`
- Test: `tests/test_provider_routing.py` (new file)

**Interfaces:**
- Produces: `ProviderConfig` (fields: `name: str`, `wire_shape: Literal["openai_compatible", "gemini"]`, `base_url: str`, `api_key_env: str | None`, `api_key_header: str | None`), `ProvidersConfig` (fields: `entries: list[ProviderConfig]`, `model_routes: dict[str, str]`, `default_provider: str`), `GatewayConfig.providers: ProvidersConfig`. `UpstreamConfig` no longer has `openai_base_url`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_provider_routing.py`:

```python
"""Tests for provider config schema, ProviderRegistry, and credential
resolution (agent_gateway.core.provider_routing)."""

from __future__ import annotations

from agent_gateway.proxy.config import GatewayConfig, ProviderConfig, ProvidersConfig


class TestProvidersConfig:
    def test_default_entries_has_openai_only(self):
        config = ProvidersConfig()
        assert len(config.entries) == 1
        assert config.entries[0].name == "openai"
        assert config.entries[0].wire_shape == "openai_compatible"
        assert config.entries[0].base_url == "https://api.openai.com/v1"
        assert config.entries[0].api_key_env == "OPENAI_API_KEY"

    def test_default_provider_is_openai(self):
        assert ProvidersConfig().default_provider == "openai"

    def test_gateway_config_has_providers(self):
        config = GatewayConfig()
        assert isinstance(config.providers, ProvidersConfig)

    def test_upstream_config_no_longer_has_openai_base_url(self):
        assert not hasattr(GatewayConfig().upstream, "openai_base_url")

    def test_provider_config_accepts_gemini_wire_shape(self):
        provider = ProviderConfig(
            name="gemini", wire_shape="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key_env="GEMINI_API_KEY",
        )
        assert provider.wire_shape == "gemini"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_routing.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProviderConfig' from 'agent_gateway.proxy.config'`

- [ ] **Step 3: Write minimal implementation**

In `agent_gateway/proxy/config.py`, add `Literal` to the existing `pydantic` import line's neighbor import:

```python
from typing import Literal

from pydantic import BaseModel, Field
```

Replace the `UpstreamConfig` class (currently lines 8-11):

```python
class UpstreamConfig(BaseModel):
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    request_timeout_s: float = 60.0
```

Add, directly after `UpstreamConfig`:

```python
class ProviderConfig(BaseModel):
    name: str
    wire_shape: Literal["openai_compatible", "gemini"]
    base_url: str
    api_key_env: str | None = None
    api_key_header: str | None = None


class ProvidersConfig(BaseModel):
    entries: list[ProviderConfig] = Field(
        default_factory=lambda: [
            ProviderConfig(
                name="openai",
                wire_shape="openai_compatible",
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
            ),
        ]
    )
    model_routes: dict[str, str] = Field(default_factory=dict)
    default_provider: str = "openai"
```

In `GatewayConfig`, add the field (alongside `upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)`):

```python
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provider_routing.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/proxy/config.py tests/test_provider_routing.py
git commit -m "feat: add ProviderConfig/ProvidersConfig schema for multi-provider routing"
```

---

## Task 2: ProviderRegistry.resolve()

**Files:**
- Create: `agent_gateway/core/provider_routing.py`
- Test: `tests/test_provider_routing.py`

**Interfaces:**
- Consumes: `ProviderConfig`, `ProvidersConfig` from Task 1 (`agent_gateway.proxy.config`).
- Produces: `ProviderRegistry` (dataclass, field `config: ProvidersConfig`), method `resolve(model: str) -> ProviderConfig`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provider_routing.py`:

```python
import pytest

from agent_gateway.core.provider_routing import ProviderRegistry


class TestProviderRegistry:
    def _config(self) -> ProvidersConfig:
        return ProvidersConfig(
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

    def test_explicit_route_wins(self):
        registry = ProviderRegistry(self._config())
        assert registry.resolve("gemini-1.5-pro").name == "gemini"

    def test_unmapped_model_falls_back_to_default_provider(self):
        registry = ProviderRegistry(self._config())
        resolved = registry.resolve("gpt-4o")
        assert resolved.name == "openai"

    def test_unmapped_model_falls_back_even_for_unknown_name(self):
        registry = ProviderRegistry(self._config())
        assert registry.resolve("some-future-model-nobody-mapped-yet").name == "openai"

    def test_resolve_raises_if_routed_provider_has_no_entry(self):
        config = ProvidersConfig(
            entries=[ProviderConfig(name="openai", wire_shape="openai_compatible",
                                     base_url="https://api.openai.com/v1")],
            model_routes={"foo": "not-configured"},
        )
        registry = ProviderRegistry(config)
        with pytest.raises(ValueError, match="not-configured"):
            registry.resolve("foo")
```

Add the needed import at the top of the test file: `from agent_gateway.proxy.config import GatewayConfig, ProviderConfig, ProvidersConfig` (already present from Task 1 — just confirm `ProviderConfig`/`ProvidersConfig` are imported; they are).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.core.provider_routing'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/core/provider_routing.py`:

```python
"""Provider resolution and credential handling for multi-provider routing.

Pure logic, no I/O -- mirrors core/routing.py's pattern. The actual HTTP
dispatch happens in adapters/.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_gateway.proxy.config import ProviderConfig, ProvidersConfig


@dataclass
class ProviderRegistry:
    config: ProvidersConfig

    def resolve(self, model: str) -> ProviderConfig:
        """Explicit `model_routes` entry wins; otherwise falls back to
        `default_provider` -- this is exactly today's implicit behavior,
        so any existing caller with an unmapped model keeps hitting real
        OpenAI unchanged."""
        provider_name = self.config.model_routes.get(model, self.config.default_provider)
        for entry in self.config.entries:
            if entry.name == provider_name:
                return entry
        raise ValueError(
            f"Provider {provider_name!r} (resolved for model {model!r}) "
            "has no matching entry in providers.entries"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provider_routing.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/core/provider_routing.py tests/test_provider_routing.py
git commit -m "feat: add ProviderRegistry.resolve() for model-to-provider routing"
```

---

## Task 3: Credential resolution

**Files:**
- Modify: `agent_gateway/core/provider_routing.py`
- Test: `tests/test_provider_routing.py`

**Interfaces:**
- Consumes: `ProviderConfig` from Task 1.
- Produces: `resolve_credential(provider: ProviderConfig, request_headers: dict[str, str]) -> str | None`. (The header-name/style mapping described in spec §4 — `provider.api_key_header` if set, else a `wire_shape` default — is applied by the adapters themselves in Tasks 8-9, via an `api_key_header` field each adapter accepts; see Task 10 for how `server.py` passes it through.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provider_routing.py`:

```python
from agent_gateway.core.provider_routing import resolve_credential


class TestResolveCredential:
    def _provider(self, **overrides) -> ProviderConfig:
        defaults = dict(name="openai", wire_shape="openai_compatible",
                         base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY")
        defaults.update(overrides)
        return ProviderConfig(**defaults)

    def test_client_authorization_header_wins(self):
        provider = self._provider()
        headers = {"authorization": "Bearer sk-client-supplied"}
        assert resolve_credential(provider, headers) == "sk-client-supplied"

    def test_falls_back_to_env_var_when_no_client_header(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-server-side")
        provider = self._provider()
        assert resolve_credential(provider, {}) == "sk-server-side"

    def test_returns_none_when_neither_present(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = self._provider()
        assert resolve_credential(provider, {}) is None

    def test_no_api_key_env_configured_returns_none_without_header(self):
        provider = self._provider(api_key_env=None)
        assert resolve_credential(provider, {}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_routing.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_credential'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent_gateway/core/provider_routing.py`:

```python
import os


def resolve_credential(provider: ProviderConfig, request_headers: dict[str, str]) -> str | None:
    """Client `Authorization: Bearer <key>` header wins if present;
    otherwise falls back to the server-side env var configured for this
    provider. Returns None if neither is available."""
    client_key = request_headers.get("authorization", "").removeprefix("Bearer ").strip()
    if client_key:
        return client_key
    if provider.api_key_env:
        return os.environ.get(provider.api_key_env)
    return None
```

Move the `import os` to the top of the file with the other imports (before `from dataclasses import dataclass`), rather than leaving it mid-file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provider_routing.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/core/provider_routing.py tests/test_provider_routing.py
git commit -m "feat: add server-side credential resolution with client-header override"
```

---

## Task 4: Gemini request translation — plain text turns

**Files:**
- Create: `agent_gateway/adapters/gemini_adapter.py`
- Test: `tests/test_gemini_adapter.py` (new file)

**Interfaces:**
- Produces: `translate_request(body: dict[str, Any]) -> dict[str, Any]` (text-turn and system-instruction handling only at this step).

- [ ] **Step 1: Write the failing test**

Create `tests/test_gemini_adapter.py`:

```python
"""Tests for OpenAI<->Gemini translation (agent_gateway.adapters.gemini_adapter)."""

from __future__ import annotations

from agent_gateway.adapters.gemini_adapter import translate_request


class TestTranslateRequestTextTurns:
    def test_system_message_becomes_top_level_system_instruction(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi"},
        ]}
        result = translate_request(body)
        assert result["systemInstruction"] == {"parts": [{"text": "You are a helpful assistant."}]}
        assert all(c.get("role") != "system" for c in result["contents"])

    def test_no_system_message_omits_system_instruction(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "Hi"}]}
        result = translate_request(body)
        assert "systemInstruction" not in result

    def test_user_turn_maps_to_role_user(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "Hello"}]}
        result = translate_request(body)
        assert result["contents"] == [{"role": "user", "parts": [{"text": "Hello"}]}]

    def test_assistant_turn_maps_to_role_model(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]}
        result = translate_request(body)
        assert result["contents"][1] == {"role": "model", "parts": [{"text": "Hi there"}]}

    def test_multiple_system_messages_join_with_newline(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Hi"},
        ]}
        result = translate_request(body)
        assert result["systemInstruction"] == {"parts": [{"text": "Rule 1.\nRule 2."}]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_gateway.adapters.gemini_adapter'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_gateway/adapters/gemini_adapter.py`:

```python
"""Bidirectional translation between OpenAI's /v1/chat/completions wire
shape and Google Gemini's generateContent/streamGenerateContent shape,
plus the I/O adapter that calls Gemini.

All translation functions are pure (no I/O) so they're unit-testable
directly. Field names use Gemini's actual documented REST casing
(systemInstruction, toolConfig, functionCallingConfig,
allowedFunctionNames, functionDeclarations, functionCall,
functionResponse) throughout.
"""

from __future__ import annotations

from typing import Any


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    """Translates an OpenAI-shaped /v1/chat/completions body into a
    Gemini generateContent-shaped body."""
    messages = body.get("messages", [])
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(msg.get("content") or "")
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": msg.get("content") or ""}]})
        else:  # "user"
            contents.append({"role": "user", "parts": [{"text": msg.get("content") or ""}]})

    gemini_body: dict[str, Any] = {"contents": contents}
    if system_parts:
        gemini_body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}

    return gemini_body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/adapters/gemini_adapter.py tests/test_gemini_adapter.py
git commit -m "feat: add Gemini request translation for plain text turns"
```

---

## Task 5: Gemini request translation — tools and tool-call turns

**Files:**
- Modify: `agent_gateway/adapters/gemini_adapter.py`
- Test: `tests/test_gemini_adapter.py`

**Interfaces:**
- Consumes: `translate_request` scaffold from Task 4.
- Produces: `translate_request` now also handles `role: "assistant"` with `tool_calls`, `role: "tool"`, `tools`, and `tool_choice`. New internal helpers: `_translate_assistant_tool_call_turn`, `_translate_tool_result_turn`, `_lookup_tool_call_name`, `_wrap_tool_response`, `_sanitize_json_schema`, `_translate_tool_choice`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gemini_adapter.py`:

```python
import pytest

from agent_gateway.adapters.gemini_adapter import _sanitize_json_schema


class TestTranslateRequestToolCalls:
    def test_assistant_tool_call_turn_becomes_function_call_part(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
            ]},
        ]}
        result = translate_request(body)
        assert result["contents"][1] == {
            "role": "model",
            "parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}],
        }

    def test_tool_result_turn_recovers_name_from_same_request_history(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"temp_c": 18}'},
        ]}
        result = translate_request(body)
        assert result["contents"][2] == {
            "role": "user",
            "parts": [{"functionResponse": {"name": "get_weather", "response": {"temp_c": 18}}}],
        }

    def test_tool_result_with_bare_string_content_is_wrapped_in_result_object(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "ping", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "pong"},
        ]}
        result = translate_request(body)
        response = result["contents"][1]["parts"][0]["functionResponse"]["response"]
        assert response == {"result": "pong"}

    def test_tool_result_with_json_array_content_is_wrapped_in_result_object(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "list_items", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "[1, 2, 3]"},
        ]}
        result = translate_request(body)
        response = result["contents"][1]["parts"][0]["functionResponse"]["response"]
        assert response == {"result": [1, 2, 3]}

    def test_tool_result_with_json_object_content_passes_through_unwrapped(self):
        body = {"model": "gemini-1.5-pro", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "ok"}'},
        ]}
        result = translate_request(body)
        response = result["contents"][1]["parts"][0]["functionResponse"]["response"]
        assert response == {"status": "ok"}

    def test_tools_map_to_function_declarations_with_sanitized_schema(self):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {
                    "name": "get_weather", "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                                   "additionalProperties": False, "$schema": "http://json-schema.org/draft-07/schema#"},
                }}]}
        result = translate_request(body)
        decl = result["tools"][0]["functionDeclarations"][0]
        assert decl["name"] == "get_weather"
        assert "additionalProperties" not in decl["parameters"]
        assert "$schema" not in decl["parameters"]

    @pytest.mark.parametrize("tool_choice,expected", [
        ("none", {"functionCallingConfig": {"mode": "NONE"}}),
        ("auto", {"functionCallingConfig": {"mode": "AUTO"}}),
        ("required", {"functionCallingConfig": {"mode": "ANY"}}),
        ({"type": "function", "function": {"name": "get_weather"}},
         {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}}),
    ])
    def test_tool_choice_mapping(self, tool_choice, expected):
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}],
                "tool_choice": tool_choice}
        result = translate_request(body)
        assert result["toolConfig"] == expected


class TestSanitizeJsonSchema:
    def test_strips_additional_properties_and_schema_key_recursively(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {
                "nested": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        }
        result = _sanitize_json_schema(schema)
        assert "additionalProperties" not in result
        assert "$schema" not in result
        assert "additionalProperties" not in result["properties"]["nested"]

    def test_strips_default_only_at_root(self):
        schema = {
            "type": "object", "default": {},
            "properties": {"count": {"type": "integer", "default": 0}},
        }
        result = _sanitize_json_schema(schema)
        assert "default" not in result
        assert result["properties"]["count"]["default"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: FAIL — assorted `KeyError`/`ImportError` (`_sanitize_json_schema` doesn't exist, tool turns not handled).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `translate_request` in `agent_gateway/adapters/gemini_adapter.py` with:

```python
import json


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    """Translates an OpenAI-shaped /v1/chat/completions body into a
    Gemini generateContent-shaped body."""
    messages = body.get("messages", [])
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(msg.get("content") or "")
        elif role == "tool":
            contents.append(_translate_tool_result_turn(msg, messages))
        elif role == "assistant" and msg.get("tool_calls"):
            contents.append(_translate_assistant_tool_call_turn(msg))
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": msg.get("content") or ""}]})
        else:  # "user"
            contents.append({"role": "user", "parts": [{"text": msg.get("content") or ""}]})

    gemini_body: dict[str, Any] = {"contents": contents}
    if system_parts:
        gemini_body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}

    tools = body.get("tools")
    if tools:
        gemini_body["tools"] = [{
            "functionDeclarations": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": _sanitize_json_schema(t["function"].get("parameters", {})),
                }
                for t in tools if t.get("type") == "function"
            ]
        }]

    if body.get("tool_choice") is not None:
        gemini_body["toolConfig"] = _translate_tool_choice(body["tool_choice"])

    return gemini_body


def _translate_assistant_tool_call_turn(msg: dict[str, Any]) -> dict[str, Any]:
    parts = [
        {"functionCall": {"name": call["function"]["name"],
                           "args": json.loads(call["function"].get("arguments") or "{}")}}
        for call in msg["tool_calls"]
    ]
    return {"role": "model", "parts": parts}


def _translate_tool_result_turn(msg: dict[str, Any], all_messages: list[dict[str, Any]]) -> dict[str, Any]:
    name = _lookup_tool_call_name(msg["tool_call_id"], all_messages)
    response = _wrap_tool_response(msg.get("content", ""))
    return {"role": "user", "parts": [{"functionResponse": {"name": name, "response": response}}]}


def _lookup_tool_call_name(tool_call_id: str, all_messages: list[dict[str, Any]]) -> str:
    """Recovers the function name for a tool result by scanning the same
    request's own message history -- OpenAI resends full history every
    call, so no persistent tool-call registry is needed."""
    for msg in all_messages:
        for call in msg.get("tool_calls") or []:
            if call.get("id") == tool_call_id:
                return call["function"]["name"]
    raise ValueError(f"No assistant tool_calls entry found for tool_call_id={tool_call_id!r}")


def _wrap_tool_response(content: Any) -> dict[str, Any]:
    """Gemini requires functionResponse.response to be a JSON object.
    Non-object tool output (a bare string, JSON array/scalar, or
    unparseable text) is wrapped as {"result": content}."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return {"result": content}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    return {"result": content}


def _sanitize_json_schema(schema: dict[str, Any], *, is_root: bool = True) -> dict[str, Any]:
    """Recursively strips JSON Schema keywords Gemini's function-calling
    schema rejects: `additionalProperties` and `$schema` at every level,
    and `default` at the root level only."""
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("additionalProperties", "$schema"):
            continue
        if key == "default" and is_root:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _sanitize_json_schema(v, is_root=False) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _sanitize_json_schema(value, is_root=False)
        else:
            out[key] = value
    return out


def _translate_tool_choice(tool_choice: str | dict[str, Any]) -> dict[str, Any]:
    if tool_choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if tool_choice == "required":
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = tool_choice["function"]["name"]
        return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    return {"functionCallingConfig": {"mode": "AUTO"}}
```

Move the `import json` to the top of the file with `from typing import Any`, rather than leaving it mid-file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/adapters/gemini_adapter.py tests/test_gemini_adapter.py
git commit -m "feat: add Gemini tool-call, tool-schema, and tool_choice translation"
```

---

## Task 6: Gemini response translation

**Files:**
- Modify: `agent_gateway/adapters/gemini_adapter.py`
- Test: `tests/test_gemini_adapter.py`

**Interfaces:**
- Produces: `translate_response(gemini_response: dict[str, Any], model: str) -> dict[str, Any]`, `_translate_finish_reason(gemini_reason: str | None, has_function_call: bool) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gemini_adapter.py`:

```python
from agent_gateway.adapters.gemini_adapter import translate_response


class TestTranslateResponse:
    def test_text_only_response(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "Hello there"}]},
                             "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["object"] == "chat.completion"
        assert result["model"] == "gemini-1.5-pro"
        assert result["choices"][0]["message"]["content"] == "Hello there"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def test_single_function_call_response(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
            ]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert json.loads(tool_calls[0]["function"]["arguments"]) == {"city": "Paris"}
        assert tool_calls[0]["id"].startswith("call_gemini_")
        assert result["choices"][0]["finish_reason"] == "tool_calls"

    def test_parallel_function_calls_response(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
                {"functionCall": {"name": "get_time", "args": {"city": "Paris"}}},
            ]}, "finishReason": "STOP"}],
            "usageMetadata": {},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert len(result["choices"][0]["message"]["tool_calls"]) == 2

    def test_max_tokens_beats_tool_calls_for_finish_reason(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {}}},
            ]}, "finishReason": "MAX_TOKENS"}],
            "usageMetadata": {},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["choices"][0]["finish_reason"] == "length"

    @pytest.mark.parametrize("gemini_reason,expected", [
        ("SAFETY", "content_filter"),
        ("RECITATION", "content_filter"),
        ("STOP", "stop"),
        ("OTHER", "stop"),
    ])
    def test_finish_reason_mapping_without_function_call(self, gemini_reason, expected):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]},
                             "finishReason": gemini_reason}],
            "usageMetadata": {},
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["choices"][0]["finish_reason"] == expected

    def test_missing_usage_metadata_defaults_to_zero(self):
        gemini_response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
        }
        result = translate_response(gemini_response, model="gemini-1.5-pro")
        assert result["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
```

Add `import json` and `import pytest` at the top of `tests/test_gemini_adapter.py` (pytest is already imported from Task 5; add `import json` alongside it).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'translate_response'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent_gateway/adapters/gemini_adapter.py`:

```python
import time
from uuid import uuid4


def translate_response(gemini_response: dict[str, Any], model: str) -> dict[str, Any]:
    candidate = gemini_response["candidates"][0]
    parts = candidate.get("content", {}).get("parts", [])

    text_parts = [p["text"] for p in parts if "text" in p]
    function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if function_calls:
        message["tool_calls"] = [
            {
                "id": f"call_gemini_{uuid4().hex}",
                "type": "function",
                "function": {"name": fc["name"], "arguments": json.dumps(fc.get("args", {}))},
            }
            for fc in function_calls
        ]

    finish_reason = _translate_finish_reason(candidate.get("finishReason"), bool(function_calls))
    usage = gemini_response.get("usageMetadata", {})

    return {
        "id": f"chatcmpl-gemini-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


def _translate_finish_reason(gemini_reason: str | None, has_function_call: bool) -> str:
    """MAX_TOKENS outranks tool_calls: a truncated response reports
    "length" even if a partial function call is present."""
    if gemini_reason == "MAX_TOKENS":
        return "length"
    if has_function_call:
        return "tool_calls"
    if gemini_reason in ("SAFETY", "RECITATION"):
        return "content_filter"
    return "stop"
```

Move `import time` and `from uuid import uuid4` to the top of the file with the other imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: PASS (23 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/adapters/gemini_adapter.py tests/test_gemini_adapter.py
git commit -m "feat: add Gemini response translation with finish_reason precedence"
```

---

## Task 7: Gemini streaming chunk translation

**Files:**
- Modify: `agent_gateway/adapters/gemini_adapter.py`
- Test: `tests/test_gemini_adapter.py`

**Interfaces:**
- Consumes: `_translate_finish_reason` from Task 6.
- Produces: `translate_stream_chunk(event: dict[str, Any], *, chunk_id: str, model: str, created: int) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gemini_adapter.py`:

```python
from agent_gateway.adapters.gemini_adapter import translate_stream_chunk


class TestTranslateStreamChunk:
    CHUNK_ID = "chatcmpl-gemini-test"
    MODEL = "gemini-1.5-pro"
    CREATED = 1_700_000_000

    def test_text_delta_chunk(self):
        event = {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        assert len(chunks) == 1
        assert chunks[0]["object"] == "chat.completion.chunk"
        assert chunks[0]["choices"][0]["delta"] == {"content": "Hel"}
        assert chunks[0]["choices"][0]["finish_reason"] is None

    def test_function_call_delta_chunk(self):
        event = {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
        ]}}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        assert len(chunks) == 1
        delta = chunks[0]["choices"][0]["delta"]
        assert delta["tool_calls"][0]["function"]["name"] == "get_weather"
        assert json.loads(delta["tool_calls"][0]["function"]["arguments"]) == {"city": "Paris"}

    def test_terminal_chunk_carries_finish_reason(self):
        event = {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["finish_reason"] == "stop"
        assert chunks[0]["choices"][0]["delta"] == {}

    def test_event_with_no_candidates_produces_no_chunks(self):
        assert translate_stream_chunk({}, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED) == []

    def test_all_chunks_share_the_same_id_model_and_created(self):
        event = {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}
        chunks = translate_stream_chunk(event, chunk_id=self.CHUNK_ID, model=self.MODEL, created=self.CREATED)
        for chunk in chunks:
            assert chunk["id"] == self.CHUNK_ID
            assert chunk["model"] == self.MODEL
            assert chunk["created"] == self.CREATED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'translate_stream_chunk'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent_gateway/adapters/gemini_adapter.py`:

```python
def translate_stream_chunk(
    event: dict[str, Any], *, chunk_id: str, model: str, created: int
) -> list[dict[str, Any]]:
    """Translates one Gemini streamGenerateContent SSE JSON event into
    0+ OpenAI-shaped chat.completion.chunk dicts."""
    candidates = event.get("candidates") or []
    if not candidates:
        return []
    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])

    chunks: list[dict[str, Any]] = []

    text = "".join(p["text"] for p in parts if "text" in p)
    if text:
        chunks.append(_stream_chunk(chunk_id, model, created, delta={"content": text}))

    function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
    for i, fc in enumerate(function_calls):
        chunks.append(_stream_chunk(chunk_id, model, created, delta={
            "tool_calls": [{
                "index": i,
                "id": f"call_gemini_{uuid4().hex}",
                "type": "function",
                "function": {"name": fc["name"], "arguments": json.dumps(fc.get("args", {}))},
            }],
        }))

    finish_reason_raw = candidate.get("finishReason")
    if finish_reason_raw:
        finish_reason = _translate_finish_reason(finish_reason_raw, bool(function_calls))
        chunks.append(_stream_chunk(chunk_id, model, created, delta={}, finish_reason=finish_reason))

    return chunks


def _stream_chunk(
    chunk_id: str, model: str, created: int, *, delta: dict[str, Any], finish_reason: str | None = None
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: PASS (28 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/adapters/gemini_adapter.py tests/test_gemini_adapter.py
git commit -m "feat: add Gemini streaming chunk translation to OpenAI chat.completion.chunk"
```

---

## Task 8: GeminiAdapter I/O

**Files:**
- Modify: `agent_gateway/adapters/gemini_adapter.py`
- Test: `tests/test_gemini_adapter.py`

**Interfaces:**
- Consumes: `translate_request`, `_iter_sse_json_events` (new), `translate_stream_chunk` from earlier tasks in this file.
- Produces: `GeminiAdapter` dataclass (fields `base_url`, `api_key`, `timeout_s`, `api_key_header: str = "x-goog-api-key"` — lets `server.py` pass `provider.api_key_header` from spec §4 without either adapter depending on `core.provider_routing`), methods `async generate_content(body, client=None) -> dict[str, Any]` (returns the **raw Gemini response**, not yet translated — translation happens in the server handler) and `async stream_generate_content(body, client) -> AsyncIterator[bytes]` (returns an already-open async generator of OpenAI-shaped SSE `bytes`, ready to hand to `StreamingResponse`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gemini_adapter.py`:

```python
import httpx

from agent_gateway.adapters.gemini_adapter import GeminiAdapter


class TestGeminiAdapterGenerateContent:
    async def test_generate_content_posts_translated_body_and_returns_raw_response(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
                "usageMetadata": {},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="gm-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}

        result = await adapter.generate_content(body, client=client)

        assert "models/gemini-1.5-pro:generateContent" in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "gm-key"
        assert captured["body"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert result["candidates"][0]["content"]["parts"][0]["text"] == "hi"

        await client.aclose()

    async def test_generate_content_respects_custom_api_key_header(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
                "usageMetadata": {},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta",
                                 api_key="custom-key", api_key_header="X-Custom-Key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}

        await adapter.generate_content(body, client=client)

        assert captured["headers"]["x-custom-key"] == "custom-key"
        assert "x-goog-api-key" not in captured["headers"]

        await client.aclose()

    async def test_generate_content_raises_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="bad-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}]}

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.generate_content(body, client=client)

        await client.aclose()


class TestGeminiAdapterStreamGenerateContent:
    async def test_stream_generate_content_yields_translated_sse_ending_in_done(self):
        sse_body = (
            'data: {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}\n\n'
            'data: {"candidates": [{"content": {"parts": [{"text": "lo"}]}, "finishReason": "STOP"}]}\n\n'
        ).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse_body,
                                   headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="gm-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}], "stream": True}

        gen = await adapter.stream_generate_content(body, client=client)
        raw_chunks = [chunk async for chunk in gen]
        text = b"".join(raw_chunks).decode()

        assert text.endswith("data: [DONE]\n\n")
        assert '"content": "Hel"' in text or '"content":"Hel"' in text
        assert text.count("chat.completion.chunk") == 2

        await client.aclose()

    async def test_stream_generate_content_raises_before_yielding_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="bad-key")
        body = {"model": "gemini-1.5-pro", "messages": [{"role": "user", "content": "hi"}], "stream": True}

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.stream_generate_content(body, client=client)

        await client.aclose()
```

Note: these are `async def test_...` functions with no `@pytest.mark.asyncio` decorator needed — `pyproject.toml` already sets `asyncio_mode = "auto"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'GeminiAdapter'`

- [ ] **Step 3: Write minimal implementation**

Append to `agent_gateway/adapters/gemini_adapter.py`:

```python
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass
class GeminiAdapter:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    api_key: str | None = None
    timeout_s: float = 60.0
    api_key_header: str = "x-goog-api-key"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        return headers

    async def generate_content(self, body: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        gemini_body = translate_request(body)
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            resp = await client.post(
                f"{self.base_url}/models/{body['model']}:generateContent",
                json=gemini_body,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns_client:
                await client.aclose()

    async def stream_generate_content(self, body: dict[str, Any], client: httpx.AsyncClient) -> AsyncIterator[bytes]:
        """Opens the upstream Gemini stream, checks its status eagerly
        (so an upstream error raises here rather than after the client
        has already received a 200), then returns an async generator
        that lazily translates and yields OpenAI-shaped SSE bytes."""
        gemini_body = translate_request(body)
        model = body["model"]
        url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
        req = client.build_request("POST", url, json=gemini_body, headers=self._headers())
        resp = await client.send(req, stream=True)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            await resp.aread()
            await resp.aclose()
            raise

        chunk_id = f"chatcmpl-gemini-{uuid4().hex}"
        created = int(time.time())

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for event in _iter_sse_json_events(resp):
                    for oa_chunk in translate_stream_chunk(event, chunk_id=chunk_id, model=model, created=created):
                        yield f"data: {json.dumps(oa_chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            finally:
                await resp.aclose()

        return _iter()


async def _iter_sse_json_events(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parses a Gemini `alt=sse` response body into decoded JSON event
    dicts, one per `data: ` line."""
    buffer_lines: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if buffer_lines:
                payload = "\n".join(buffer_lines)
                buffer_lines = []
                yield json.loads(payload.removeprefix("data: "))
            continue
        buffer_lines.append(line)
    if buffer_lines:
        yield json.loads("\n".join(buffer_lines).removeprefix("data: "))
```

Move `from collections.abc import AsyncIterator`, `from dataclasses import dataclass`, and `import httpx` to the top of the file with the other imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gemini_adapter.py -v`
Expected: PASS (33 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/adapters/gemini_adapter.py tests/test_gemini_adapter.py
git commit -m "feat: add GeminiAdapter I/O for non-streaming and streaming generateContent"
```

---

## Task 9: OpenAI-compatible passthrough streaming

**Files:**
- Modify: `agent_gateway/adapters/openai_adapter.py`
- Test: `tests/test_agent_gateway.py` (append a new test class near existing adapter-adjacent coverage, or create `tests/test_openai_adapter_streaming.py` — use the latter to avoid growing the already-552-line shared test file further; this is a small, focused addition)

**Interfaces:**
- Produces: `OpenAIAdapter.stream_chat_completions(self, body: dict[str, Any], client: httpx.AsyncClient) -> AsyncIterator[bytes]` (async method, no default for `client` — streaming always requires an explicit client since ownership/closing must span the generator's whole lifetime, unlike the single-request-response `chat_completions` method). Also adds a new dataclass field `api_key_header: str = "Authorization"` and updates `_headers()` to respect it — this is the fix for spec §4's "attached under `provider.api_key_header` if set, else a wire_shape default" requirement, mirroring the `api_key_header` field added to `GeminiAdapter` in Task 8. Existing callers that construct `OpenAIAdapter` without this field are unaffected (default preserves today's `Authorization: Bearer` behavior).

- [ ] **Step 1: Write the failing test**

Create `tests/test_openai_adapter_streaming.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_openai_adapter_streaming.py -v`
Expected: FAIL — `AttributeError: 'OpenAIAdapter' object has no attribute 'stream_chat_completions'`

- [ ] **Step 3: Write minimal implementation**

In `agent_gateway/adapters/openai_adapter.py`, add `from collections.abc import AsyncIterator` to the imports.

Add the new field to the `OpenAIAdapter` dataclass (after `timeout_s: float = 60.0`):

```python
    api_key_header: str = "Authorization"
```

Replace `_headers()`:

```python
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            if self.api_key_header == "Authorization":
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                headers[self.api_key_header] = self.api_key
        return headers
```

Then append this method to the `OpenAIAdapter` class (after `chat_completions`):

```python
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
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            await resp.aread()
            await resp.aclose()
            raise

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()

        return _iter()
```

Update the module docstring's second sentence to note the new scope (it currently says "This adapter's job is therefore just to relay..."; append after it): `Also serves DeepSeek and any other OpenAI-wire-compatible upstream (local Ollama/vLLM/llama.cpp/LM Studio servers) via the same relay logic, and provides a streaming variant for all of them.`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_openai_adapter_streaming.py -v`
Expected: PASS (4 tests)

- [ ] **Step 4b: Run full existing suite to confirm the `_headers()` change is backward compatible**

Run: `pytest tests/test_agent_gateway.py -v`
Expected: PASS — all existing tests still pass, since `api_key_header` defaults to `"Authorization"` and `_headers()`'s default-path output is byte-identical to before.

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/adapters/openai_adapter.py tests/test_openai_adapter_streaming.py
git commit -m "feat: add OpenAIAdapter.stream_chat_completions for SSE passthrough"
```

---

## Task 10: Wire provider routing and streaming into the server

**Files:**
- Modify: `agent_gateway/proxy/server.py`

**Interfaces:**
- Consumes: `ProviderRegistry`, `resolve_credential` (Tasks 2-3), `GeminiAdapter`, `translate_response` (Tasks 6, 8), `OpenAIAdapter.stream_chat_completions` and its new `api_key_header` field (Task 9), `GeminiAdapter`'s `api_key_header` field (Task 8).
- Produces: `GatewayState.provider_registry: ProviderRegistry`. `/v1/chat/completions` now returns `JSONResponse | StreamingResponse` and branches on `provider.wire_shape`.

- [ ] **Step 1: Write the failing test**

This task is verified end-to-end in Task 11 (the server has no direct unit tests of its own today — see the earlier discovery that `test_agent_gateway.py` never imports `TestClient`). Skip straight to implementation; Task 11 supplies the failing-then-passing test cycle for this wiring.

- [ ] **Step 2: (n/a — see Task 11)**

- [ ] **Step 3: Write the implementation**

In `agent_gateway/proxy/server.py`:

Add imports (alongside the existing ones):

```python
from fastapi.responses import JSONResponse, StreamingResponse

from agent_gateway.adapters.gemini_adapter import GeminiAdapter, translate_response
from agent_gateway.core.provider_routing import ProviderRegistry, resolve_credential
```

(Replace the existing `from fastapi.responses import JSONResponse` line with the combined `JSONResponse, StreamingResponse` import above.)

In `GatewayState.__init__`, replace this line:

```python
        self.openai_adapter = OpenAIAdapter(base_url=config.upstream.openai_base_url)
```

with:

```python
        self.provider_registry = ProviderRegistry(config.providers)
```

(`config.upstream.openai_base_url` no longer exists after Task 1 — the default OpenAI entry now lives in `config.providers.entries`. `self.openai_adapter` was already unused by the handler, which builds its own adapter instance per request, so removing it is not a behavior change.)

Replace the entire `chat_completions` handler with:

```python
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        gw: GatewayState = request.app.state.gateway
        body = await request.json()
        conversation_id = request.headers.get("x-conversation-id", "default")

        prefix_text = OpenAIAdapter.extract_frozen_prefix(
            body.get("messages", []), body.get("tools")
        )
        try:
            gw.boundary_guard.register_or_verify(conversation_id, prefix_text)
        except CachePrefixMutationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        model = body.get("model", "")
        try:
            provider = gw.provider_registry.resolve(model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        api_key = resolve_credential(provider, dict(request.headers))
        stream = bool(body.get("stream"))

        if provider.wire_shape == "gemini":
            gemini_kwargs = {"base_url": provider.base_url, "api_key": api_key}
            if provider.api_key_header:
                gemini_kwargs["api_key_header"] = provider.api_key_header
            adapter = GeminiAdapter(**gemini_kwargs)
            try:
                if stream:
                    gen = await adapter.stream_generate_content(body, client=gw.http_client)
                    return StreamingResponse(gen, media_type="text/event-stream")
                gemini_response = await adapter.generate_content(body, client=gw.http_client)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
            return JSONResponse(translate_response(gemini_response, model))

        # openai_compatible: openai, deepseek, local engines -- unchanged wire shape
        openai_kwargs = {"base_url": provider.base_url, "api_key": api_key}
        if provider.api_key_header:
            openai_kwargs["api_key_header"] = provider.api_key_header
        adapter = OpenAIAdapter(**openai_kwargs)
        try:
            if stream:
                gen = await adapter.stream_chat_completions(body, client=gw.http_client)
                return StreamingResponse(gen, media_type="text/event-stream")
            result = await adapter.chat_completions(body, client=gw.http_client)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
        except GuardrailViolation as exc:
            raise HTTPException(status_code=422, detail=exc.message) from exc

        return JSONResponse(result)
```

`provider.api_key_header` (spec §4's override) is passed straight through to whichever adapter is constructed, only when set — omitting it lets each adapter's own dataclass default apply (`"Authorization"` for `OpenAIAdapter`, `"x-goog-api-key"` for `GeminiAdapter`), which is exactly the "wire_shape default" spec §4 describes. No separate header-building function is needed in `server.py`; the mapping from resolved credential to actual `Authorization`/raw-header formatting lives in each adapter's `_headers()` (Tasks 8-9), right next to the HTTP call that uses it.

- [ ] **Step 4: (n/a — see Task 11 for pass verification)**

- [ ] **Step 5: Commit**

```bash
git add agent_gateway/proxy/server.py
git commit -m "feat: wire ProviderRegistry and streaming into /v1/chat/completions"
```

---

## Task 11: End-to-end tests and full regression run

**Files:**
- Create: `tests/test_chat_completions_routing.py`

**Interfaces:**
- Consumes: `create_app`, `GatewayConfig`, `ProviderConfig`, `ProvidersConfig` from earlier tasks; exercises the full request path via FastAPI's `TestClient`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_completions_routing.py`:

```python
"""End-to-end tests for /v1/chat/completions provider routing, using a
mocked httpx transport injected into GatewayState.http_client -- no real
network calls anywhere in this file."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_gateway.proxy.config import GatewayConfig, ProviderConfig, ProvidersConfig
from agent_gateway.proxy.server import create_app


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_completions_routing.py -v`
Expected: FAIL if Task 10 was skipped or mis-wired (e.g. `AttributeError: 'GatewayState' object has no attribute 'provider_registry'`). If Task 10's implementation step was already applied, some or all of these may already pass — that's fine, this task's job is to prove the wiring, not necessarily to watch every one fail first. Confirm at least the DeepSeek and Gemini tests fail against a pre-Task-10 checkout, or run `git stash` on the Task 10 commit temporarily to confirm the red state, then restore it.

- [ ] **Step 3: Fix any gaps found**

If any test fails for a reason other than confirming Task 10's necessity (e.g. a header-casing mismatch, an `httpx.AsyncClient` lifecycle issue in `_client_with_mock_transport`), fix `agent_gateway/proxy/server.py` or the test helper inline. A likely gap: `TestClient.__enter__()` runs the FastAPI lifespan synchronously in a background thread; if `app.state.gateway.http_client` swap races the first request, add a `client.app.state.gateway.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))` assignment (as shown) immediately after `__enter__()` returns, before issuing any request — this is already the order in `_client_with_mock_transport`, so no change should be needed, but verify by reading the failure output before editing anything.

- [ ] **Step 4: Run test to verify it passes, then run the full suite**

Run: `pytest tests/test_chat_completions_routing.py -v`
Expected: PASS (5 tests)

Run: `pytest tests/ -v`
Expected: PASS — all pre-existing 69 tests plus every test added in Tasks 1-11 (regression check: nothing in `test_agent_gateway.py` should have changed behavior).

- [ ] **Step 5: Commit**

```bash
git add tests/test_chat_completions_routing.py
git commit -m "test: add end-to-end coverage for multi-provider routing and streaming"
```

---

## Post-implementation note (not a task)

`README.md`'s "Getting started" section documents `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` env vars but not the new `providers` config block or `DEEPSEEK_API_KEY`/`GEMINI_API_KEY`. The approved spec's file breakdown (§8) does not list `README.md` as modified, so this plan intentionally leaves it untouched — raise it as a follow-up if operator-facing docs are wanted before this ships.
