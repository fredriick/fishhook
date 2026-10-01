"""Simulation world - orchestrates the full swarm simulation."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fishhook.config.settings import SwarmConfig
from fishhook.swarm.agent import Agent, AgentHeterogeneity, AgentMemory, AgentPersonality
from fishhook.swarm.consensus import ConsensusState, ConsensusTracker
from fishhook.swarm.social import SocialNetwork
from fishhook.utils.logging import get_logger

logger = get_logger("swarm.world")


@dataclass
class SimulationResult:
    total_rounds: int
    final_consensus: ConsensusState
    consensus_history: list[ConsensusState]
    agent_count: int
    social_network_stats: dict[str, Any]
    elapsed_seconds: float
    converged: bool
    regime_changes: int
    final_distribution: dict[str, int]
    heterogeneity_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rounds": self.total_rounds,
            "final_consensus": self.final_consensus.to_dict(),
            "agent_count": self.agent_count,
            "social_network": self.social_network_stats,
            "heterogeneity": self.heterogeneity_stats,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "converged": self.converged,
            "regime_changes": self.regime_changes,
            "distribution": self.final_distribution,
        }


class SimulationWorld:
    def __init__(self, config: SwarmConfig | None = None) -> None:
        self._config = config or SwarmConfig()
        self._agents: list[Agent] = []
        self._social_network = SocialNetwork(self._config.social_connection_probability)
        self._consensus = ConsensusTracker(self._config.consensus_threshold)
        self._round = 0
        self._regime_changes = 0

    @property
    def agents(self) -> list[Agent]:
        return list(self._agents)

    @property
    def consensus(self) -> ConsensusTracker:
        return self._consensus

    @property
    def social_network(self) -> SocialNetwork:
        return self._social_network

    def initialize(self, num_agents: int | None = None) -> None:
        n = num_agents or self._config.num_agents
        logger.info(f"Initializing swarm with {n} agents")

        Agent.reset_id_counter()

        base_personality = AgentPersonality(
            risk_tolerance=self._config.personality.risk_tolerance,
            conformity_bias=self._config.personality.conformity_bias,
            information_weight=self._config.personality.information_weight,
            social_influence_susceptibility=self._config.personality.social_influence_susceptibility,
            memory_decay_rate=self._config.personality.memory_decay_rate,
            conviction_strength=self._config.personality.conviction_strength,
        )
        self._agents = [Agent(base_personality.mutate(rate=0.2)) for _ in range(n)]

        self._assign_heterogeneity()

        self._social_network.build_from_agents(self._agents)

        communities = self._social_network.detect_communities()
        for agent in self._agents:
            agent.group_id = communities.get(agent.id, 0)

        self._consensus.reset()
        self._round = 0
        self._regime_changes = 0

        logger.info(
            f"Swarm initialized: {n} agents, groups: {max(communities.values()) + 1 if communities else 0}"
        )

    def _assign_heterogeneity(self) -> None:
        """Vary information access, update cadence and memory horizon."""

        cfg = self._config.heterogeneity
        if not cfg.enabled:
            return

        for agent in self._agents:
            info_access = random.uniform(cfg.info_access_min, 1.0)
            skew = random.random() ** cfg.update_frequency_power
            update_frequency = 1 + int(
                skew * (cfg.max_update_frequency - 1)
            )
            memory_capacity = random.randint(20, cfg.memory_capacity_max)
            agent.heterogeneity = AgentHeterogeneity(
                info_access=info_access,
                update_frequency=update_frequency,
                memory_capacity=memory_capacity,
            )
            agent.memory = AgentMemory(max_entries=memory_capacity)

    def get_heterogeneity_stats(self) -> dict[str, Any]:
        if not self._agents:
            return {"enabled": False}
        traits = [a.heterogeneity for a in self._agents if a.heterogeneity]
        access = [t.info_access for t in traits]
        freqs = [t.update_frequency for t in traits]
        caps = [t.memory_capacity for t in traits]

        return {
            "enabled": bool(traits),
            "agents_with_traits": len(traits),
            "mean_info_access": round(float(np.mean(access)), 3) if access else 1.0,
            "info_access_min": round(min(access), 3) if access else 1.0,
            "info_access_max": round(max(access), 3) if access else 1.0,
            "update_frequency": {
                str(f): freqs.count(f) for f in sorted(set(freqs))
            },
            "mean_memory_capacity": (
                round(float(np.mean(caps)), 1) if caps else 100.0
            ),
        }

    def inject_information(
        self,
        signal: float,
        source: str = "market_data",
        perception: dict[int, bool] | None = None,
    ) -> None:
        """Feed a signal into the swarm; agents perceive it by their own
        ``info_access`` probability."""
        for agent in self._agents:
            perceived = True
            if agent.heterogeneity is not None:
                perceived = random.random() < agent.heterogeneity.info_access
            if perception is not None:
                perception[agent.id] = perceived
            if not perceived:
                continue
            noise = (1 - agent.personality.information_weight) * 0.2
            perceived_signal = signal + (random.gauss(0, 1) * noise if noise > 0 else 0)
            agent.observe_information(perceived_signal, source=source)

    def run_round(self, external_signal: float | None = None) -> ConsensusState:
        self._round += 1

        perception: dict[int, bool] = {}
        if external_signal is not None:
            self.inject_information(external_signal, perception=perception)

        for agent in self._agents:
            agent.tick()

            signal = external_signal
            if external_signal is not None and not perception.get(agent.id, True):
                signal = None

            if not agent.should_update_this_round():
                continue

            neighbor_opinions = self._social_network.get_neighbor_opinions(agent.id)
            agent.update_opinion(
                neighbor_opinions,
                external_signal=signal,
                noise_factor=self._config.noise_factor,
            )

        state = self._consensus.compute(self._agents, self._round)

        if self._consensus.detect_regime_change():
            self._regime_changes += 1
            logger.info(f"Regime change detected at round {self._round}")

        if self._round % 10 == 0:
            communities = self._social_network.detect_communities()
            for agent in self._agents:
                agent.group_id = communities.get(agent.id, 0)

        return state

    async def run_simulation(
        self,
        signals: list[float] | None = None,
        max_rounds: int | None = None,
    ) -> SimulationResult:
        start = time.time()
        rounds = max_rounds or self._config.max_rounds

        if not self._agents:
            self.initialize()

        for r in range(rounds):
            signal = None
            if signals and r < len(signals):
                signal = signals[r]

            state = self.run_round(external_signal=signal)

            if self._consensus.consensus_reached:
                logger.info(
                    f"Consensus reached at round {r + 1}: {state.dominant_direction} ({state.agreement_ratio:.2f})"
                )
                break

            if r % 10 == 0:
                await asyncio.sleep(0)

        elapsed = time.time() - start
        final = self._consensus.latest or ConsensusState(
            round_number=0,
            mean_opinion=0,
            median_opinion=0,
            std_deviation=1,
            agreement_ratio=0,
            polarization_index=0,
            confidence_mean=0,
            group_count=0,
            dominant_direction="neutral",
            strength=0,
        )

        return SimulationResult(
            total_rounds=self._round,
            final_consensus=final,
            consensus_history=self._consensus.history,
            agent_count=len(self._agents),
            social_network_stats=self._social_network.get_stats(),
            elapsed_seconds=elapsed,
            converged=self._consensus.consensus_reached,
            regime_changes=self._regime_changes,
            final_distribution=final.distribution,
            heterogeneity_stats=self.get_heterogeneity_stats(),
        )

    def get_swarm_signal(self) -> dict[str, Any]:
        if not self._consensus.latest:
            return {"signal": 0, "confidence": 0, "direction": "neutral"}

        state = self._consensus.latest
        return {
            "signal": state.mean_opinion,
            "confidence": state.confidence_mean,
            "direction": state.dominant_direction,
            "agreement": state.agreement_ratio,
            "strength": state.strength,
            "polarization": state.polarization_index,
        }
