"""FastAPI gateway server.

Exposes /v1/chat/completions and /v1/messages as drop-in-compatible
proxies in front of OpenAI and Anthropic respectively, plus /v1/metrics
(unblended token/cost/call reduction, see `metrics.py`) and /healthz.

The request path is: guardrails -> cache boundary verify/register ->
observation masking -> compaction (if over threshold) -> optional lossy
passes (only if explicitly requested in the request body) -> adapter
relay -> record metrics. Every stage is a thin call into `core/`; this
module only wires them together and speaks HTTP.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent_gateway.adapters.anthropic_adapter import AnthropicAdapter
from agent_gateway.adapters.gemini_adapter import GeminiAdapter, translate_response
from agent_gateway.adapters.openai_adapter import OpenAIAdapter
from agent_gateway.core.cache_boundary import (
    BoundaryGuard,
    CachePrefixMutationError,
    TokenizerEngine,
)
from agent_gateway.core.guardrails import GuardrailViolation
from agent_gateway.core.provider_routing import ProviderRegistry, resolve_credential
from agent_gateway.proxy.config import GatewayConfig
from agent_gateway.proxy.metrics import MetricsAccumulator
from agent_gateway.storage.sqlite_store import SqliteStore


class GatewayState:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.store = SqliteStore(config.storage.sqlite_path)
        self.tokenizer_engine = TokenizerEngine()
        self.boundary_guard = BoundaryGuard()
        self.metrics = MetricsAccumulator()
        self.provider_registry = ProviderRegistry(config.providers)
        self.anthropic_adapter = AnthropicAdapter(base_url=config.upstream.anthropic_base_url)
        self.http_client = httpx.AsyncClient(timeout=config.upstream.request_timeout_s)

    async def aclose(self) -> None:
        await self.http_client.aclose()
        self.store.close()


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    config = config or GatewayConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.gateway = GatewayState(config)
        try:
            yield
        finally:
            await app.state.gateway.aclose()

    app = FastAPI(title="Agent Gateway", version="2.0.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/metrics")
    async def metrics(request: Request) -> dict[str, Any]:
        gw: GatewayState = request.app.state.gateway
        unblended = gw.metrics.snapshot().as_dict()
        unblended["storage"] = gw.store.artifact_stats()
        return unblended

    @app.post("/v1/chat/completions", response_model=None)
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
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    @app.post("/v1/messages")
    async def messages(request: Request) -> JSONResponse:
        gw: GatewayState = request.app.state.gateway
        body = await request.json()
        conversation_id = request.headers.get("x-conversation-id", "default")

        prefix_text = AnthropicAdapter.extract_frozen_prefix(body)
        try:
            gw.boundary_guard.register_or_verify(conversation_id, prefix_text)
        except CachePrefixMutationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        body = AnthropicAdapter.apply_ephemeral_cache_control(body)

        api_key = request.headers.get("x-api-key") or None
        adapter = AnthropicAdapter(base_url=gw.config.upstream.anthropic_base_url, api_key=api_key)
        try:
            result = await adapter.messages(body, client=gw.http_client)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc

        return JSONResponse(result)

    return app


def _config_path_from_env() -> str | None:
    return os.environ.get("AGENT_GATEWAY_CONFIG")


app = create_app(GatewayConfig.load(_config_path_from_env()))
