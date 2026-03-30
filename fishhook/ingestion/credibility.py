"""Source credibility scorer - weights signals by origin reliability."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.credibility")


@dataclass
class SourceScore:
    domain: str
    score: float
    total_signals: int
    correct_signals: int
    last_updated: float = field(default_factory=time.time)

    @property
    def accuracy(self) -> float:
        if self.total_signals == 0:
            return 0.5
        return self.correct_signals / self.total_signals

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "score": round(self.score, 4),
            "accuracy": round(self.accuracy, 4),
            "total_signals": self.total_signals,
            "correct_signals": self.correct_signals,
        }


class CredibilityScorer:
    DEFAULT_SCORE = 0.5
    MIN_SCORE = 0.1
    MAX_SCORE = 0.95

    TRUSTED_DOMAINS: dict[str, float] = {
        "reuters.com": 0.85,
        "bloomberg.com": 0.85,
        "apnews.com": 0.8,
        "coindesk.com": 0.7,
        "cointelegraph.com": 0.65,
        "polymarket.com": 0.75,
        "predictit.org": 0.7,
        "twitter.com": 0.5,
        "x.com": 0.5,
        "reddit.com": 0.45,
    }

    def __init__(self, learning_rate: float = 0.05) -> None:
        self._learning_rate = learning_rate
        self._scores: dict[str, SourceScore] = {}
        self._pending_outcomes: dict[str, list[dict[str, Any]]] = {}

        for domain, score in self.TRUSTED_DOMAINS.items():
            self._scores[domain] = SourceScore(
                domain=domain, score=score, total_signals=0, correct_signals=0
            )

    def get_score(self, source: str) -> float:
        domain = self._extract_domain(source)
        if domain in self._scores:
            return self._scores[domain].score
        return self.DEFAULT_SCORE

    def get_weighted_value(self, value: float, source: str) -> float:
        credibility = self.get_score(source)
        return value * credibility

    def record_signal(
        self, source: str, predicted_direction: float, market_id: str = ""
    ) -> None:
        domain = self._extract_domain(source)
        if domain not in self._pending_outcomes:
            self._pending_outcomes[domain] = []
        self._pending_outcomes[domain].append(
            {
                "predicted_direction": predicted_direction,
                "market_id": market_id,
                "timestamp": time.time(),
            }
        )

    def resolve_outcome(self, market_id: str, actual_direction: float) -> None:
        for domain, pending in list(self._pending_outcomes.items()):
            remaining = []
            for record in pending:
                if record["market_id"] == market_id:
                    predicted = record["predicted_direction"]
                    correct = (predicted > 0 and actual_direction > 0) or (
                        predicted < 0 and actual_direction < 0
                    )
                    self._update_score(domain, correct)
                else:
                    remaining.append(record)
            if remaining:
                self._pending_outcomes[domain] = remaining
            else:
                del self._pending_outcomes[domain]

    def _update_score(self, domain: str, correct: bool) -> None:
        if domain not in self._scores:
            self._scores[domain] = SourceScore(
                domain=domain,
                score=self.DEFAULT_SCORE,
                total_signals=0,
                correct_signals=0,
            )
        record = self._scores[domain]
        record.total_signals += 1
        if correct:
            record.correct_signals += 1
            record.score = min(
                self.MAX_SCORE,
                record.score + self._learning_rate * (1 - record.score),
            )
        else:
            record.score = max(
                self.MIN_SCORE,
                record.score - self._learning_rate * record.score,
            )
        record.last_updated = time.time()

    @staticmethod
    def _extract_domain(source: str) -> str:
        source = source.lower().strip()
        for prefix in ("https://", "http://", "www."):
            if source.startswith(prefix):
                source = source[len(prefix) :]
        return source.split("/")[0]

    def get_all_scores(self) -> list[SourceScore]:
        return sorted(self._scores.values(), key=lambda s: s.score, reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": {d: s.to_dict() for d, s in self._scores.items()},
            "default_score": self.DEFAULT_SCORE,
            "pending_outcomes": {d: len(v) for d, v in self._pending_outcomes.items()},
        }
