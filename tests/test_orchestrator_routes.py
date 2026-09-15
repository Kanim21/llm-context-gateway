"""End-to-end tests for the orchestrator's FastAPI routes, including the
SSE termination requirement (route must close the stream right after a
terminal event, and serve a snapshot frame to late/finished subscribers).

start_run is non-blocking: POST /runs returns immediately with status
"running", and the run executes in the background. Tests therefore poll
the run snapshot for the expected state rather than reading it off the
POST response.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_gateway.core.provider_routing import ProviderRegistry
from agent_gateway.orchestrator.compiler import compile_canvas
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


async def _snapshot(client, run_id):
    return (await client.get(f"/v1/playbooks/runs/{run_id}")).json()


async def _wait_until(client, run_id, pred, tries=200):
    """Poll the run snapshot until pred(snapshot) holds (start_run runs in the
    background now). Returns the snapshot; raises if it never settles."""
    snap = await _snapshot(client, run_id)
    for _ in range(tries):
        if pred(snap):
            return snap
        await asyncio.sleep(0.005)
        snap = await _snapshot(client, run_id)
    raise AssertionError(
        f"run {run_id} never settled; last status={snap.get('status')} "
        f"step={snap.get('current_step_index')}"
    )


async def _wait_for_status(client, run_id, status, tries=200):
    return await _wait_until(client, run_id, lambda s: s["status"] == status, tries)


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

            # start_run is non-blocking: the run finishes in the background.
            snap = await _wait_for_status(client, run_id, "completed")
            assert snap["status"] == "completed"

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
        playbook = compile_canvas(canvas, playbook_id="pb_sse", name="SSE Test")
        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description="", schema_version=1,
            definition_json=playbook.model_dump(), canvas_json=canvas,
        )
        run_id = await gw.playbook_runner.start_run(playbook, "hi")
        await gw.playbook_runner.wait(run_id)  # run reaches the gate pause

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/v1/playbooks/runs/{run_id}/events") as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                # aiter_bytes() completing at all (rather than hanging) proves the
                # stream returned right after the terminal frame -- the concrete
                # proof of the SSE Termination requirement.
                assert b"gate_paused" in body


class TestSSETerminationSnapshot:
    """Late/finished subscribers: the live bus never delivers a terminal event
    to them, so the endpoint reconstructs one frame from the run snapshot."""

    async def test_stream_of_already_completed_run_emits_run_completed_and_closes(self):
        app = _app()
        gw = app.state.gateway
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
        }
        playbook = compile_canvas(canvas, playbook_id="pb_done", name="Done")
        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description="", schema_version=1,
            definition_json=playbook.model_dump(), canvas_json=canvas,
        )
        run_id = await gw.playbook_runner.start_run(playbook, "hi")
        await gw.playbook_runner.wait(run_id)  # finished, and nobody was subscribed

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/v1/playbooks/runs/{run_id}/events") as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
        assert b"run_completed" in body

    async def test_stream_of_rejected_run_emits_run_rejected_and_closes(self):
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
        playbook = compile_canvas(canvas, playbook_id="pb_rej", name="Rej")
        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description="", schema_version=1,
            definition_json=playbook.model_dump(), canvas_json=canvas,
        )
        run_id = await gw.playbook_runner.start_run(playbook, "hi")
        await gw.playbook_runner.wait(run_id)  # paused at the gate
        await gw.playbook_runner.resume_with_decision(playbook, run_id, "reject", 0)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/v1/playbooks/runs/{run_id}/events") as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
        assert b"run_rejected" in body


_GATED_CANVAS = {
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


class TestRequestValidation:
    async def test_create_playbook_missing_name_returns_422_not_500(self):
        async with _client() as client:
            resp = await client.post("/v1/playbooks", json={"canvas_json": {"nodes": [], "edges": []}})
            assert resp.status_code == 422

    async def test_create_run_missing_input_returns_422(self):
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks",
                                             json={"name": "P", "canvas_json": _GATED_CANVAS})
            playbook_id = create_resp.json()["id"]
            resp = await client.post(f"/v1/playbooks/{playbook_id}/runs", json={})
            assert resp.status_code == 422

    async def test_unknown_decision_is_rejected_and_run_stays_paused(self):
        """The fail-open bug: any unrecognised decision string used to take the
        approve path and complete the run."""
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks",
                                             json={"name": "Gated", "canvas_json": _GATED_CANVAS})
            playbook_id = create_resp.json()["id"]
            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs",
                                          json={"input": {"text": "hi"}})
            run_id = run_resp.json()["id"]
            await _wait_for_status(client, run_id, "paused")

            resp = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                      json={"decision": "banana", "step_index": 0})
            assert resp.status_code == 422

            snapshot = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot.json()["status"] == "paused"


class TestGateDoubleSubmit:
    async def test_second_decision_returns_409_and_leaves_terminal_state(self):
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks",
                                             json={"name": "Gated", "canvas_json": _GATED_CANVAS})
            playbook_id = create_resp.json()["id"]
            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs",
                                          json={"input": {"text": "hi"}})
            run_id = run_resp.json()["id"]
            await _wait_for_status(client, run_id, "paused")

            first = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                       json={"decision": "approve", "step_index": 0})
            assert first.status_code == 200
            await _wait_for_status(client, run_id, "completed")

            second = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                        json={"decision": "reject", "step_index": 0})
            assert second.status_code == 409

            snapshot = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot.json()["status"] == "completed"


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
            await _wait_for_status(client, run_id, "paused")

            decision_resp = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                               json={"decision": "approve", "step_index": 0})
            assert decision_resp.status_code == 200

            snap = await _wait_for_status(client, run_id, "completed")
            assert snap["status"] == "completed"


class TestGateStepIndexMismatch:
    async def test_stale_decision_for_earlier_gate_does_not_approve_a_later_gate(self):
        """Reproduces the N1 finding: on a playbook with two gates, a duplicate
        or stale decision submission that names the *already-decided* gate must
        not be silently applied to whatever gate the run has since advanced to."""
        canvas = {
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "g1", "type": "approval_gate", "data": {"label": "Review 1"}},
                {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                {"id": "g2", "type": "approval_gate", "data": {"label": "Review 2"}},
                {"id": "t2", "type": "teammate", "data": {"role": "R2", "objective": "O2", "tier": "speed"}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [
                {"source": "start", "target": "g1"}, {"source": "g1", "target": "t1"},
                {"source": "t1", "target": "g2"}, {"source": "g2", "target": "t2"},
                {"source": "t2", "target": "end"},
            ],
        }
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks",
                                             json={"name": "TwoGate", "canvas_json": canvas})
            playbook_id = create_resp.json()["id"]
            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs",
                                          json={"input": {"text": "hi"}})
            run_id = run_resp.json()["id"]
            snap = await _wait_for_status(client, run_id, "paused")
            assert snap["current_step_index"] == 0  # paused at g1

            first = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                       json={"decision": "approve", "step_index": 0})
            assert first.status_code == 200
            # After approving g1, t1 runs and the run pauses at g2 (index 2).
            snap = await _wait_until(client, run_id,
                                     lambda s: s["status"] == "paused" and s["current_step_index"] == 2)

            # Stale/duplicate submission still naming g1 must be rejected, not
            # silently applied to g2 (the run's actual current gate).
            second = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                        json={"decision": "approve", "step_index": 0})
            assert second.status_code == 409

            snapshot = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot.json()["status"] == "paused"
            assert snapshot.json()["current_step_index"] == 2

            # The correct step_index for the run's actual current gate still works.
            correct = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                         json={"decision": "approve", "step_index": 2})
            assert correct.status_code == 200
            snap = await _wait_for_status(client, run_id, "completed")
            assert snap["status"] == "completed"

    async def test_wrong_step_index_on_single_gate_playbook_returns_409(self):
        async with _client() as client:
            create_resp = await client.post("/v1/playbooks",
                                             json={"name": "Gated", "canvas_json": _GATED_CANVAS})
            playbook_id = create_resp.json()["id"]
            run_resp = await client.post(f"/v1/playbooks/{playbook_id}/runs",
                                          json={"input": {"text": "hi"}})
            run_id = run_resp.json()["id"]
            snap = await _wait_for_status(client, run_id, "paused")
            assert snap["current_step_index"] == 0

            resp = await client.post(f"/v1/playbooks/runs/{run_id}/gate",
                                      json={"decision": "approve", "step_index": 7})
            assert resp.status_code == 409

            snapshot = await client.get(f"/v1/playbooks/runs/{run_id}")
            assert snapshot.json()["status"] == "paused"
            assert snapshot.json()["current_step_index"] == 0


class TestUpdatePlaybook:
    async def test_put_adds_a_teammate_and_persists(self):
        async with _client() as client:
            canvas = {
                "nodes": [
                    {"id": "start", "type": "start", "data": {}},
                    {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                    {"id": "end", "type": "end", "data": {}},
                ],
                "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
            }
            pid = (await client.post("/v1/playbooks", json={"name": "P", "canvas_json": canvas})).json()["id"]

            canvas["nodes"].insert(2, {"id": "t2", "type": "teammate",
                                       "data": {"role": "R2", "objective": "O2", "tier": "speed"}})
            canvas["edges"] = [{"source": "start", "target": "t1"},
                               {"source": "t1", "target": "t2"},
                               {"source": "t2", "target": "end"}]
            resp = await client.put(f"/v1/playbooks/{pid}", json={"name": "P", "canvas_json": canvas})
            assert resp.status_code == 200
            assert len(resp.json()["definition_json"]["steps"]) == 2

            got = await client.get(f"/v1/playbooks/{pid}")
            assert len(got.json()["definition_json"]["steps"]) == 2  # persisted, not tab-local

    async def test_put_invalid_canvas_returns_422(self):
        async with _client() as client:
            canvas = {
                "nodes": [
                    {"id": "start", "type": "start", "data": {}},
                    {"id": "t1", "type": "teammate", "data": {"role": "R", "objective": "O", "tier": "speed"}},
                    {"id": "end", "type": "end", "data": {}},
                ],
                "edges": [{"source": "start", "target": "t1"}, {"source": "t1", "target": "end"}],
            }
            pid = (await client.post("/v1/playbooks", json={"name": "P", "canvas_json": canvas})).json()["id"]
            bad = {"nodes": [{"id": "t1", "type": "teammate", "data": {"role": "", "objective": "", "tier": "speed"}}], "edges": []}
            resp = await client.put(f"/v1/playbooks/{pid}", json={"name": "P", "canvas_json": bad})
            assert resp.status_code == 422

    async def test_put_unknown_playbook_returns_404(self):
        async with _client() as client:
            resp = await client.put("/v1/playbooks/pb_missing",
                                    json={"name": "x", "canvas_json": {"nodes": [], "edges": []}})
            assert resp.status_code == 404
