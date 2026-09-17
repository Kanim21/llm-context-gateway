"""Tests for the playbook_tiers config field."""

from __future__ import annotations

import json

from agent_gateway.proxy.config import GatewayConfig


class TestPlaybookTiersConfig:
    def test_default_tiers(self):
        config = GatewayConfig()
        assert config.playbook_tiers == {
            "speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro",
        }

    def test_load_overrides_tiers_from_json(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"playbook_tiers": {"speed": "custom-fast-model"}}))
        config = GatewayConfig.load(str(path))
        assert config.playbook_tiers == {"speed": "custom-fast-model"}
