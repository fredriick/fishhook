"""Backtest metrics computation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class BacktestMetrics:
    total_markets: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_pnl_per_trade: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float
    avg_edge: float
    avg_edge_on_wins: float
    avg_edge_on_losses: float
    accuracy_by_threshold: dict[str, float]
    pnl_by_threshold: dict[str, float]
    trades_by_direction: dict[str, int]
    accuracy_by_direction: dict[str, float]
    cumulative_pnl: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_markets": self.total_markets,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 4),
            "avg_pnl_per_trade": round(self.avg_pnl_per_trade, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "avg_edge": round(self.avg_edge, 4),
            "avg_edge_wins": round(self.avg_edge_on_wins, 4),
            "avg_edge_losses": round(self.avg_edge_on_losses, 4),
            "accuracy_by_threshold": {
                k: round(v, 4) for k, v in self.accuracy_by_threshold.items()
            },
            "pnl_by_threshold": {
                k: round(v, 4) for k, v in self.pnl_by_threshold.items()
            },
            "trades_by_direction": self.trades_by_direction,
            "accuracy_by_direction": {
                k: round(v, 4) for k, v in self.accuracy_by_direction.items()
            },
            "equity_curve": self.cumulative_pnl[:200],
        }

    @staticmethod
    def compute(trades: list[Any]) -> BacktestMetrics:
        if not trades:
            return BacktestMetrics(
                total_markets=0,
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0,
                total_pnl=0,
                avg_pnl_per_trade=0,
                max_drawdown=0,
                sharpe_ratio=0,
                profit_factor=0,
                avg_edge=0,
                avg_edge_on_wins=0,
                avg_edge_on_losses=0,
                accuracy_by_threshold={},
                pnl_by_threshold={},
                trades_by_direction={},
                accuracy_by_direction={},
                cumulative_pnl=[],
            )

        total = len(trades)
        pnls = np.array([t.pnl for t in trades])
        wins_arr = pnls[pnls > 0]
        losses_arr = pnls[pnls <= 0]

        wins = len(wins_arr)
        losses = len(losses_arr)
        win_rate = wins / total if total > 0 else 0
        total_pnl = float(np.sum(pnls))
        avg_pnl = float(np.mean(pnls))

        # Cumulative P&L and max drawdown
        cum_pnl = np.cumsum(pnls).tolist()
        running_max = np.maximum.accumulate(pnls.cumsum())
        drawdowns = pnls.cumsum() - running_max
        max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0

        # Sharpe ratio (annualized, assuming 1 trade per day)
        if len(pnls) > 1 and np.std(pnls) > 0:
            sharpe = float(np.mean(pnls) / np.std(pnls) * math.sqrt(252))
        else:
            sharpe = 0.0

        # Profit factor
        gross_profit = float(np.sum(wins_arr)) if len(wins_arr) > 0 else 0
        gross_loss = abs(float(np.sum(losses_arr))) if len(losses_arr) > 0 else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Edge analysis
        edges = np.array([t.edge for t in trades])
        avg_edge = float(np.mean(edges))
        win_edges = [t.edge for t in trades if t.pnl > 0]
        loss_edges = [t.edge for t in trades if t.pnl <= 0]
        avg_edge_wins = float(np.mean(win_edges)) if win_edges else 0
        avg_edge_losses = float(np.mean(loss_edges)) if loss_edges else 0

        # Accuracy by edge threshold
        thresholds = [0.05, 0.1, 0.15, 0.2, 0.25]
        accuracy_by_threshold = {}
        pnl_by_threshold = {}
        for t in thresholds:
            key = f"edge>={t}"
            filtered = [tr for tr in trades if tr.edge >= t]
            if filtered:
                acc = sum(1 for tr in filtered if tr.pnl > 0) / len(filtered)
                pnl_sum = sum(tr.pnl for tr in filtered)
                accuracy_by_threshold[key] = acc
                pnl_by_threshold[key] = pnl_sum

        # By direction
        buy_trades = [t for t in trades if t.direction == "BUY"]
        sell_trades = [t for t in trades if t.direction == "SELL"]
        trades_by_direction = {"BUY": len(buy_trades), "SELL": len(sell_trades)}
        accuracy_by_direction = {}
        if buy_trades:
            accuracy_by_direction["BUY"] = sum(
                1 for t in buy_trades if t.pnl > 0
            ) / len(buy_trades)
        if sell_trades:
            accuracy_by_direction["SELL"] = sum(
                1 for t in sell_trades if t.pnl > 0
            ) / len(sell_trades)

        return BacktestMetrics(
            total_markets=len(set(t.market_id for t in trades)),
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl_per_trade=avg_pnl,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            avg_edge=avg_edge,
            avg_edge_on_wins=avg_edge_wins,
            avg_edge_on_losses=avg_edge_losses,
            accuracy_by_threshold=accuracy_by_threshold,
            pnl_by_threshold=pnl_by_threshold,
            trades_by_direction=trades_by_direction,
            accuracy_by_direction=accuracy_by_direction,
            cumulative_pnl=cum_pnl,
        )
