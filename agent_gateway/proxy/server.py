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

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
from agent_gateway.orchestrator.engine import GatewayCompletionFn, PlaybookRunner
from agent_gateway.orchestrator.events import EventBus
from agent_gateway.orchestrator.routes import build_router
from agent_gateway.orchestrator.schema import Playbook
from agent_gateway.orchestrator.seed import seed_templates
from agent_gateway.orchestrator.store import OrchestratorStore
from agent_gateway.proxy.config import GatewayConfig
from agent_gateway.proxy.logging_config import install_log_redaction
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
        self.orchestrator_store = OrchestratorStore(self.store)
        self.event_bus = EventBus()
        completion_fn = GatewayCompletionFn(
            provider_registry=self.provider_registry, http_client=self.http_client,
        )
        self.playbook_runner = PlaybookRunner(
            store=self.orchestrator_store, events=self.event_bus,
            tier_models=config.playbook_tiers, blackboard_store=self.store,
            complete=completion_fn,
        )

    async def aclose(self) -> None:
        await self.http_client.aclose()
        self.store.close()


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    config = config or GatewayConfig()
    install_log_redaction()  # ensure no credential reaches a log sink

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.gateway = GatewayState(config)
        seed_templates(app.state.gateway.orchestrator_store)
        playbooks_by_id = {
            row["id"]: Playbook.model_validate(row["definition_json"])
            for row in app.state.gateway.orchestrator_store.list_playbooks()
        }
        # Recovery replays each interrupted run to completion, including real
        # LLM calls. Awaiting it here meant the app served nothing -- not even
        # /healthz -- until every interrupted run finished. Recovery is
        # idempotent (reset_step_to_pending only fires on a still-"running"
        # record), so cancelling it at shutdown just defers it to next startup.
        recovery_task = asyncio.create_task(
            app.state.gateway.playbook_runner.recover_interrupted_runs(playbooks_by_id)
        )
        app.state.recovery_task = recovery_task
        try:
            yield
        finally:
            recovery_task.cancel()
            await app.state.gateway.aclose()

    app = FastAPI(title="Agent Gateway", version="2.0.0", lifespan=lifespan)

    # Local-first, no-auth v1: the Next.js dev server on :3000 is the only
    # browser origin that talks to this API. Without this, every fetch and
    # EventSource from the frontend fails on preflight.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_allow_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "x-api-key", "x-conversation-id"],
        # allow_credentials stays False: the browser sends API keys as explicit
        # headers, not cookies, so cookie-credentialed CORS is not needed.
    )

    app.include_router(build_router())

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
