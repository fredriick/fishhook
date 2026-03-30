"""Signal deduplicator - normalizes signals to canonical events before they enter the simulation."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.deduplicator")


@dataclass
class CanonicalSignal:
    canonical_id: str
    value: float
    source_count: int
    sources: list[str]
    category: str
    first_seen: float
    last_seen: float
    merged_values: list[float] = field(default_factory=list)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.first_seen

    def is_stale(self, ttl_seconds: int) -> bool:
        return self.age_seconds > ttl_seconds

    @property
    def blended_value(self) -> float:
        if not self.merged_values:
            return self.value
        return sum(self.merged_values) / len(self.merged_values)


class SignalDeduplicator:
    def __init__(
        self, similarity_threshold: float = 0.85, window_seconds: int = 300
    ) -> None:
        self._similarity_threshold = similarity_threshold
        self._window_seconds = window_seconds
        self._seen: dict[str, CanonicalSignal] = {}

    def _make_canonical_id(
        self, value: float, category: str, metadata: dict[str, Any]
    ) -> str:
        topic = metadata.get("topic", "")
        market_id = metadata.get("market_id", "")
        bucket = round(value, 1)
        raw = f"{category}:{topic}:{market_id}:{bucket}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def add(
        self,
        value: float,
        source: str,
        category: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> CanonicalSignal | None:
        metadata = metadata or {}
        canonical_id = self._make_canonical_id(value, category, metadata)
        now = time.time()

        self._evict_stale()

        if canonical_id in self._seen:
            existing = self._seen[canonical_id]
            if source not in existing.sources:
                existing.sources.append(source)
                existing.source_count = len(existing.sources)
            existing.merged_values.append(value)
            existing.value = existing.blended_value
            existing.last_seen = now
            return None

        signal = CanonicalSignal(
            canonical_id=canonical_id,
            value=value,
            source_count=1,
            sources=[source],
            category=category,
            first_seen=now,
            last_seen=now,
            merged_values=[value],
        )
        self._seen[canonical_id] = signal
        return signal

    def add_from_signals(self, signals: list[Any]) -> list[CanonicalSignal]:
        new_signals = []
        for sig in signals:
            metadata = getattr(sig, "metadata", {}) or {}
            metadata["market_id"] = metadata.get(
                "market_id", getattr(sig, "market_id", "")
            )
            result = self.add(
                value=getattr(sig, "value", 0.0),
                source=getattr(sig, "source_name", getattr(sig, "source", "unknown")),
                category=getattr(sig, "category", "general"),
                metadata=metadata,
            )
            if result:
                new_signals.append(result)
        return new_signals

    def get_active(self, max_age_seconds: int | None = None) -> list[CanonicalSignal]:
        cutoff = max_age_seconds or self._window_seconds
        now = time.time()
        return [s for s in self._seen.values() if now - s.last_seen <= cutoff]

    def _evict_stale(self) -> None:
        now = time.time()
        expired = [
            k
            for k, v in self._seen.items()
            if now - v.last_seen > self._window_seconds * 2
        ]
        for k in expired:
            del self._seen[k]

    @property
    def count(self) -> int:
        return len(self._seen)

    def clear(self) -> None:
        self._seen.clear()
