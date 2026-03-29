"""Backtesting layer - replay historical markets against swarm signals."""

from fishhook.backtest.fetcher import HistoricalDataFetcher
from fishhook.backtest.engine import BacktestEngine, BacktestTrade, BacktestResult
from fishhook.backtest.metrics import BacktestMetrics

__all__ = [
    "HistoricalDataFetcher",
    "BacktestEngine",
    "BacktestTrade",
    "BacktestResult",
    "BacktestMetrics",
]
