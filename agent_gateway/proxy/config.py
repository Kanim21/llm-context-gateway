"""Pydantic configuration for the gateway process."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UpstreamConfig(BaseModel):
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    request_timeout_s: float = 60.0


class ProviderConfig(BaseModel):
    name: str
    wire_shape: Literal["openai_compatible", "gemini"]
    base_url: str
    api_key_env: str | None = None
    api_key_header: str | None = None


class ProvidersConfig(BaseModel):
    entries: list[ProviderConfig] = Field(
        default_factory=lambda: [
            ProviderConfig(
                name="openai",
                wire_shape="openai_compatible",
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
            ),
        ]
    )
    model_routes: dict[str, str] = Field(default_factory=dict)
    default_provider: str = "openai"


class MaskingConfig(BaseModel):
    trailing_k: int = 2


class CompactionConfig(BaseModel):
    token_threshold: int = 4000
    keep_recent_turns: int = 4
    tokenizer: str = "cl100k_base"


class CircuitBreakerConfig(BaseModel):
    max_turns: int | None = 200
    max_cumulative_tokens: int | None = 2_000_000


class RoutingConfig(BaseModel):
    flagship_model: str = "gpt-4o"
    tier2_model: str = "gpt-4o-mini"


class LossyDefaultsConfig(BaseModel):
    """All False per spec Sec 3.3 -- lossy passes default OFF."""

    structural_stripping: bool = False
    eco_trimming: bool = False
    pruning: bool = False
    numeric_quantization: bool = False
    query_filtering: bool = False


class StorageConfig(BaseModel):
    sqlite_path: str = "./data/agent_gateway.db"
    use_zclaw_codec: bool = True


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    # Browser origins allowed to call the API (CORS). Default: the Next.js dev
    # server. Override for real deployments; "*" is intentionally not the
    # default because the API resolves per-request credentials from headers.
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
    masking: MaskingConfig = Field(default_factory=MaskingConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    lossy_defaults: LossyDefaultsConfig = Field(default_factory=LossyDefaultsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    playbook_tiers: dict[str, str] = Field(
        default_factory=lambda: {
            "speed": "gpt-4o-mini", "balanced": "gpt-4o", "brain": "gemini-1.5-pro",
        }
    )

    @classmethod
    def load(cls, path: str | None = None) -> "GatewayConfig":
        if path is None:
            return cls()
        import json
        from pathlib import Path

        data = json.loads(Path(path).read_text())
        return cls.model_validate(data)
