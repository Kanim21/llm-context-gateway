"""FastAPI router for the Visual Playbook Builder's 7 endpoints. Mounted
into the existing proxy/server.py app in Task 14."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent_gateway.orchestrator.compiler import CompilerError, compile_canvas
from agent_gateway.orchestrator.engine import InvalidDecisionError, RunStateError
from agent_gateway.orchestrator.schema import Playbook
from agent_gateway.orchestrator.store import new_id


class CreatePlaybookRequest(BaseModel):
    name: str
    description: str = ""
    canvas_json: dict


class RunInput(BaseModel):
    text: str


class StartRunRequest(BaseModel):
    input: RunInput


class GateDecisionRequest(BaseModel):
    # Literal, not str: an unrecognised decision must be rejected here rather
    # than fall through to the approve path in the engine.
    decision: Literal["approve", "edit", "reject"]
    edited_output: dict | None = None


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/playbooks")

    @router.post("")
    async def create_playbook(body: CreatePlaybookRequest, request: Request):
        gw = request.app.state.gateway
        playbook_id = new_id("pb")
        try:
            playbook = compile_canvas(
                body.canvas_json, playbook_id=playbook_id,
                name=body.name, description=body.description,
            )
        except CompilerError as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})

        gw.orchestrator_store.upsert_playbook(
            id=playbook.id, name=playbook.name, description=playbook.description,
            schema_version=playbook.schema_version, definition_json=playbook.model_dump(),
            canvas_json=body.canvas_json,
        )
        return gw.orchestrator_store.get_playbook(playbook.id)

    @router.get("")
    async def list_playbooks(request: Request) -> list[dict]:
        gw = request.app.state.gateway
        return gw.orchestrator_store.list_playbooks()

    @router.get("/{playbook_id}")
    async def get_playbook(playbook_id: str, request: Request) -> dict:
        gw = request.app.state.gateway
        playbook = gw.orchestrator_store.get_playbook(playbook_id)
        if playbook is None:
            raise HTTPException(status_code=404, detail="Playbook not found")
        return playbook

    @router.post("/{playbook_id}/runs")
    async def create_run(playbook_id: str, body: StartRunRequest, request: Request) -> dict:
        gw = request.app.state.gateway
        playbook_row = gw.orchestrator_store.get_playbook(playbook_id)
        if playbook_row is None:
            raise HTTPException(status_code=404, detail="Playbook not found")
        playbook = Playbook.model_validate(playbook_row["definition_json"])
        run_id = await gw.playbook_runner.start_run(playbook, body.input.text)
        return gw.orchestrator_store.get_run(run_id)

    @router.get("/runs/{run_id}")
    async def get_run_snapshot(run_id: str, request: Request) -> dict:
        gw = request.app.state.gateway
        run = gw.orchestrator_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run["steps"] = gw.orchestrator_store.list_step_records(run_id)
        return run

    @router.get("/runs/{run_id}/events")
    async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
        gw = request.app.state.gateway

        async def event_stream():
            async for event_type, data in gw.event_bus.subscribe(run_id):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.post("/runs/{run_id}/gate")
    async def submit_gate_decision(run_id: str, body: GateDecisionRequest,
                                    request: Request) -> dict:
        gw = request.app.state.gateway
        run = gw.orchestrator_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        playbook_row = gw.orchestrator_store.get_playbook(run["playbook_id"])
        if playbook_row is None:
            raise HTTPException(status_code=404, detail="Playbook not found")
        playbook = Playbook.model_validate(playbook_row["definition_json"])
        try:
            await gw.playbook_runner.resume_with_decision(
                playbook, run_id, body.decision, edited_output=body.edited_output,
            )
        except InvalidDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RunStateError as exc:
            # Double-submitted decision, or a run that already moved on.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return gw.orchestrator_store.get_run(run_id)

    return router
