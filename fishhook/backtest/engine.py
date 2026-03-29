"""Backtest engine - validates swarm signals against market price movements."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import numpy as np

from fishhook.backtest.fetcher import HistoricalDataFetcher
from fishhook.backtest.metrics import BacktestMetrics
from fishhook.config.settings import StrategyConfig, SwarmConfig
from fishhook.swarm.world import SimulationWorld
from fishhook.utils.logging import get_logger

logger = get_logger("backtest.engine")


@dataclass
class BacktestTrade:
    market_id: str
    question: str
    direction: str
    swarm_signal: float
    market_price: float
    edge: float
    confidence: float
    outcome_direction: str
    pnl: float
    correct: bool
    size: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "question": self.question[:80],
            "direction": self.direction,
            "swarm_signal": round(self.swarm_signal, 4),
            "market_price": round(self.market_price, 4),
            "edge": round(self.edge, 4),
            "confidence": round(self.confidence, 4),
            "outcome": self.outcome_direction,
            "pnl": round(self.pnl, 4),
            "correct": self.correct,
        }


@dataclass
class BacktestResult:
    trades: list[BacktestTrade]
    metrics: BacktestMetrics
    markets_tested: int
    signals_generated: int
    config_used: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "markets_tested": self.markets_tested,
            "signals_generated": self.signals_generated,
            "metrics": self.metrics.to_dict(),
            "trades": [t.to_dict() for t in self.trades[:50]],
            "config": self.config_used,
        }


class BacktestEngine:
    def __init__(
        self,
        swarm_config: SwarmConfig | None = None,
        strategy_config: StrategyConfig | None = None,
    ) -> None:
        self._swarm_config = swarm_config or SwarmConfig()
        self._strategy_config = strategy_config or StrategyConfig()
        self._fetcher = HistoricalDataFetcher()

    async def run(
        self,
        num_markets: int = 50,
        min_volume: float = 1000.0,
        category: str | None = None,
        agents: int = 500,
        rounds: int = 30,
    ) -> BacktestResult:
        logger.info(
            f"Starting backtest: {num_markets} markets, {agents} agents, {rounds} rounds"
        )

        markets = await self._fetcher.fetch_recent_markets(
            limit=num_markets,
            category=category,
            min_volume=min_volume,
            include_closed=False,
        )

        if not markets:
            logger.warning("No markets found for backtesting")
            return BacktestResult(
                trades=[],
                metrics=BacktestMetrics.compute([]),
                markets_tested=0,
                signals_generated=0,
                config_used=self._get_config_dict(),
            )

        logger.info(f"Backtesting against {len(markets)} markets")

        trades = []
        signals_generated = 0

        for i, market in enumerate(markets):
            if i % 10 == 0:
                logger.info(f"Backtesting market {i + 1}/{len(markets)}...")
                await asyncio.sleep(0)

            prices = market.get("prices", [])
            if len(prices) < 2:
                continue

            current_price = prices[0]
            one_day_change = market.get("one_day_change", 0)

            # Skip markets with no price movement data
            if one_day_change == 0:
                continue

            swarm = SimulationWorld(self._swarm_config)
            swarm._config.num_agents = agents
            swarm._config.max_rounds = rounds
            swarm.initialize()

            # Feed current price as signal
            market_signal = (0.5 - current_price) * 2
            signals = [market_signal] * rounds
            await swarm.run_simulation(signals=signals, max_rounds=rounds)
            swarm_signal = swarm.get_swarm_signal()

            signals_generated += 1

            swarm_opinion = swarm_signal["signal"]
            swarm_confidence = swarm_signal["confidence"]

            combined = (
                swarm_opinion * self._strategy_config.simulation_weight
                + market_signal * self._strategy_config.data_weight
            )
            total_weight = (
                self._strategy_config.simulation_weight
                + self._strategy_config.data_weight
            )
            if total_weight > 0:
                combined /= total_weight

            if combined > 0:
                direction = "BUY"
                fair_price = 0.5 + combined * 0.5
                edge = fair_price - current_price
            else:
                direction = "SELL"
                fair_price = 0.5 + combined * 0.5
                edge = current_price - fair_price

            confidence = swarm_confidence * 0.7 + min(1.0, abs(edge) * 5) * 0.3

            if edge < self._strategy_config.divergence_threshold:
                continue
            if confidence < self._strategy_config.min_confidence:
                continue

            # Outcome: did price go up or down?
            outcome_direction = "UP" if one_day_change > 0 else "DOWN"

            # P&L based on whether swarm predicted direction correctly
            if direction == "BUY" and outcome_direction == "UP":
                pnl = abs(one_day_change)
                correct = True
            elif direction == "SELL" and outcome_direction == "DOWN":
                pnl = abs(one_day_change)
                correct = True
            else:
                pnl = -abs(one_day_change)
                correct = False

            size = 10.0 * min(3.0, 1.0 + edge * 10) * confidence
            pnl *= size

            trade = BacktestTrade(
                market_id=market["id"],
                question=market["question"],
                direction=direction,
                swarm_signal=swarm_opinion,
                market_price=current_price,
                edge=edge,
                confidence=confidence,
                outcome_direction=outcome_direction,
                pnl=pnl,
                correct=correct,
                size=size,
            )
            trades.append(trade)

        metrics = BacktestMetrics.compute(trades)

        logger.info(
            f"Backtest complete: {len(trades)} trades from {len(markets)} markets, "
            f"win_rate={metrics.win_rate:.2%}, total_pnl={metrics.total_pnl:.2f}, "
            f"sharpe={metrics.sharpe_ratio:.2f}"
        )

        return BacktestResult(
            trades=trades,
            metrics=metrics,
            markets_tested=len(markets),
            signals_generated=signals_generated,
            config_used=self._get_config_dict(),
        )

    async def run_sweep(
        self,
        num_markets: int = 50,
        min_volume: float = 1000.0,
        category: str | None = None,
        agents_list: list[int] | None = None,
        thresholds: list[float] | None = None,
    ) -> dict[str, BacktestResult]:
        agents_list = agents_list or [200, 500, 1000]
        thresholds = thresholds or [0.05, 0.1, 0.15, 0.2]

        markets = await self._fetcher.fetch_recent_markets(
            limit=num_markets,
            category=category,
            min_volume=min_volume,
            include_closed=False,
        )

        results = {}

        for agents in agents_list:
            for threshold in thresholds:
                key = f"agents={agents}_threshold={threshold}"
                logger.info(f"Running sweep: {key}")

                swarm_config = SwarmConfig(num_agents=agents, max_rounds=30)
                strategy_config = StrategyConfig(
                    divergence_threshold=threshold,
                    min_confidence=self._strategy_config.min_confidence,
                    simulation_weight=self._strategy_config.simulation_weight,
                    data_weight=self._strategy_config.data_weight,
                )

                trades = []
                for market in markets:
                    prices = market.get("prices", [])
                    if len(prices) < 2:
                        continue
                    current_price = prices[0]
                    one_day_change = market.get("one_day_change", 0)
                    if one_day_change == 0:
                        continue

                    swarm = SimulationWorld(swarm_config)
                    swarm._config.num_agents = agents
                    swarm._config.max_rounds = 30
                    swarm.initialize()

                    market_signal = (0.5 - current_price) * 2
                    signals = [market_signal] * 30
                    await swarm.run_simulation(signals=signals, max_rounds=30)
                    ss = swarm.get_swarm_signal()

                    swarm_opinion = ss["signal"]
                    swarm_confidence = ss["confidence"]
                    combined = (
                        swarm_opinion * strategy_config.simulation_weight
                        + market_signal * strategy_config.data_weight
                    )
                    tw = strategy_config.simulation_weight + strategy_config.data_weight
                    if tw > 0:
                        combined /= tw

                    if combined > 0:
                        direction = "BUY"
                        fair = 0.5 + combined * 0.5
                        edge = fair - current_price
                    else:
                        direction = "SELL"
                        fair = 0.5 + combined * 0.5
                        edge = current_price - fair

                    if edge < threshold:
                        continue
                    conf = swarm_confidence * 0.7 + min(1.0, abs(edge) * 5) * 0.3
                    if conf < strategy_config.min_confidence:
                        continue

                    outcome_direction = "UP" if one_day_change > 0 else "DOWN"
                    if direction == "BUY" and outcome_direction == "UP":
                        pnl = abs(one_day_change)
                        correct = True
                    elif direction == "SELL" and outcome_direction == "DOWN":
                        pnl = abs(one_day_change)
                        correct = True
                    else:
                        pnl = -abs(one_day_change)
                        correct = False

                    size = 10.0 * min(3.0, 1.0 + edge * 10) * conf
                    pnl *= size

                    trades.append(
                        BacktestTrade(
                            market_id=market["id"],
                            question=market["question"],
                            direction=direction,
                            swarm_signal=swarm_opinion,
                            market_price=current_price,
                            edge=edge,
                            confidence=conf,
                            outcome_direction=outcome_direction,
                            pnl=pnl,
                            correct=correct,
                            size=size,
                        )
                    )

                results[key] = BacktestResult(
                    trades=trades,
                    metrics=BacktestMetrics.compute(trades),
                    markets_tested=len(markets),
                    signals_generated=len(markets),
                    config_used={"agents": agents, "threshold": threshold},
                )

        return results

    def _get_config_dict(self) -> dict[str, Any]:
        return {
            "swarm": self._swarm_config.model_dump(),
            "strategy": self._strategy_config.model_dump(),
        }
