# Multi-Provider Routing Expansion — Design Spec

Status: draft, awaiting review
Date: 2026-09-14
Scope: sub-project 1 of 2 from the "LLM Context Gateway" expansion (the Visual
Playbook Builder is a separate, later spec).

## 1. Goals

Extend the existing `/v1/chat/completions` endpoint so a single OpenAI-shaped
request can be transparently routed to:

- **OpenAI** (unchanged, today's default)
- **DeepSeek** — reuses the existing OpenAI wire format as-is
- **Local OpenAI-compatible inference servers** (Ollama, vLLM, llama.cpp
  server, LM Studio) — also reuse the OpenAI wire format as-is
- **Gemini** — genuinely different wire format; the gateway translates
  request/response (including tool calls) and streaming chunks in both
  directions

`/v1/messages` (Anthropic) is untouched by this work.

## 2. Non-goals (explicitly out of scope)

- Automatic cross-provider failover / retry / circuit breaker. Routing
  picks one provider per request; resilience is a dedicated follow-up spec.
- The Visual Playbook Builder (frontend, graph schema, execution runtime).
- Per-provider breakdown of `UnblendedMetrics` — metrics stay
  provider-agnostic, as they are today.
- True non-OpenAI-shaped local runtimes (e.g. a raw llama.cpp `/completion`
  endpoint). Only OpenAI-wire-compatible local servers are supported.
- Changes to `/v1/messages` or `AnthropicAdapter`.

## 3. Architecture overview

```
client --(OpenAI-shaped request, model="...")--> /v1/chat/completions
                                                        |
                                        1. cache boundary check
                                           (always against the canonical
                                           OpenAI-shaped body -- unchanged
                                           for every provider)
                                                        |
                                        2. ProviderRegistry.resolve(model)
                                           -> ProviderConfig
                                                        |
                                        3. resolve_credential(provider, headers)
                                           (client Authorization header wins
                                           if present, else server-side
                                           env var for that provider)
                                                        |
                         -------------------------------------------------
                         |                                               |
                  wire_shape == "openai_compatible"           wire_shape == "gemini"
                  (openai / deepseek / local-*)                          |
                         |                                    translate_request()
                  passthrough, unchanged body                           |
                  (stream or non-stream)                     GeminiAdapter.generate_content()
                         |                                    / stream_generate_content()
                                                                          |
                                                               translate_response() /
                                                               translate_stream_chunk()
                         -------------------------------------------------
                                                        |
                                          OpenAI-shaped JSONResponse or
                                          OpenAI-shaped SSE StreamingResponse
```

Key invariant: **the cache boundary check never changes.** It always
operates on `OpenAIAdapter.extract_frozen_prefix()` of the incoming
client-facing body, regardless of which provider ends up serving the
request. This is what lets cache-prefix immutability stay a single,
provider-agnostic guarantee.

## 4. Provider registry & config (Approach A)

`agent_gateway/proxy/config.py` changes:

```python
class ProviderConfig(BaseModel):
    name: str
    wire_shape: Literal["openai_compatible", "gemini"]
    base_url: str
    api_key_env: str | None = None   # env var holding the server-side key; None = no auth
    api_key_header: str | None = None  # override; default per wire_shape if unset

class ProvidersConfig(BaseModel):
    entries: list[ProviderConfig] = Field(default_factory=lambda: [
        ProviderConfig(name="openai", wire_shape="openai_compatible",
                       base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
    ])
    model_routes: dict[str, str] = Field(default_factory=dict)  # model name -> provider name
    default_provider: str = "openai"
```

`GatewayConfig` gains `providers: ProvidersConfig`. `UpstreamConfig` keeps
`anthropic_base_url` and `request_timeout_s` (still used by `/v1/messages`);
`openai_base_url` moves into `providers.entries` as the default `"openai"`
entry. This is a config-*schema* change, not an endpoint change — the
"strict backward compatibility" rule covers `/v1/chat/completions`,
`/v1/messages`, and streaming routes, not the config file's internal shape,
and no external config-file consumers exist yet for this young (v2.0.0,
single-commit) project.

Operators add DeepSeek/local entries by config, e.g.:

```json
{"name": "deepseek", "wire_shape": "openai_compatible",
 "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"}
```

New module `agent_gateway/core/provider_routing.py` (pure logic, no I/O,
mirrors the existing `routing.py` pattern):

- `ProviderRegistry.resolve(model: str) -> ProviderConfig` — explicit
  `model_routes` entry wins; falls back to `default_provider` ("openai"),
  which is exactly today's implicit behavior, so any existing caller keeps
  hitting real OpenAI unchanged.
- `resolve_credential(provider: ProviderConfig, request_headers: dict) -> str | None`
  — if the client sent an `Authorization: Bearer <key>` header, that value
  is used as the resolved provider's credential; otherwise falls back to
  `os.environ[provider.api_key_env]`. Either way, the resolved value is
  attached to the *upstream* request under `provider.api_key_header` if
  set, else a `wire_shape` default (`Authorization: Bearer <key>` for
  `openai_compatible`, `x-goog-api-key: <key>` for `gemini`) — so a client
  always speaks plain `Authorization: Bearer`, regardless of which
  provider's own header convention the resolved upstream actually expects.

## 5. Request translation (OpenAI → Gemini)

Implemented as pure functions in `agent_gateway/adapters/gemini_adapter.py`,
so they're unit-testable with no HTTP mocking at all:

- **Messages/history:** `system` messages → `systemInstruction`. Plain
  `user`/`assistant` text turns → `contents` with `role: "user"|"model"`
  and a single text `part`.
- **Tool-call turns:** an `assistant` message with `tool_calls` → a
  `model` turn whose parts are `functionCall: {name, args}` (one per
  call). A `role: "tool"` message (`tool_call_id` + `content`) → a `user`
  turn with `parts: [{functionResponse: {name, response: {content: ...}}}]`.
  The function `name` is recovered by scanning the *same request's*
  `messages` array for the assistant `tool_calls` entry whose `id` matches
  — since OpenAI's API is stateless and resends full history every call,
  no persistent tool-call registry is needed.
- **Tools:** `tools: [{type: "function", function: {...}}]` →
  `tools: [{function_declarations: [...]}]`, with each function's
  `parameters` schema passed through `_sanitize_json_schema()`, which
  recursively strips `additionalProperties` and `$schema` at every level,
  and strips `default` only at the schema's root level.
- **tool_choice:**
  - `"none"` → `tool_config.function_calling_config.mode = "NONE"`
  - `"auto"` → `mode = "AUTO"`
  - `"required"` → `mode = "ANY"`
  - `{"type": "function", "function": {"name": "foo"}}` → `mode = "ANY"`,
    `allowed_function_names: ["foo"]`

## 6. Response translation (Gemini → OpenAI)

- Text parts → `choices[0].message.content`.
- `functionCall` parts → `choices[0].message.tool_calls`, one entry per
  part, each with a freshly generated `id: f"call_gemini_{uuid4()}"`.
- `finish_reason`, in priority order: Gemini `MAX_TOKENS` → `"length"`;
  else any `functionCall` part present → `"tool_calls"`; else Gemini
  `SAFETY`/`RECITATION` → `"content_filter"`; else (`STOP`/anything else)
  → `"stop"`.
- `usageMetadata.{promptTokenCount, candidatesTokenCount, totalTokenCount}`
  → `usage.{prompt_tokens, completion_tokens, total_tokens}`.

## 7. Streaming

Two independent pieces, since **no streaming exists anywhere in the
codebase today** — this spec adds it from scratch for both paths:

- **`openai_compatible` passthrough streaming** (new
  `OpenAIAdapter.stream_chat_completions()`): opens the upstream request
  via `client.stream("POST", ...)` and relays the raw SSE lines verbatim —
  no transformation, since the wire shape already matches. Used for real
  OpenAI, DeepSeek, and local engines alike.
- **Gemini streaming translation** (new
  `GeminiAdapter.stream_generate_content()` + `translate_stream_chunk()`):
  calls Gemini's `:streamGenerateContent?alt=sse` endpoint, and for each
  incoming Gemini SSE JSON event emits one or more OpenAI-shaped
  `chat.completion.chunk` SSE events (`choices[0].delta.content` for text,
  `choices[0].delta.tool_calls` for function calls, terminated by a final
  chunk carrying `finish_reason` and `data: [DONE]`).
  - **Known simplification:** Gemini does not fragment function-call
    arguments across multiple stream events the way OpenAI does. The
    translated stream emits the function `name` and its complete
    `arguments` JSON string together in a single delta chunk once Gemini
    finishes generating that call, rather than progressively streaming
    partial argument text. The final assembled tool call is identical;
    only the intra-call chunk timing differs from native OpenAI streaming.

## 8. File breakdown

**New files:**
- `agent_gateway/core/provider_routing.py` — `ProviderRegistry`,
  `resolve_credential()`
- `agent_gateway/adapters/gemini_adapter.py` — `GeminiAdapter` (I/O methods
  `generate_content`/`stream_generate_content`) + pure translation
  functions (`translate_request`, `translate_response`,
  `translate_stream_chunk`, `_sanitize_json_schema`)
- `tests/test_provider_routing.py` — registry + credential resolution
- `tests/test_gemini_adapter.py` — all translation functions (pure,
  no mocking) + streaming/non-streaming I/O (mocked transport)

**Modified files:**
- `agent_gateway/proxy/config.py` — add `ProviderConfig`, `ProvidersConfig`;
  trim `UpstreamConfig`
- `agent_gateway/adapters/openai_adapter.py` — add
  `stream_chat_completions()`; docstring updated to note it now serves any
  OpenAI-wire-compatible upstream (OpenAI, DeepSeek, local engines), no
  rename
- `agent_gateway/proxy/server.py` — `GatewayState` builds a
  `ProviderRegistry` from config; `/v1/chat/completions` handler branches
  on resolved `wire_shape`, adds streaming responses on both branches
- `tests/test_agent_gateway.py` — unchanged in structure; existing tests
  stay as regression coverage for today's default (unmapped model →
  OpenAI) behavior

**Unchanged:** `anthropic_adapter.py`, `/v1/messages`, `cache_boundary.py`,
`metrics.py`, all `core/` modules other than the new `provider_routing.py`.

## 9. Testing strategy

Consistent with this repo's existing convention (see `BENCHMARKS.md`'s
latency note): **no real network calls or live API keys anywhere in the
test suite.**

- **Pure-function unit tests, no I/O mocking:** `ProviderRegistry.resolve`
  (explicit route, fallback, unmapped model); `GeminiAdapter.translate_request`
  (text turns, tool-call/tool-response turn mapping with same-request name
  lookup, all 4 `tool_choice` cases, schema sanitization of
  `additionalProperties`/`$schema`/root `default`); `translate_response`
  (text-only, single and parallel function calls, each `finish_reason`
  branch, usage mapping); `translate_stream_chunk` (text delta,
  function-call delta, terminal chunk).
- **Credential resolution unit tests:** client `Authorization` header
  present → used and translated to the target provider's header style;
  absent → `os.environ[api_key_env]` used (via `monkeypatch`).
- **Adapter I/O tests via `httpx.MockTransport`:** `OpenAIAdapter.stream_chat_completions`
  relays a canned SSE body byte-for-byte unchanged; `GeminiAdapter.generate_content`/
  `stream_generate_content` hit the configured `base_url` and pass through
  a hand-authored fixture response matching Gemini's public API shape.
- **End-to-end via FastAPI `TestClient`** with `MockTransport` injected into
  `GatewayState.http_client`: unmapped model still reaches the real OpenAI
  base URL unchanged (regression test); `model="deepseek-chat"` reaches a
  configured DeepSeek entry using `OpenAIAdapter` with no new adapter class;
  `model="gemini-1.5-pro"` with tools produces a correctly-translated
  upstream request and an OpenAI-shaped response with `tool_calls`;
  streaming Gemini request produces a valid OpenAI-shaped SSE stream ending
  in `data: [DONE]`; a mutated frozen prefix on a Gemini-routed conversation
  still raises `409` (boundary check is provider-agnostic).

## 10. Known limitations / honesty notes

- DeepSeek and local-engine (Ollama/vLLM/llama.cpp/LM Studio) support is
  implemented as generic OpenAI-wire-compatible passthrough and tested only
  against mocked responses — **not verified against a live DeepSeek account
  or a live local server**, since neither is available in this environment.
- Gemini translation is tested against hand-authored fixtures matching
  Google's publicly documented Gemini API request/response shapes, **not a
  live Gemini account**.
- Gemini's streamed function-call arguments arrive as one complete chunk
  rather than progressively, per §7.
- No automatic failover/circuit-breaker across providers — single-provider
  routing only, per §2.
