"""Configuration management for the pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ProxyConfig(BaseModel):
    enabled: bool = False
    rotation_interval_seconds: int = 300
    proxies: list[str] = Field(default_factory=list)


class ScraperConfig(BaseModel):
    headless: bool = True
    timeout_ms: int = 30000
    max_concurrent_pages: int = 5
    user_agent_rotation: bool = True
    intercept_requests: bool = True
    capture_dynamic_values: bool = True
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)


class AgentPersonalityConfig(BaseModel):
    risk_tolerance: float = Field(0.5, ge=0.0, le=1.0)
    conformity_bias: float = Field(0.5, ge=0.0, le=1.0)
    information_weight: float = Field(0.5, ge=0.0, le=1.0)
    social_influence_susceptibility: float = Field(0.5, ge=0.0, le=1.0)
    memory_decay_rate: float = Field(0.05, ge=0.0, le=1.0)
    conviction_strength: float = Field(0.5, ge=0.0, le=1.0)


class SwarmConfig(BaseModel):
    num_agents: int = 1000
    max_rounds: int = 50
    consensus_threshold: float = 0.8
    personality: AgentPersonalityConfig = Field(default_factory=AgentPersonalityConfig)
    social_connection_probability: float = 0.01
    opinion_update_rate: float = 0.1
    noise_factor: float = 0.05


class PolymarketConfig(BaseModel):
    api_base_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    chain_id: int = 137
    max_position_size: float = 100.0
    min_edge_threshold: float = 0.05
    testnet: bool = True


class StrategyConfig(BaseModel):
    divergence_threshold: float = 0.1
    min_confidence: float = 0.6
    simulation_weight: float = 0.6
    data_weight: float = 0.4
    cooldown_seconds: int = 60
    max_trades_per_hour: int = 10
    kelly_fraction: float = 0.25
    signal_ttl_seconds: int = 300


class DuneConfig(BaseModel):
    api_key: str = ""
    query_ids: list[int] = Field(default_factory=list)


class DataSourcesConfig(BaseModel):
    dune: DuneConfig = Field(default_factory=DuneConfig)
    orderbook_as_signal: bool = True


class CircuitBreakerConfig(BaseModel):
    enabled: bool = True
    max_drawdown_pct: float = 10.0
    drawdown_window_hours: float = 4.0
    max_consecutive_losses: int = 5
    max_api_errors_per_hour: int = 10
    cooldown_seconds: int = 300


class DeduplicatorConfig(BaseModel):
    enabled: bool = True
    similarity_threshold: float = 0.85
    window_seconds: int = 300


class CredibilityConfig(BaseModel):
    enabled: bool = True
    learning_rate: float = 0.05


class PipelineConfig(BaseSettings):
    model_config = {"env_prefix": "MCP_PARSE_"}

    data_dir: Path = Path("fishhook/data")
    log_level: str = "INFO"
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)
    swarm: SwarmConfig = Field(default_factory=SwarmConfig)
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    deduplicator: DeduplicatorConfig = Field(default_factory=DeduplicatorConfig)
    credibility: CredibilityConfig = Field(default_factory=CredibilityConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> PipelineConfig:
        path = Path(path)
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()

    def to_yaml(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(mode="json"), f, default_flow_style=False)
