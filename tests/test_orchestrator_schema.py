"""Tests for the orchestrator's canonical Pydantic schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_gateway.orchestrator.schema import (
    GateConfig,
    Playbook,
    PlaybookStep,
    TeammateConfig,
)


class TestTeammateConfig:
    def test_defaults(self):
        teammate = TeammateConfig(role="Sales Qualifier", objective="Qualify inbound leads", tier="balanced")
        assert teammate.connected_apps == []
        assert teammate.knowledge == []

    def test_invalid_tier_rejected(self):
        with pytest.raises(ValidationError):
            TeammateConfig(role="X", objective="Y", tier="ultra")


class TestGateConfig:
    def test_allow_edit_defaults_true(self):
        assert GateConfig(label="Review before sending").allow_edit is True


class TestPlaybookStep:
    def test_teammate_step_requires_teammate_config(self):
        with pytest.raises(ValidationError):
            PlaybookStep(step_id="s1", type="teammate", teammate=None)

    def test_approval_gate_step_requires_gate_config(self):
        with pytest.raises(ValidationError):
            PlaybookStep(step_id="s1", type="approval_gate", gate=None)

    def test_valid_teammate_step(self):
        step = PlaybookStep(
            step_id="s1", type="teammate",
            teammate=TeammateConfig(role="R", objective="O", tier="speed"),
        )
        assert step.teammate.role == "R"

    def test_valid_gate_step(self):
        step = PlaybookStep(step_id="s2", type="approval_gate", gate=GateConfig(label="Review"))
        assert step.gate.label == "Review"


class TestPlaybook:
    def test_defaults(self):
        playbook = Playbook(id="pb_1", name="My Playbook", steps=[])
        assert playbook.schema_version == 1
        assert playbook.workspace_id == "default"
        assert playbook.description == ""
