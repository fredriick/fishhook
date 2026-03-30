"""Portfolio heat limits - prevents correlated ruin by tracking exposure across positions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("strategy.heat")


@dataclass
class PositionExposure:
    market_id: str
    category: str
    direction: str
    notional: float
    entry_time: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "category": self.category,
            "direction": self.direction,
            "notional": round(self.notional, 2),
        }


class PortfolioHeatTracker:
    def __init__(
        self,
        max_total_exposure: float = 500.0,
        max_category_exposure: float = 200.0,
        max_single_position: float = 100.0,
        max_correlated_positions: int = 5,
    ) -> None:
        self._max_total_exposure = max_total_exposure
        self._max_category_exposure = max_category_exposure
        self._max_single_position = max_single_position
        self._max_correlated_positions = max_correlated_positions
        self._positions: dict[str, PositionExposure] = {}
        self._category_map: dict[str, str] = {}

    def register_market(self, market_id: str, category: str) -> None:
        self._category_map[market_id] = category

    def check_can_add(
        self,
        market_id: str,
        direction: str,
        notional: float,
    ) -> tuple[bool, str]:
        if notional > self._max_single_position:
            return (
                False,
                f"Position ${notional:.0f} exceeds single max ${self._max_single_position}",
            )

        current_total = sum(p.notional for p in self._positions.values())
        if current_total + notional > self._max_total_exposure:
            return (
                False,
                f"Total exposure ${current_total + notional:.0f} would exceed max ${self._max_total_exposure}",
            )

        category = self._category_map.get(market_id, "uncategorized")
        category_total = sum(
            p.notional for p in self._positions.values() if p.category == category
        )
        if category_total + notional > self._max_category_exposure:
            return (
                False,
                f"Category '{category}' exposure ${category_total + notional:.0f} would exceed max ${self._max_category_exposure}",
            )

        same_direction = [
            p for p in self._positions.values() if p.direction == direction
        ]
        if len(same_direction) >= self._max_correlated_positions:
            return (
                False,
                f"Too many {direction} positions ({len(same_direction)} >= {self._max_correlated_positions})",
            )

        if market_id in self._positions:
            existing = self._positions[market_id]
            if existing.direction != direction:
                return True, "OK (reversal)"
            return True, "OK (adding to existing)"

        return True, "OK"

    def add_position(
        self,
        market_id: str,
        direction: str,
        notional: float,
    ) -> None:
        category = self._category_map.get(market_id, "uncategorized")
        if market_id in self._positions:
            existing = self._positions[market_id]
            if existing.direction == direction:
                existing.notional += notional
            else:
                existing.notional = notional
                existing.direction = direction
        else:
            self._positions[market_id] = PositionExposure(
                market_id=market_id,
                category=category,
                direction=direction,
                notional=notional,
                entry_time=time.time(),
            )

    def remove_position(self, market_id: str) -> None:
        self._positions.pop(market_id, None)

    def update_notional(self, market_id: str, notional: float) -> None:
        if market_id in self._positions:
            self._positions[market_id].notional = notional

    @property
    def total_exposure(self) -> float:
        return sum(p.notional for p in self._positions.values())

    @property
    def position_count(self) -> int:
        return len(self._positions)

    def get_category_exposure(self, category: str) -> float:
        return sum(
            p.notional for p in self._positions.values() if p.category == category
        )

    def get_direction_exposure(self, direction: str) -> float:
        return sum(
            p.notional for p in self._positions.values() if p.direction == direction
        )

    def get_status(self) -> dict[str, Any]:
        categories: dict[str, float] = {}
        directions: dict[str, float] = {}
        for p in self._positions.values():
            categories[p.category] = categories.get(p.category, 0) + p.notional
            directions[p.direction] = directions.get(p.direction, 0) + p.notional

        return {
            "positions": len(self._positions),
            "total_exposure": round(self.total_exposure, 2),
            "max_total_exposure": self._max_total_exposure,
            "utilization_pct": round(
                self.total_exposure / max(1, self._max_total_exposure) * 100, 1
            ),
            "by_category": {k: round(v, 2) for k, v in categories.items()},
            "by_direction": {k: round(v, 2) for k, v in directions.items()},
            "limits": {
                "max_total": self._max_total_exposure,
                "max_category": self._max_category_exposure,
                "max_single": self._max_single_position,
                "max_correlated": self._max_correlated_positions,
            },
        }

    def clear(self) -> None:
        self._positions.clear()
