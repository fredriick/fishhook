"""Tests for agent heterogeneity (info access, update frequency, memory)."""

import pytest

from fishhook.config.settings import SwarmConfig
from fishhook.swarm.agent import Agent, AgentHeterogeneity
from fishhook.swarm.world import SimulationWorld


# --- update frequency -------------------------------------------------------


@pytest.mark.parametrize("freq,rounds,expected", [(1, 5, True), (2, 1, False)])
def test_update_frequency_gating(freq: int, rounds: int, expected: bool) -> None:
    agent = Agent(
        heterogeneity=AgentHeterogeneity(update_frequency=freq)
    )
    for _ in range(rounds):
        agent.tick()
    assert agent.should_update_this_round() is expected


def test_slow_agents_skip_updates_in_world() -> None:
    world = SimulationWorld(
        SwarmConfig(
            num_agents=10,
            max_rounds=3,
            heterogeneity={"enabled": False},
        )
    )
    world.initialize()
    assert all(a.heterogeneity is None for a in world.agents)


# --- info access ------------------------------------------------------------


def test_info_access_zero_blocks_all_signals() -> None:
    world = SimulationWorld(
        SwarmConfig(
            num_agents=5,
            max_rounds=2,
            heterogeneity={"enabled": False},
        )
    )
    world.initialize()

    for agent in world.agents:
        agent.heterogeneity = AgentHeterogeneity(info_access=0.0)

    world.inject_information(0.6)
    assert all(agent.memory.count == 0 for agent in world.agents)


def test_info_access_one_reaches_everyone() -> None:
    world = SimulationWorld(
        SwarmConfig(
            num_agents=5,
            max_rounds=2,
            heterogeneity={"enabled": False},
        )
    )
    world.initialize()

    for agent in world.agents:
        agent.heterogeneity = AgentHeterogeneity(info_access=1.0)

    world.inject_information(0.6)
    assert all(agent.memory.count == 1 for agent in world.agents)


# --- memory capacity --------------------------------------------------------


def test_memory_capacity_limits_history() -> None:
    agent = Agent(
        heterogeneity=AgentHeterogeneity(memory_capacity=3)
    )
    for i in range(5):
        agent.observe_information(i / 10.0)

    assert agent.memory.capacity == 3
    assert agent.memory.count == 3


# --- world integration ------------------------------------------------------


def test_world_assigns_heterogeneity_by_default() -> None:
    world = SimulationWorld(SwarmConfig(num_agents=25, max_rounds=3))
    world.initialize()

    traits = [a.heterogeneity for a in world.agents]
    assert all(t is not None for t in traits)
    assert 0 <= min(t.info_access for t in traits) <= max(
        t.info_access for t in traits
    ) <= 1.0
    assert max(t.update_frequency for t in traits) >= 1
    assert all(20 <= t.memory_capacity <= 200 for t in traits)


@pytest.mark.asyncio
async def test_heterogeneity_stats_in_simulation() -> None:
    world = SimulationWorld(
        SwarmConfig(
            num_agents=15,
            max_rounds=3,
            heterogeneity={
                "enabled": True,
                "info_access_min": 0.5,
                "max_update_frequency": 3,
            },
        )
    )
    result = await world.run_simulation(signals=[0.3, 0.3, 0.3])

    assert result.heterogeneity_stats["enabled"] is True
    assert result.heterogeneity_stats["agents_with_traits"] == 15
    assert result.heterogeneity_stats["mean_info_access"] >= 0.5
    assert set(result.heterogeneity_stats["update_frequency"]) <= {"1", "2", "3"}
    assert result.to_dict()["heterogeneity"]["enabled"] is True