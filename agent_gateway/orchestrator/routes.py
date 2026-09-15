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
    step_index: int
    edited_output: dict | None = None


def _last_completed_output(records: list[dict], before_index: int) -> dict:
    """The most recent completed step output before `before_index` (the gate's
    proposed output). Mirrors PlaybookRunner._last_completed_output so a
    snapshot-reconstructed gate_paused frame matches the live one."""
    for record in reversed(records):
        if record["step_index"] < before_index and record["status"] == "completed":
            return record["output_json"] or {"text": ""}
    return {"text": ""}


def _terminal_frame_from_snapshot(gw, run: dict) -> tuple[str, dict] | None:
    """If the run already reached a terminal/paused state, return the SSE
    (event_type, data) a live subscriber would have received. None means the
    run is still running -> subscribe live."""
    status = run["status"]
    run_id = run["id"]
    idx = run["current_step_index"]
    if status == "completed":
        return "run_completed", {}
    if status == "rejected":
        return "run_rejected", {"step_index": idx}
    if status == "failed":
        err = ""
        for record in gw.orchestrator_store.list_step_records(run_id):
            if record["status"] == "failed" and record["output_json"]:
                err = record["output_json"].get("error", "")
                break
        return "run_failed", {"step_index": idx, "error": err}
    if status == "paused":
        playbook_row = gw.orchestrator_store.get_playbook(run["playbook_id"])
        if playbook_row is not None:
            playbook = Playbook.model_validate(playbook_row["definition_json"])
            if 0 <= idx < len(playbook.steps) and playbook.steps[idx].type == "approval_gate":
                gate = playbook.steps[idx].gate
                records = gw.orchestrator_store.list_step_records(run_id)
                return "gate_paused", {
                    "step_index": idx, "step_id": playbook.steps[idx].step_id,
                    "label": gate.label, "allow_edit": gate.allow_edit,
                    "proposed_output": _last_completed_output(records, idx),
                }
        return "gate_paused", {"step_index": idx}
    return None


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

        def _frame(event_type: str, data: dict) -> bytes:
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()

        async def event_stream():
            run = gw.orchestrator_store.get_run(run_id)
            if run is None:
                return  # unknown run -> empty stream, closes immediately
            terminal = _terminal_frame_from_snapshot(gw, run)
            if terminal is not None:
                # Already finished/paused before this subscriber connected: the
                # live bus will never deliver a terminal event, so serve one
                # synthetic frame reconstructed from the snapshot and close.
                yield _frame(*terminal)
                return
            async for event_type, data in gw.event_bus.subscribe(run_id):
                yield _frame(event_type, data)

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
                playbook, run_id, body.decision, body.step_index,
                edited_output=body.edited_output,
            )
        except InvalidDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RunStateError as exc:
            # Double-submitted decision, or a run that already moved on.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return gw.orchestrator_store.get_run(run_id)

    return router
