"""Confirms the orchestrator is wired into the main gateway app: routes
respond, templates are seeded on startup, and gateway state carries the
orchestrator objects."""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from agent_gateway.orchestrator.engine import PlaybookRunner
from agent_gateway.proxy.config import GatewayConfig
from agent_gateway.proxy.server import create_app


class TestOrchestratorWiring:
    def test_gateway_state_has_orchestrator_objects(self):
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            gw = app.state.gateway
            assert gw.orchestrator_store is not None
            assert gw.event_bus is not None
            assert gw.playbook_runner is not None

    def test_cors_allows_the_frontend_dev_origin(self):
        """Without this the Next.js dev server on :3000 can't call the API at
        all -- every fetch and EventSource dies on preflight."""
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            resp = client.get("/v1/playbooks", headers={"Origin": "http://localhost:3000"})
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_cors_preflight_is_answered(self):
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            resp = client.options("/v1/playbooks", headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            })
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_startup_does_not_block_on_interrupted_run_recovery(self, monkeypatch):
        """Recovery replays whole runs, LLM calls included. Awaiting it inline
        meant the app served nothing -- not even /healthz -- until it finished."""
        async def slow_recovery(self, playbooks_by_id):
            await asyncio.sleep(30)

        monkeypatch.setattr(PlaybookRunner, "recover_interrupted_runs", slow_recovery)

        app = create_app(GatewayConfig())
        started = time.monotonic()
        with TestClient(app) as client:
            resp = client.get("/healthz")
            elapsed = time.monotonic() - started
            assert resp.status_code == 200
            assert elapsed < 5, "startup waited on recovery"
            assert not app.state.recovery_task.done()

    def test_templates_are_seeded_on_startup(self):
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            resp = client.get("/v1/playbooks")
            assert resp.status_code == 200
            ids = {p["id"] for p in resp.json()}
            assert "tpl_inbound_sales_triage" in ids
            assert "tpl_multi_channel_social_content" in ids
            assert "tpl_support_ticket_escalation" in ids


def test_cors_rejects_unlisted_origin():
    app = create_app(GatewayConfig())
    with TestClient(app) as client:
        r = client.get("/v1/playbooks", headers={"Origin": "http://evil.example"})
        assert r.headers.get("access-control-allow-origin") != "http://evil.example"


def test_custom_cors_origin_is_honored():
    cfg = GatewayConfig(cors_allow_origins=["http://localhost:3000", "https://app.example.com"])
    app = create_app(cfg)
    with TestClient(app) as client:
        r = client.get("/v1/playbooks", headers={"Origin": "https://app.example.com"})
        assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
