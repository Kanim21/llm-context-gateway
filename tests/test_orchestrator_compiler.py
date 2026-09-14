"""Tests for compile_canvas()'s 6 validation rules and the happy path."""

from __future__ import annotations

import pytest

from agent_gateway.orchestrator.compiler import CompilerError, compile_canvas


def _node(id, type, **data):
    return {"id": id, "type": type, "data": data}


def _linear_canvas():
    return {
        "nodes": [
            _node("start", "start"),
            _node("t1", "teammate", role="Qualifier", objective="Qualify the lead", tier="balanced"),
            _node("g1", "approval_gate", label="Review before routing"),
            _node("end", "end"),
        ],
        "edges": [
            {"source": "start", "target": "t1"},
            {"source": "t1", "target": "g1"},
            {"source": "g1", "target": "end"},
        ],
    }


class TestHappyPath:
    def test_linear_canvas_compiles_to_playbook_with_two_steps(self):
        playbook = compile_canvas(_linear_canvas(), playbook_id="pb_1", name="Test")
        assert playbook.id == "pb_1"
        assert [s.type for s in playbook.steps] == ["teammate", "approval_gate"]
        assert playbook.steps[0].teammate.role == "Qualifier"
        assert playbook.steps[1].gate.label == "Review before routing"


class TestRuleExactlyOneStart:
    def test_zero_start_nodes_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"] = [n for n in canvas["nodes"] if n["type"] != "start"]
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        assert "A playbook needs exactly one starting point." in [e["message"] for e in exc_info.value.errors]

    def test_two_start_nodes_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("start2", "start"))
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        assert "A playbook needs exactly one starting point." in [e["message"] for e in exc_info.value.errors]


class TestRuleOneOutgoingEdge:
    def test_dead_end_step_rejected(self):
        canvas = _linear_canvas()
        canvas["edges"] = [e for e in canvas["edges"] if e != {"source": "t1", "target": "g1"}]
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This step doesn't lead anywhere — connect it to the next step or mark it as the end." in messages

    def test_branching_step_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("t2", "teammate", role="R", objective="O", tier="speed"))
        canvas["edges"].append({"source": "t1", "target": "t2"})
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This step has two next steps — Playbooks run one step at a time." in messages


class TestRuleNoCycles:
    def test_cycle_rejected(self):
        canvas = _linear_canvas()
        canvas["edges"].append({"source": "g1", "target": "t1"})
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This playbook loops back on itself — playbooks run start to finish, once." in messages


class TestRuleNoOrphans:
    def test_disconnected_node_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("orphan", "teammate", role="R", objective="O", tier="speed"))
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This step isn't connected to the playbook — attach it to the chain or remove it." in messages


class TestRuleGateLabelRequired:
    def test_empty_gate_label_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "g1":
                n["data"]["label"] = ""
        with pytest.raises(CompilerError):
            compile_canvas(canvas, playbook_id="pb_1", name="Test")


class TestRuleTeammateRoleObjectiveRequired:
    def test_empty_role_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "t1":
                n["data"]["role"] = ""
        with pytest.raises(CompilerError):
            compile_canvas(canvas, playbook_id="pb_1", name="Test")

    def test_empty_objective_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "t1":
                n["data"]["objective"] = ""
        with pytest.raises(CompilerError):
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
