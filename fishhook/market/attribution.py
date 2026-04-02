"""P&L edge attribution - tracks realized vs predicted edge to diagnose underperformance."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fishhook.utils.logging import get_logger

logger = get_logger("market.attribution")


@dataclass
class TradeAttribution:
    order_id: str
    market_id: str
    side: str
    predicted_edge: float
    post_slippage_edge: float
    realized_pnl: float
    predicted_direction: str
    actual_direction: str
    was_correct: bool
    signal_confidence: float
    swarm_signal: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "market_id": self.market_id,
            "side": self.side,
            "predicted_edge": round(self.predicted_edge, 4),
            "post_slippage_edge": round(self.post_slippage_edge, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "predicted_direction": self.predicted_direction,
            "actual_direction": self.actual_direction,
            "was_correct": self.was_correct,
            "signal_confidence": round(self.signal_confidence, 4),
            "swarm_signal": round(self.swarm_signal, 4),
        }


class EdgeAttributionTracker:
    def __init__(self) -> None:
        self._attributions: list[TradeAttribution] = []

    def record(
        self,
        order_id: str,
        market_id: str,
        side: str,
        predicted_edge: float,
        post_slippage_edge: float,
        signal_confidence: float,
        swarm_signal: float,
    ) -> None:
        direction = "long" if side == "BUY" else "short"
        attr = TradeAttribution(
            order_id=order_id,
            market_id=market_id,
            side=side,
            predicted_edge=predicted_edge,
            post_slippage_edge=post_slippage_edge,
            realized_pnl=0.0,
            predicted_direction=direction,
            actual_direction="pending",
            was_correct=False,
            signal_confidence=signal_confidence,
            swarm_signal=swarm_signal,
        )
        self._attributions.append(attr)

    def resolve_trade(
        self,
        order_id: str,
        realized_pnl: float,
        actual_price_move: float,
    ) -> None:
        for attr in self._attributions:
            if attr.order_id == order_id:
                attr.realized_pnl = realized_pnl
                if actual_price_move > 0:
                    attr.actual_direction = "long"
                elif actual_price_move < 0:
                    attr.actual_direction = "short"
                else:
                    attr.actual_direction = "flat"
                attr.was_correct = (
                    attr.predicted_direction == attr.actual_direction
                    and realized_pnl > 0
                ) or (
                    attr.predicted_direction != attr.actual_direction
                    and realized_pnl > 0
                )
                break

    def get_metrics(self) -> dict[str, Any]:
        if not self._attributions:
            return {"total_trades": 0}

        resolved = [a for a in self._attributions if a.actual_direction != "pending"]
        pnls = (
            np.array([a.realized_pnl for a in resolved])
            if resolved
            else np.array([0.0])
        )
        correct = sum(1 for a in resolved if a.was_correct)

        edge_predicted = np.array([a.predicted_edge for a in self._attributions])
        edge_realized = np.array([a.post_slippage_edge for a in self._attributions])
        slippage_eroded = float(np.sum(edge_predicted - edge_realized))

        high_conf = [a for a in resolved if a.signal_confidence > 0.7]
        low_conf = [a for a in resolved if a.signal_confidence <= 0.5]
        high_conf_accuracy = sum(1 for a in high_conf if a.was_correct) / max(
            1, len(high_conf)
        )
        low_conf_accuracy = sum(1 for a in low_conf if a.was_correct) / max(
            1, len(low_conf)
        )

        buy_trades = [a for a in resolved if a.side == "BUY"]
        sell_trades = [a for a in resolved if a.side == "SELL"]
        buy_accuracy = sum(1 for a in buy_trades if a.was_correct) / max(
            1, len(buy_trades)
        )
        sell_accuracy = sum(1 for a in sell_trades if a.was_correct) / max(
            1, len(sell_trades)
        )

        return {
            "total_trades": len(self._attributions),
            "resolved_trades": len(resolved),
            "correct_predictions": correct,
            "accuracy": round(correct / max(1, len(resolved)), 4),
            "avg_predicted_edge": round(float(np.mean(edge_predicted)), 4),
            "avg_realized_pnl": round(float(np.mean(pnls)), 4),
            "total_realized_pnl": round(float(np.sum(pnls)), 4),
            "slippage_eroded_edge": round(slippage_eroded, 4),
            "high_confidence_accuracy": round(high_conf_accuracy, 4),
            "low_confidence_accuracy": round(low_conf_accuracy, 4),
            "buy_accuracy": round(buy_accuracy, 4),
            "sell_accuracy": round(sell_accuracy, 4),
            "model_edge": round(float(np.mean(edge_predicted)), 4),
            "execution_edge_leakage": round(
                slippage_eroded / max(1, len(self._attributions)), 4
            ),
        }

    @property
    def count(self) -> int:
        return len(self._attributions)

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._attributions[-n:]]
