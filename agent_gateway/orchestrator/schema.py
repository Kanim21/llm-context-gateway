"""Canonical compiled-playbook schema. `compiler.py` produces a `Playbook`
from raw canvas JSON; `engine.py` consumes it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TeammateConfig(BaseModel):
    role: str
    objective: str
    tier: Literal["speed", "balanced", "brain"]
    connected_apps: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)


class GateConfig(BaseModel):
    label: str
    allow_edit: bool = True


class PlaybookStep(BaseModel):
    step_id: str
    type: Literal["teammate", "approval_gate"]
    teammate: TeammateConfig | None = None
    gate: GateConfig | None = None

    @model_validator(mode="after")
    def _check_config_matches_type(self) -> "PlaybookStep":
        if self.type == "teammate" and self.teammate is None:
            raise ValueError("teammate step requires teammate config")
        if self.type == "approval_gate" and self.gate is None:
            raise ValueError("approval_gate step requires gate config")
        return self


class Playbook(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str = "default"
    name: str
    description: str = ""
    steps: list[PlaybookStep]
