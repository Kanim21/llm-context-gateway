"""Provider resolution and credential handling for multi-provider routing.

Pure logic, no I/O -- mirrors core/routing.py's pattern. The actual HTTP
dispatch happens in adapters/.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_gateway.proxy.config import ProviderConfig, ProvidersConfig


@dataclass
class ProviderRegistry:
    config: ProvidersConfig

    def resolve(self, model: str) -> ProviderConfig:
        """Explicit `model_routes` entry wins; otherwise falls back to
        `default_provider` -- this is exactly today's implicit behavior,
        so any existing caller with an unmapped model keeps hitting real
        OpenAI unchanged."""
        provider_name = self.config.model_routes.get(model, self.config.default_provider)
        for entry in self.config.entries:
            if entry.name == provider_name:
                return entry
        raise ValueError(
            f"Provider {provider_name!r} (resolved for model {model!r}) "
            "has no matching entry in providers.entries"
        )
