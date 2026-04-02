"""Consensus tracking and emergent behavior detection."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fishhook.swarm.agent import Agent
from fishhook.utils.logging import get_logger

logger = get_logger("swarm.consensus")


@dataclass
class ConsensusState:
    round_number: int
    mean_opinion: float
    median_opinion: float
    std_deviation: float
    agreement_ratio: float
    polarization_index: float
    confidence_mean: float
    group_count: int
    dominant_direction: str
    strength: float
    distribution: dict[str, int] = field(default_factory=dict)

    @property
    def is_strong_consensus(self) -> bool:
        return self.agreement_ratio > 0.7 and self.std_deviation < 0.3

    @property
    def is_polarized(self) -> bool:
        return self.polarization_index > 0.5

    @property
    def bimodality_coefficient(self) -> float:
        return getattr(self, "_bimodality", 0.0)

    @property
    def polarization_type(self) -> str:
        bc = self.bimodality_coefficient
        if bc > 0.555:
            return "bimodal"
        elif self.polarization_index > 0.3 and self.confidence_mean > 0.5:
            return "high_conviction_split"
        elif self.polarization_index > 0.3:
            return "noisy_split"
        return "unimodal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_number,
            "mean_opinion": round(self.mean_opinion, 4),
            "median_opinion": round(self.median_opinion, 4),
            "std_dev": round(self.std_deviation, 4),
            "agreement_ratio": round(self.agreement_ratio, 4),
            "polarization": round(self.polarization_index, 4),
            "bimodality": round(self.bimodality_coefficient, 4),
            "polarization_type": self.polarization_type,
            "confidence": round(self.confidence_mean, 4),
            "groups": self.group_count,
            "direction": self.dominant_direction,
            "strength": round(self.strength, 4),
            "distribution": self.distribution,
        }


class ConsensusTracker:
    def __init__(self, threshold: float = 0.8) -> None:
        self._threshold = threshold
        self._history: list[ConsensusState] = []

    @property
    def history(self) -> list[ConsensusState]:
        return list(self._history)

    @property
    def latest(self) -> ConsensusState | None:
        return self._history[-1] if self._history else None

    @property
    def consensus_reached(self) -> bool:
        if not self._history:
            return False
        return self._history[-1].agreement_ratio >= self._threshold

    def compute(self, agents: list[Agent], round_num: int) -> ConsensusState:
        opinions = np.array([a.opinion for a in agents])
        confidences = np.array([a.confidence for a in agents])

        mean_op = float(np.mean(opinions))
        median_op = float(np.median(opinions))
        std_op = float(np.std(opinions))

        agreement_threshold = 0.3
        agreeing = np.sum(np.abs(opinions - mean_op) < agreement_threshold)
        agreement_ratio = float(agreeing / len(opinions)) if len(opinions) > 0 else 0.0

        bins = np.linspace(-1, 1, 21)
        hist, _ = np.histogram(opinions, bins=bins)
        hist_normalized = hist / hist.sum() if hist.sum() > 0 else hist
        polarization = float(np.std(hist_normalized))

        if mean_op > 0.1:
            direction = "bullish"
        elif mean_op < -0.1:
            direction = "bearish"
        else:
            direction = "neutral"

        strength = agreement_ratio * (1 - std_op) * float(np.mean(confidences))

        n = len(opinions)
        if n > 3 and std_op > 1e-6:
            mean_centered = opinions - mean_op
            skewness = float(np.mean(mean_centered**3) / (std_op**3))
            kurtosis = float(np.mean(mean_centered**4) / (std_op**4)) - 3.0
            bimodality = (skewness**2 + 1) / (
                kurtosis + 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))
            )
        else:
            bimodality = 0.0

        distribution = {
            "strong_bull": int(np.sum(opinions > 0.5)),
            "bull": int(np.sum((opinions > 0.1) & (opinions <= 0.5))),
            "neutral": int(np.sum(np.abs(opinions) <= 0.1)),
            "bear": int(np.sum((opinions < -0.1) & (opinions >= -0.5))),
            "strong_bear": int(np.sum(opinions < -0.5)),
        }

        groups = set()
        for a in agents:
            if a.group_id is not None:
                groups.add(a.group_id)

        state = ConsensusState(
            round_number=round_num,
            mean_opinion=mean_op,
            median_opinion=median_op,
            std_deviation=std_op,
            agreement_ratio=agreement_ratio,
            polarization_index=polarization,
            confidence_mean=float(np.mean(confidences)),
            group_count=len(groups),
            dominant_direction=direction,
            strength=strength,
            distribution=distribution,
        )
        state._bimodality = bimodality

        self._history.append(state)
        return state

    def get_opinion_trajectory(self, last_n: int = 10) -> list[float]:
        recent = self._history[-last_n:]
        return [s.mean_opinion for s in recent]

    def get_convergence_rate(self) -> float:
        if len(self._history) < 2:
            return 0.0
        stds = [s.std_deviation for s in self._history]
        if stds[0] < 0.01:
            return 1.0
        return 1.0 - (stds[-1] / stds[0])

    def detect_regime_change(self, window: int = 5) -> bool:
        if len(self._history) < window * 2:
            return False
        recent = self._history[-window:]
        previous = self._history[-window * 2 : -window]
        recent_mean = np.mean([s.mean_opinion for s in recent])
        prev_mean = np.mean([s.mean_opinion for s in previous])
        return abs(recent_mean - prev_mean) > 0.3

    def reset(self) -> None:
        self._history.clear()
