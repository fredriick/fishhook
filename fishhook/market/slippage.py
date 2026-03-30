"""Slippage model - estimates execution cost from order book depth."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from fishhook.market.models import OrderBook
from fishhook.utils.logging import get_logger

logger = get_logger("market.slippage")


@dataclass
class SlippageEstimate:
    mid_price: float
    expected_fill_price: float
    slippage_per_unit: float
    total_slippage_cost: float
    post_edge: float
    spread: float
    depth_ratio: float
    accept: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mid_price": round(self.mid_price, 4),
            "expected_fill_price": round(self.expected_fill_price, 4),
            "slippage_per_unit": round(self.slippage_per_unit, 4),
            "total_slippage_cost": round(self.total_slippage_cost, 4),
            "post_edge": round(self.post_edge, 4),
            "spread": round(self.spread, 4),
            "depth_ratio": round(self.depth_ratio, 4),
            "accept": self.accept,
            "reason": self.reason,
        }


class SlippageModel:
    def __init__(
        self,
        impact_coefficient: float = 0.1,
        min_acceptable_edge: float = 0.02,
    ) -> None:
        self._impact_coefficient = impact_coefficient
        self._min_acceptable_edge = min_acceptable_edge

    def estimate(
        self,
        order_book: OrderBook | None,
        side: str,
        price: float,
        size: float,
        edge: float,
    ) -> SlippageEstimate:
        if order_book is None or (not order_book.bids and not order_book.asks):
            return SlippageEstimate(
                mid_price=price,
                expected_fill_price=price,
                slippage_per_unit=0.0,
                total_slippage_cost=0.0,
                post_edge=edge,
                spread=0.0,
                depth_ratio=0.0,
                accept=True,
                reason="No order book data, assuming no slippage",
            )

        mid = order_book.mid_price
        spread = order_book.spread

        if side == "BUY":
            available_depth = order_book.ask_depth
        else:
            available_depth = order_book.bid_depth

        depth_ratio = size / max(0.001, available_depth)

        spread_slippage = spread / 2

        impact_slippage = self._impact_coefficient * math.sqrt(depth_ratio)

        total_slippage = spread_slippage + impact_slippage

        if side == "BUY":
            expected_fill = mid + total_slippage
        else:
            expected_fill = mid - total_slippage

        post_edge = edge - total_slippage

        if post_edge < self._min_acceptable_edge:
            accept = False
            reason = f"Post-slippage edge {post_edge:.4f} below minimum {self._min_acceptable_edge}"
        elif depth_ratio > 0.5:
            accept = False
            reason = f"Order consumes {depth_ratio:.0%} of available depth, too large"
        else:
            accept = True
            reason = (
                f"Slippage {total_slippage:.4f} acceptable, post-edge {post_edge:.4f}"
            )

        return SlippageEstimate(
            mid_price=mid,
            expected_fill_price=expected_fill,
            slippage_per_unit=total_slippage,
            total_slippage_cost=total_slippage * size,
            post_edge=post_edge,
            spread=spread,
            depth_ratio=depth_ratio,
            accept=accept,
            reason=reason,
        )

    def adjust_edge_for_slippage(self, edge: float, slippage: float) -> float:
        return edge - slippage
