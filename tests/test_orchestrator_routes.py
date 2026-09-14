"""End-to-end tests for the orchestrator's FastAPI routes, including the
SSE termination requirement (route must close the stream right after a
terminal event)."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_gateway.core.provider_routing import ProviderRegistry
from agent_gateway.orchestrator.engine import PlaybookRunner
from agent_gateway.orchestrator.events import EventBus
from agent_gateway.orchestrator.routes import build_router
from agent_gateway.orchestrator.store import OrchestratorStore
from agent_gateway.proxy.config import ProviderConfig, ProvidersConfig
from agent_gateway.storage.sqlite_store import SqliteStore
from fastapi import FastAPI


class _FakeGateway:
    def __init__(self):
        self.store = OrchestratorStore(SqliteStore(":memory:"))
        self.orchestrator_store = self.store
        self.event_bus = EventBus()

        async def complete(*, model, prompt, on_token):
            await on_token("Result text")
            return "Result text"

        self.playbook_runner = PlaybookRunner(
            store=self.store, events=self.event_bus,
            tier_models={"speed": "m", "balanced": "m", "brain": "m"},
            blackboard_store=SqliteStore(":memory:"), complete=complete,
        )


def _app():
    app = FastAPI()
    app.state.gateway = _FakeGateway()
    app.include_router(build_router())
    return app


def _client():
    transport = httpx.ASGITransport(app=_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestCreateAndListPlaybooks:
    async def test_create_then_list_then_get(self):
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "t1", "type": "teammate", "data": {
                    "role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks", json={
                "name": "My Playbook", "canvas_json": canvas,
            })
            assert create_resp.status_code == 200
            playbook_id = create_resp.json()["id"]

            list_resp = await client.get("/v1/playbooks")
            assert any(p["id"] == playbook_id for p in list_resp.json())

            get_resp = await client.get(f"/v1/playbooks/{playbook_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["name"] == "My Playbook"

    async def test_invalid_canvas_returns_422_with_plain_language_errors(self):
        canvas = {"nodes": [{"id": "t1", "type": "teammate", "data": {"role": "", "objective": "", "tier": "speed"}}], "edges": []}
        async with _client() as client:
            resp = await client.post("/v1/playbooks", json={"name": "Bad", "canvas_json": canvas})
            assert resp.status_code == 422
            assert "A playbook needs exactly one starting point." in [e["message"] for e in resp.json()["errors"]]


class TestRunLifecycleAndSSETermination:
    async def test_start_run_and_get_snapshot(self):
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks", json={"name": "P", "canvas_json": canvas})
            playbook_id = create_resp.json()["id"]

            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs",
                                          json={"input": {"text": "hello"}})
            assert run_resp.status_code == 200
            run_id = run_resp.json()["id"]

            snapshot_resp = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot_resp.status_code == 200
            assert snapshot_resp.json()["status"] == "completed"

    async def test_sse_stream_closes_after_terminal_event(self):
        app = _app()
        gw = app.state.gateway
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "g1", "type": "approval_gate", "data": {"label": "Review"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "g1"}, {"source": "g1", "target": "end"}],
        }
        from agent_gateway.orchestrator.compiler import compile_canvas
        playbook = compile_canvas(canvas, playbook_id="pb_sse", name="SSE Test")
        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description="", schema_version=1,
            definition_json=playbook.model_dump(), canvas_json=canvas,
        )
        run_id = await gw.playbook_runner.start_run(playbook, "hi")  # pauses at the gate

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/v1/playbooks/runs/{run_id}/events") as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                # aiter_bytes() completing at all (rather than hanging) proves the
                # generator returned right after the terminal event -- this is the
                # concrete proof of the SSE Termination requirement.
                assert b"gate_paused" in body


class TestGateDecision:
    async def test_submit_approve_decision_resumes_run(self):
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "g1", "type": "approval_gate", "data": {"label": "Review"}},
                {"id": "t2", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [
                {"source": "start", "target": "g1"}, {"source": "g1", "target": "t2"},
                {"source": "t2", "target": "end"},
            ],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks", json={"name": "Gated", "canvas_json": canvas})
            playbook_id = create_resp.json()["id"]
            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs", json={"input": {"text": "hi"}})
            run_id = run_resp.json()["id"]

            decision_resp = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                               json={"decision": "approve"})
            assert decision_resp.status_code == 200

            snapshot_resp = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot_resp.json()["status"] == "completed"
