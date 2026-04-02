"""Learned weight parameters - adapts simulation/data weights based on trade outcomes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fishhook.utils.logging import get_logger

logger = get_logger("strategy.weights")


@dataclass
class WeightOutcome:
    simulation_signal: float
    data_signal: float
    combined_signal: float
    swarm_was_correct: bool
    data_was_correct: bool
    market_id: str
    timestamp: float = field(default_factory=time.time)


class AdaptiveWeightLearner:
    def __init__(
        self,
        initial_sim_weight: float = 0.6,
        initial_data_weight: float = 0.4,
        learning_rate: float = 0.05,
        min_weight: float = 0.1,
        max_weight: float = 0.9,
        window_size: int = 50,
    ) -> None:
        self._sim_weight = initial_sim_weight
        self._data_weight = initial_data_weight
        self._learning_rate = learning_rate
        self._min_weight = min_weight
        self._max_weight = max_weight
        self._window_size = window_size
        self._outcomes: list[WeightOutcome] = []
        self._category_weights: dict[str, tuple[float, float]] = {}

    @property
    def simulation_weight(self) -> float:
        return self._sim_weight

    @property
    def data_weight(self) -> float:
        return self._data_weight

    def get_weights(self, category: str = "") -> tuple[float, float]:
        if category and category in self._category_weights:
            return self._category_weights[category]
        return self._sim_weight, self._data_weight

    def record_outcome(
        self,
        simulation_signal: float,
        data_signal: float,
        combined_signal: float,
        actual_direction: float,
        market_id: str = "",
        category: str = "",
    ) -> None:
        predicted_dir = 1.0 if combined_signal > 0 else -1.0
        swarm_dir = 1.0 if simulation_signal > 0 else -1.0
        data_dir = 1.0 if data_signal > 0 else -1.0

        outcome = WeightOutcome(
            simulation_signal=simulation_signal,
            data_signal=data_signal,
            combined_signal=combined_signal,
            swarm_was_correct=(swarm_dir == actual_direction),
            data_was_correct=(data_dir == actual_direction),
            market_id=market_id,
        )
        self._outcomes.append(outcome)
        if len(self._outcomes) > self._window_size:
            self._outcomes = self._outcomes[-self._window_size :]

        self._update_weights()

        if category:
            self._update_category_weights(category)

    def _update_weights(self) -> None:
        if len(self._outcomes) < 5:
            return

        recent = self._outcomes[-20:]
        swarm_correct = sum(1 for o in recent if o.swarm_was_correct) / len(recent)
        data_correct = sum(1 for o in recent if o.data_was_correct) / len(recent)

        total = swarm_correct + data_correct
        if total > 0:
            target_sim = swarm_correct / total
            target_data = data_correct / total
        else:
            target_sim = 0.5
            target_data = 0.5

        target_sim = max(self._min_weight, min(self._max_weight, target_sim))
        target_data = max(self._min_weight, min(self._max_weight, target_data))

        self._sim_weight += self._learning_rate * (target_sim - self._sim_weight)
        self._data_weight += self._learning_rate * (target_data - self._data_weight)

        total_w = self._sim_weight + self._data_weight
        if total_w > 0:
            self._sim_weight /= total_w
            self._data_weight /= total_w

    def _update_category_weights(self, category: str) -> None:
        category_outcomes = [o for o in self._outcomes[-30:] if o.market_id]
        if len(category_outcomes) < 3:
            return

        swarm_correct = sum(1 for o in category_outcomes if o.swarm_was_correct) / len(
            category_outcomes
        )
        data_correct = sum(1 for o in category_outcomes if o.data_was_correct) / len(
            category_outcomes
        )

        total = swarm_correct + data_correct
        if total > 0:
            sim = max(self._min_weight, min(self._max_weight, swarm_correct / total))
            data = max(self._min_weight, min(self._max_weight, data_correct / total))
            self._category_weights[category] = (sim, data)

    def get_status(self) -> dict[str, Any]:
        recent = self._outcomes[-20:] if self._outcomes else []
        return {
            "simulation_weight": round(self._sim_weight, 4),
            "data_weight": round(self._data_weight, 4),
            "total_outcomes": len(self._outcomes),
            "recent_swarm_accuracy": round(
                sum(1 for o in recent if o.swarm_was_correct) / max(1, len(recent)), 4
            ),
            "recent_data_accuracy": round(
                sum(1 for o in recent if o.data_was_correct) / max(1, len(recent)), 4
            ),
            "category_weights": {
                k: {"sim": round(v[0], 4), "data": round(v[1], 4)}
                for k, v in self._category_weights.items()
            },
        }
