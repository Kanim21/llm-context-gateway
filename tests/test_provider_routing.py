"""Tests for provider config schema, ProviderRegistry, and credential
resolution (agent_gateway.core.provider_routing)."""

from __future__ import annotations

from agent_gateway.proxy.config import GatewayConfig, ProviderConfig, ProvidersConfig


class TestProvidersConfig:
    def test_default_entries_has_openai_only(self):
        config = ProvidersConfig()
        assert len(config.entries) == 1
        assert config.entries[0].name == "openai"
        assert config.entries[0].wire_shape == "openai_compatible"
        assert config.entries[0].base_url == "https://api.openai.com/v1"
        assert config.entries[0].api_key_env == "OPENAI_API_KEY"

    def test_default_provider_is_openai(self):
        assert ProvidersConfig().default_provider == "openai"

    def test_gateway_config_has_providers(self):
        config = GatewayConfig()
        assert isinstance(config.providers, ProvidersConfig)

    def test_upstream_config_no_longer_has_openai_base_url(self):
        assert not hasattr(GatewayConfig().upstream, "openai_base_url")

    def test_provider_config_accepts_gemini_wire_shape(self):
        provider = ProviderConfig(
            name="gemini", wire_shape="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key_env="GEMINI_API_KEY",
        )
        assert provider.wire_shape == "gemini"
