"""Agent personality and memory system for swarm simulation."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np


@dataclass
class AgentPersonality:
    risk_tolerance: float = 0.5
    conformity_bias: float = 0.5
    information_weight: float = 0.5
    social_influence_susceptibility: float = 0.5
    memory_decay_rate: float = 0.05
    conviction_strength: float = 0.5

    @classmethod
    def random(cls) -> AgentPersonality:
        return cls(
            risk_tolerance=random.betavariate(2, 2),
            conformity_bias=random.betavariate(2, 2),
            information_weight=random.betavariate(2, 2),
            social_influence_susceptibility=random.betavariate(2, 2),
            memory_decay_rate=random.uniform(0.01, 0.15),
            conviction_strength=random.betavariate(2, 2),
        )

    def mutate(self, rate: float = 0.1) -> AgentPersonality:
        return AgentPersonality(
            risk_tolerance=self._mutate_value(self.risk_tolerance, rate),
            conformity_bias=self._mutate_value(self.conformity_bias, rate),
            information_weight=self._mutate_value(self.information_weight, rate),
            social_influence_susceptibility=self._mutate_value(
                self.social_influence_susceptibility, rate
            ),
            memory_decay_rate=self._mutate_value(
                self.memory_decay_rate, rate, 0.0, 1.0
            ),
            conviction_strength=self._mutate_value(self.conviction_strength, rate),
        )

    @staticmethod
    def _mutate_value(
        val: float, rate: float, lo: float = 0.0, hi: float = 1.0
    ) -> float:
        delta = random.gauss(0, rate)
        return max(lo, min(hi, val + delta))


@dataclass
class MemoryEntry:
    content: dict[str, Any]
    timestamp: datetime
    strength: float = 1.0
    source: str = "observation"

    def decay(self, rate: float) -> None:
        elapsed = (datetime.now() - self.timestamp).total_seconds() / 3600.0
        self.strength = math.exp(-rate * elapsed)


class AgentMemory:
    def __init__(self, max_entries: int = 100) -> None:
        self._entries: list[MemoryEntry] = []
        self._max = max_entries

    def add(self, content: dict[str, Any], source: str = "observation") -> None:
        entry = MemoryEntry(
            content=content,
            timestamp=datetime.now(),
            source=source,
        )
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries.pop(0)

    def recall(self, decay_rate: float, top_k: int = 10) -> list[MemoryEntry]:
        for entry in self._entries:
            entry.decay(decay_rate)
        self._entries = [e for e in self._entries if e.strength > 0.01]
        sorted_entries = sorted(self._entries, key=lambda e: e.strength, reverse=True)
        return sorted_entries[:top_k]

    def get_weighted_opinion_signal(self, decay_rate: float) -> float:
        memories = self.recall(decay_rate)
        if not memories:
            return 0.0
        signals = []
        weights = []
        for m in memories:
            if "opinion_signal" in m.content:
                signals.append(m.content["opinion_signal"])
                weights.append(m.strength)
        if not signals:
            return 0.0
        return float(np.average(signals, weights=weights))

    @property
    def count(self) -> int:
        return len(self._entries)


class Agent:
    _next_id = 0

    def __init__(self, personality: AgentPersonality | None = None) -> None:
        Agent._next_id += 1
        self.id = Agent._next_id
        self.personality = personality or AgentPersonality.random()
        self.memory = AgentMemory()
        self.opinion: float = random.uniform(-1.0, 1.0)
        self.confidence: float = random.uniform(0.1, 0.5)
        self.group_id: int | None = None
        self._social_connections: list[int] = []
        self._influence_score: float = 0.0

    @property
    def social_connections(self) -> list[int]:
        return list(self._social_connections)

    def add_connection(self, other_id: int) -> None:
        if other_id not in self._social_connections:
            self._social_connections.append(other_id)

    def remove_connection(self, other_id: int) -> None:
        if other_id in self._social_connections:
            self._social_connections.remove(other_id)

    def observe_information(self, signal: float, source: str = "data") -> None:
        self.memory.add(
            {"opinion_signal": signal, "current_opinion": self.opinion},
            source=source,
        )

    def update_opinion(
        self,
        neighbor_opinions: list[float],
        external_signal: float | None = None,
        noise_factor: float = 0.05,
    ) -> float:
        social_pull = 0.0
        if neighbor_opinions:
            weights = [1.0] * len(neighbor_opinions)
            social_pull = float(np.average(neighbor_opinions, weights=weights))

        memory_signal = self.memory.get_weighted_opinion_signal(
            self.personality.memory_decay_rate
        )

        components = []
        weights = []

        social_component = (
            social_pull * self.personality.social_influence_susceptibility
        )
        components.append(social_component)
        weights.append(self.personality.conformity_bias)

        if external_signal is not None:
            info_component = external_signal * self.personality.information_weight
            components.append(info_component)
            weights.append(self.personality.information_weight)

        if abs(memory_signal) > 0.01:
            mem_component = memory_signal * self.personality.memory_decay_rate * 10
            components.append(mem_component)
            weights.append(0.3)

        current_component = self.opinion * self.personality.conviction_strength
        components.append(current_component)
        weights.append(self.personality.conviction_strength)

        noise = random.gauss(0, noise_factor)
        components.append(noise)
        weights.append(0.1)

        total_weight = sum(weights)
        if total_weight > 0:
            new_opinion = sum(c * w for c, w in zip(components, weights)) / total_weight
        else:
            new_opinion = self.opinion

        self.opinion = max(-1.0, min(1.0, new_opinion))

        self.confidence = min(
            1.0, self.confidence + 0.01 * self.personality.conviction_strength
        )

        return self.opinion

    def get_vote(self) -> float:
        noise = random.gauss(0, 0.1 * (1 - self.confidence))
        return max(-1.0, min(1.0, self.opinion + noise))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "opinion": self.opinion,
            "confidence": self.confidence,
            "group_id": self.group_id,
            "connections": len(self._social_connections),
            "personality": {
                "risk_tolerance": self.personality.risk_tolerance,
                "conformity_bias": self.personality.conformity_bias,
                "conviction_strength": self.personality.conviction_strength,
            },
        }
