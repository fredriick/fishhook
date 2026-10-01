"""Backtest engine - validates swarm signals against market outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fishhook.backtest.fetcher import HistoricalDataFetcher, ResolvedMarket
from fishhook.backtest.metrics import BacktestMetrics
from fishhook.config.settings import StrategyConfig, SwarmConfig
from fishhook.swarm.world import SimulationWorld
from fishhook.utils.logging import get_logger

logger = get_logger("backtest.engine")

# Entry price used when no pre-resolution price snapshot is available. Binary
# markets open at 50/50, so the open price is the standard, transparent default
# rather than the circular final price of an already-resolved market.
DEFAULT_ENTRY_PRICE = 0.5


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
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "question": self.question[:80],
            "category": self.category,
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
    raw_accuracy: float = 0.0
    accuracy_by_category_raw: dict[str, float] = field(default_factory=dict)
    entry_assumption: str = f"open_price_{DEFAULT_ENTRY_PRICE}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "markets_tested": self.markets_tested,
            "signals_generated": self.signals_generated,
            "raw_accuracy": round(self.raw_accuracy, 4),
            "accuracy_by_category_raw": {
                k: round(v, 4)
                for k, v in sorted(self.accuracy_by_category_raw.items())
            },
            "entry_assumption": self.entry_assumption,
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
        use_resolved: bool = True,
    ) -> BacktestResult:
        mode = "resolved" if use_resolved else "live"
        logger.info(
            f"Starting backtest ({mode}): {num_markets} markets, "
            f"{agents} agents, {rounds} rounds"
        )

        records = await self._fetch_records(
            num_markets=num_markets,
            min_volume=min_volume,
            category=category,
            use_resolved=use_resolved,
        )

        if not records:
            logger.warning("No markets found for backtesting")
            result = BacktestResult(
                trades=[],
                metrics=BacktestMetrics.compute([]),
                markets_tested=0,
                signals_generated=0,
                config_used=self._get_config_dict(use_resolved),
            )
            return self._finalize(result, {}, 0, 0)

        logger.info(f"Backtesting against {len(records)} markets")

        trades = []
        signals_generated = 0
        agreement_correct = 0
        agreement_total = 0
        category_agreement: dict[str, list[int]] = {}

        for i, rec in enumerate(records):
            if i % 10 == 0:
                logger.info(f"Backtesting market {i + 1}/{len(records)}...")
                await asyncio.sleep(0)

            current_price = rec["current_price"]
            one_day_change = rec["one_day_change"]

            if one_day_change == 0:
                continue

            swarm = SimulationWorld(self._swarm_config)
            swarm._config.num_agents = agents
            swarm._config.max_rounds = rounds
            swarm.initialize()

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
            outcome_direction = rec["outcome_direction"]

            predicted_correctly = (
                direction == "BUY" and outcome_direction == "UP"
            ) or (direction == "SELL" and outcome_direction == "DOWN")

            agreement_total += 1
            if predicted_correctly:
                agreement_correct += 1
            cat = rec["category"] or "unknown"
            bucket = category_agreement.setdefault(cat, [0, 0])
            bucket[0] += 1
            if predicted_correctly:
                bucket[1] += 1

            if edge < self._strategy_config.divergence_threshold:
                continue
            if confidence < self._strategy_config.min_confidence:
                continue

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

            trades.append(
                BacktestTrade(
                    market_id=rec["id"],
                    question=rec["question"],
                    direction=direction,
                    swarm_signal=swarm_opinion,
                    market_price=current_price,
                    edge=edge,
                    confidence=confidence,
                    outcome_direction=outcome_direction,
                    pnl=pnl,
                    correct=correct,
                    size=size,
                    category=cat,
                )
            )

        metrics = BacktestMetrics.compute(trades, total_markets=len(records))

        logger.info(
            f"Backtest complete ({mode}): {len(trades)} trades from "
            f"{len(records)} markets, win_rate={metrics.win_rate:.2%}, "
            f"total_pnl={metrics.total_pnl:.2f}, sharpe={metrics.sharpe_ratio:.2f}, "
            f"raw_accuracy={agreement_correct / agreement_total if agreement_total else 0:.2%}"
        )

        result = BacktestResult(
            trades=trades,
            metrics=metrics,
            markets_tested=len(records),
            signals_generated=signals_generated,
            config_used=self._get_config_dict(use_resolved),
        )
        return self._finalize(
            result, category_agreement, agreement_correct, agreement_total
        )

    async def run_sweep(
        self,
        num_markets: int = 50,
        min_volume: float = 1000.0,
        category: str | None = None,
        agents_list: list[int] | None = None,
        thresholds: list[float] | None = None,
        use_resolved: bool = True,
    ) -> dict[str, BacktestResult]:
        agents_list = agents_list or [200, 500, 1000]
        thresholds = thresholds or [0.05, 0.1, 0.15, 0.2]

        records = await self._fetch_records(
            num_markets=num_markets,
            min_volume=min_volume,
            category=category,
            use_resolved=use_resolved,
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

                trades: list[BacktestTrade] = []
                signals_generated = 0
                agreement_correct = 0
                agreement_total = 0
                category_agreement: dict[str, list[int]] = {}

                for rec in records:
                    current_price = rec["current_price"]
                    one_day_change = rec["one_day_change"]
                    if one_day_change == 0:
                        continue

                    signals_generated += 1
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
                    tw = (
                        strategy_config.simulation_weight
                        + strategy_config.data_weight
                    )
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

                    outcome_direction = rec["outcome_direction"]
                    predicted_correctly = (
                        direction == "BUY" and outcome_direction == "UP"
                    ) or (direction == "SELL" and outcome_direction == "DOWN")

                    agreement_total += 1
                    if predicted_correctly:
                        agreement_correct += 1
                    cat = rec["category"] or "unknown"
                    bucket = category_agreement.setdefault(cat, [0, 0])
                    bucket[0] += 1
                    if predicted_correctly:
                        bucket[1] += 1

                    if edge < threshold:
                        continue
                    conf = swarm_confidence * 0.7 + min(1.0, abs(edge) * 5) * 0.3
                    if conf < strategy_config.min_confidence:
                        continue

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
                            market_id=rec["id"],
                            question=rec["question"],
                            direction=direction,
                            swarm_signal=swarm_opinion,
                            market_price=current_price,
                            edge=edge,
                            confidence=conf,
                            outcome_direction=outcome_direction,
                            pnl=pnl,
                            correct=correct,
                            size=size,
                            category=cat,
                        )
                    )

                result = BacktestResult(
                    trades=trades,
                    metrics=BacktestMetrics.compute(
                        trades, total_markets=len(records)
                    ),
                    markets_tested=len(records),
                    signals_generated=signals_generated,
                    config_used={
                        "agents": agents,
                        "threshold": threshold,
                        "use_resolved": use_resolved,
                    },
                )
                results[key] = self._finalize(
                    result, category_agreement, agreement_correct, agreement_total
                )

        return results

    async def _fetch_records(
        self,
        num_markets: int,
        min_volume: float,
        category: str | None,
        use_resolved: bool,
    ) -> list[dict[str, Any]]:
        if use_resolved:
            markets = await self._fetcher.fetch_resolved_markets(
                limit=num_markets,
                category=category,
                min_volume=min_volume,
            )
            records = []
            for market in markets:
                rec = await self._resolved_record(market)
                if rec:
                    records.append(rec)
            return records

        markets = await self._fetcher.fetch_recent_markets(
            limit=num_markets,
            category=category,
            min_volume=min_volume,
            include_closed=False,
        )
        records = []
        for market in markets:
            rec = self._live_record(market)
            if rec:
                records.append(rec)
        return records

    async def _resolved_record(
        self, market: ResolvedMarket
    ) -> dict[str, Any] | None:
        closing = market.closing_price
        if not market.outcome_prices or len(market.outcome_prices) < 2:
            return None

        entry_price = DEFAULT_ENTRY_PRICE
        try:
            hint = await self._fetcher.fetch_entry_price(market.condition_id)
            if hint is not None:
                entry_price = hint
        except Exception:
            pass

        magnitude = abs(closing - entry_price)
        up = market.was_yes_winner

        return {
            "id": market.id,
            "question": market.question,
            "current_price": entry_price,
            "one_day_change": magnitude if up else -magnitude,
            "outcome_direction": "UP" if up else "DOWN",
            "category": market.category,
        }

    @staticmethod
    def _live_record(market: dict[str, Any]) -> dict[str, Any] | None:
        prices = market.get("prices", [])
        if len(prices) < 2:
            return None
        current_price = prices[0]
        one_day_change = market.get("one_day_change", 0)
        if one_day_change == 0:
            return None
        return {
            "id": market["id"],
            "question": market["question"],
            "current_price": current_price,
            "one_day_change": one_day_change,
            "outcome_direction": "UP" if one_day_change > 0 else "DOWN",
            "category": market.get("category", ""),
        }

    @staticmethod
    def _finalize(
        result: BacktestResult,
        category_agreement: dict[str, list[int]],
        agreement_correct: int,
        agreement_total: int,
    ) -> BacktestResult:
        if agreement_total > 0:
            result.raw_accuracy = agreement_correct / agreement_total
        for cat, (total, correct) in category_agreement.items():
            if total > 0:
                result.accuracy_by_category_raw[cat] = correct / total
        return result

    def _get_config_dict(self, use_resolved: bool) -> dict[str, Any]:
        return {
            "mode": "resolved" if use_resolved else "live",
            "entry_assumption": (
                "open_price_0.5" if use_resolved else "current_market_price"
            ),
            "swarm": self._swarm_config.model_dump(),
            "strategy": self._strategy_config.model_dump(),
        }