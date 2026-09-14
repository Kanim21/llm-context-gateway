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
        # Should only have the start-count error, not false-positive orphan errors (Finding 1)
        assert len(exc_info.value.errors) == 1
        assert exc_info.value.errors[0]["message"] == "A playbook needs exactly one starting point."

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
        # Should report dead end for t1 (no outgoing edge)
        dead_end_errors = [e for e in exc_info.value.errors
                          if e["message"] == "This step doesn't lead anywhere — connect it to the next step or mark it as the end."]
        assert len(dead_end_errors) > 0
        assert dead_end_errors[0]["node_id"] == "t1"

    def test_branching_step_rejected(self):
        canvas = _linear_canvas()
        canvas["nodes"].append(_node("t2", "teammate", role="R", objective="O", tier="speed"))
        canvas["edges"].append({"source": "t1", "target": "t2"})
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # Should report branching for t1 (two outgoing edges)
        branching_errors = [e for e in exc_info.value.errors
                           if e["message"] == "This step has two next steps — Playbooks run one step at a time."]
        assert len(branching_errors) > 0
        assert branching_errors[0]["node_id"] == "t1"


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
        # Should report orphan for the disconnected node
        orphan_errors = [e for e in exc_info.value.errors
                        if e["message"] == "This step isn't connected to the playbook — attach it to the chain or remove it."]
        assert len(orphan_errors) > 0
        assert orphan_errors[0]["node_id"] == "orphan"


class TestRuleGateLabelRequired:
    def test_empty_gate_label_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "g1":
                n["data"]["label"] = ""
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # Should report gate label error with correct node_id
        gate_errors = [e for e in exc_info.value.errors
                      if e["message"] == "This approval gate needs a label."]
        assert len(gate_errors) > 0
        assert gate_errors[0]["node_id"] == "g1"


class TestRuleTeammateRoleObjectiveRequired:
    def test_empty_role_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "t1":
                n["data"]["role"] = ""
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # Should report role error with correct node_id
        role_errors = [e for e in exc_info.value.errors
                      if e["message"] == "This teammate needs a role."]
        assert len(role_errors) > 0
        assert role_errors[0]["node_id"] == "t1"

    def test_empty_objective_rejected(self):
        canvas = _linear_canvas()
        for n in canvas["nodes"]:
            if n["id"] == "t1":
                n["data"]["objective"] = ""
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # Should report objective error with correct node_id
        objective_errors = [e for e in exc_info.value.errors
                           if e["message"] == "This teammate needs an objective."]
        assert len(objective_errors) > 0
        assert objective_errors[0]["node_id"] == "t1"


# Additional tests for findings from the review
class TestFinding1ZeroStartNoFalseOrphans:
    """Finding 1: Zero start nodes should only produce start-count error, not false orphan errors."""
    def test_zero_start_nodes_valid_chain_no_false_orphans(self):
        canvas = {
            "nodes": [
                _node("t1", "teammate", role="Qualifier", objective="Qualify the lead", tier="balanced"),
                _node("g1", "approval_gate", label="Review"),
                _node("end", "end"),
            ],
            "edges": [
                {"source": "t1", "target": "g1"},
                {"source": "g1", "target": "end"},
            ],
        }
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # Should only have the start-count error, not orphan errors for t1 and g1
        assert len(exc_info.value.errors) == 1
        assert exc_info.value.errors[0]["message"] == "A playbook needs exactly one starting point."


class TestFinding2EndNodeTraversalStop:
    """Finding 2: Traversal must stop at end nodes; end with outgoing edge should not pull downstream."""
    def test_end_node_with_outgoing_edge_no_downstream(self):
        canvas = {
            "nodes": [
                _node("start", "start"),
                _node("t1", "teammate", role="R", objective="O", tier="balanced"),
                _node("end", "end"),
                _node("t2", "teammate", role="R", objective="O", tier="balanced"),
            ],
            "edges": [
                {"source": "start", "target": "t1"},
                {"source": "t1", "target": "end"},
                {"source": "end", "target": "t2"},  # Malformed: edge out of end node
            ],
        }
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # t2 should be flagged as orphan, not silently included in steps
        orphan_errors = [e for e in exc_info.value.errors
                        if e["message"] == "This step isn't connected to the playbook — attach it to the chain or remove it."]
        assert len(orphan_errors) > 0
        assert orphan_errors[0]["node_id"] == "t2"


class TestFinding3CycleInDisconnectedComponent:
    """Finding 3: Cycle detection must find cycles in all components, not just start-reachable."""
    def test_cycle_in_disconnected_component_detected(self):
        canvas = {
            "nodes": [
                _node("start", "start"),
                _node("t1", "teammate", role="R", objective="O", tier="balanced"),
                _node("end", "end"),
                # Disconnected cycle: A -> B -> A
                _node("a", "teammate", role="R", objective="O", tier="balanced"),
                _node("b", "teammate", role="R", objective="O", tier="balanced"),
            ],
            "edges": [
                {"source": "start", "target": "t1"},
                {"source": "t1", "target": "end"},
                # Cycle in disconnected component
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ],
        }
        with pytest.raises(CompilerError) as exc_info:
            compile_canvas(canvas, playbook_id="pb_1", name="Test")
        # Must report cycle message, not just orphan message
        messages = [e["message"] for e in exc_info.value.errors]
        assert "This playbook loops back on itself — playbooks run start to finish, once." in messages
