"""Simulation world - orchestrates the full swarm simulation."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from fishhook.config.settings import SwarmConfig
from fishhook.swarm.agent import Agent, AgentPersonality
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rounds": self.total_rounds,
            "final_consensus": self.final_consensus.to_dict(),
            "agent_count": self.agent_count,
            "social_network": self.social_network_stats,
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

    def inject_information(self, signal: float, source: str = "market_data") -> None:
        for agent in self._agents:
            noise = (1 - agent.personality.information_weight) * 0.2
            perceived_signal = signal + (random.gauss(0, 1) * noise if noise > 0 else 0)
            agent.observe_information(perceived_signal, source=source)

    def run_round(self, external_signal: float | None = None) -> ConsensusState:
        self._round += 1

        if external_signal is not None:
            self.inject_information(external_signal)

        for agent in self._agents:
            neighbor_opinions = self._social_network.get_neighbor_opinions(agent.id)
            agent.update_opinion(
                neighbor_opinions,
                external_signal=external_signal,
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
