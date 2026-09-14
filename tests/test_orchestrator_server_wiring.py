"""Confirms the orchestrator is wired into the main gateway app: routes
respond, templates are seeded on startup, and gateway state carries the
orchestrator objects."""

from __future__ import annotations

from fastapi.testclient import TestClient

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

    def test_templates_are_seeded_on_startup(self):
        app = create_app(GatewayConfig())
        with TestClient(app) as client:
            resp = client.get("/v1/playbooks")
            assert resp.status_code == 200
            ids = {p["id"] for p in resp.json()}
            assert "tpl_inbound_sales_triage" in ids
            assert "tpl_multi_channel_social_content" in ids
            assert "tpl_support_ticket_escalation" in ids
