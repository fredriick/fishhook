"""Tests for PipelineOrchestrator behaviours (simulation config seeding)."""

import pytest

from fishhook.config.settings import PipelineConfig
from fishhook.orchestrator import PipelineOrchestrator


@pytest.mark.asyncio
async def test_simulation_uses_configured_swarm_heterogeneity(tmp_path) -> None:
    config = PipelineConfig(data_dir=tmp_path)
    config.swarm.num_agents = 2500
    config.swarm.heterogeneity.enabled = False

    orchestrator = PipelineOrchestrator(config)
    result = await orchestrator.run_simulation_only(
        signal=0.2,
        agents=2500,
        rounds=3,
    )

    assert result["heterogeneity"]["enabled"] is False
    assert result["heterogeneity"]["agents_with_traits"] == 0
    assert 1 <= result["rounds"] <= 3


@pytest.mark.asyncio
async def test_simulation_does_not_mutate_configured_swarm(tmp_path) -> None:
    config = PipelineConfig(data_dir=tmp_path)
    config.swarm.num_agents = 400

    orchestrator = PipelineOrchestrator(config)
    await orchestrator.run_simulation_only(
        signal=0.1,
        agents=2000,
        rounds=2,
    )

    assert orchestrator._config.swarm.num_agents == 400
    assert orchestrator._config.swarm.max_rounds == 50
    assert config.swarm.num_agents == 400