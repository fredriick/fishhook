"""Strategy engine - connects data ingestion to swarm simulation to trade signals."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fishhook.config.settings import StrategyConfig
from fishhook.ingestion.credibility import CredibilityScorer
from fishhook.ingestion.deduplicator import SignalDeduplicator
from fishhook.ingestion.sources import OrderBookSignalSource, SourceSignal
from fishhook.market.models import Market, OrderSide, TradeSignal
from fishhook.swarm.world import SimulationResult, SimulationWorld
from fishhook.utils.logging import get_logger

logger = get_logger("strategy")


@dataclass
class StrategyState:
    last_signal_time: float = 0.0
    signals_generated: int = 0
    signals_executed: int = 0
    last_simulation: SimulationResult | None = None
    market_signals: dict[str, float] = field(default_factory=dict)


class StrategyEngine:
    def __init__(
        self,
        config: StrategyConfig | None = None,
        swarm: SimulationWorld | None = None,
        deduplicator: SignalDeduplicator | None = None,
        credibility: CredibilityScorer | None = None,
        orderbook_source: OrderBookSignalSource | None = None,
    ) -> None:
        self._config = config or StrategyConfig()
        self._swarm = swarm or SimulationWorld()
        self._state = StrategyState()
        self._initialized = False
        self._deduplicator = deduplicator
        self._credibility = credibility
        self._orderbook_source = orderbook_source

    async def initialize(self, num_agents: int = 1000) -> None:
        if not self._initialized:
            self._swarm.initialize(num_agents)
            self._initialized = True
            logger.info(f"Strategy engine initialized with {num_agents} agents")

    async def analyze_market(
        self,
        market: Market,
        scraped_data: dict[str, Any] | None = None,
    ) -> TradeSignal | None:
        if not self._initialized:
            await self.initialize()

        now = time.time()
        if now - self._state.last_signal_time < self._config.cooldown_seconds:
            return None

        market_signal = await self._compute_market_signal(market, scraped_data)

        sim_result = await self._run_simulation(market_signal, scraped_data)
        swarm_signal = self._swarm.get_swarm_signal()

        signal = self._generate_trade_signal(market, swarm_signal, market_signal)

        self._state.last_signal_time = now
        self._state.signals_generated += 1

        if signal:
            logger.info(
                f"Signal for '{market.question[:50]}...': "
                f"{signal.side.value} @ ${signal.price:.3f} "
                f"(edge={signal.edge:.4f}, conf={signal.confidence:.4f})"
            )

        return signal

    async def analyze_markets(
        self,
        markets: list[Market],
        scraped_data_map: dict[str, dict[str, Any]] | None = None,
    ) -> list[TradeSignal]:
        signals = []
        for market in markets:
            data = None
            if scraped_data_map:
                data = scraped_data_map.get(market.id)
            signal = await self.analyze_market(market, data)
            if signal and signal.is_actionable:
                signals.append(signal)

        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals

    async def _compute_market_signal(
        self,
        market: Market,
        scraped_data: dict[str, Any] | None,
    ) -> float:
        signal = 0.0
        weight_sum = 0.0

        implied_prob = market.implied_probability
        implied_signal = (0.5 - implied_prob) * self._config.data_weight
        signal += implied_signal
        weight_sum += self._config.data_weight

        if scraped_data:
            if "sentiment" in scraped_data:
                sentiment = float(scraped_data["sentiment"])
                source = scraped_data.get("sentiment_source", "")
                if self._credibility:
                    sentiment = self._credibility.get_weighted_value(sentiment, source)
                    self._credibility.record_signal(
                        source, sentiment, market_id=market.id
                    )
                signal += sentiment * 0.3
                weight_sum += 0.3

            if "volume_trend" in scraped_data:
                trend = float(scraped_data["volume_trend"])
                signal += trend * 0.2
                weight_sum += 0.2

            if "social_signals" in scraped_data:
                social = float(scraped_data["social_signals"])
                signal += social * 0.2
                weight_sum += 0.2

            if self._deduplicator:
                self._deduplicator.add(
                    value=implied_signal,
                    source="implied_probability",
                    category="price",
                    metadata={"market_id": market.id},
                )

        if self._orderbook_source:
            ob_signals = await self._orderbook_source.fetch_signals(
                market_id=market.id, token_ids=[market.id]
            )
            for ob_sig in ob_signals:
                signal += ob_sig.value * ob_sig.confidence * 0.3
                weight_sum += 0.3

        if weight_sum > 0:
            signal /= weight_sum

        return max(-1.0, min(1.0, signal))

    async def _run_simulation(
        self,
        market_signal: float,
        scraped_data: dict[str, Any] | None,
    ) -> SimulationResult:
        signals = [market_signal] * 20
        if scraped_data and "signal_history" in scraped_data:
            history = scraped_data["signal_history"]
            if isinstance(history, list):
                signals = [float(s) for s in history] + signals

        result = await self._swarm.run_simulation(
            signals=signals,
            max_rounds=self._swarm._config.max_rounds,
        )

        self._state.last_simulation = result
        return result

    def _generate_trade_signal(
        self,
        market: Market,
        swarm_signal: dict[str, Any],
        market_signal: float,
    ) -> TradeSignal | None:
        swarm_opinion = swarm_signal["signal"]
        swarm_confidence = swarm_signal["confidence"]

        combined_signal = (
            swarm_opinion * self._config.simulation_weight
            + market_signal * self._config.data_weight
        )

        total_weight = self._config.simulation_weight + self._config.data_weight
        if total_weight > 0:
            combined_signal /= total_weight

        market_price = market.yes_price

        if combined_signal > 0:
            side = OrderSide.BUY
            fair_price = 0.5 + combined_signal * 0.5
            edge = fair_price - market_price
        else:
            side = OrderSide.SELL
            fair_price = 0.5 + combined_signal * 0.5
            edge = market_price - fair_price

        confidence = swarm_confidence * 0.7 + min(1.0, abs(edge) * 5) * 0.3

        if edge < self._config.divergence_threshold:
            return None

        if confidence < self._config.min_confidence:
            return None

        position_size = self._calculate_position_size(edge, confidence)

        return TradeSignal(
            market_id=market.id,
            side=side,
            price=market_price,
            size=position_size,
            confidence=confidence,
            edge=edge,
            reason=f"Swarm={swarm_opinion:.3f}({swarm_signal['direction']}) vs Market={market_price:.3f}",
            swarm_signal=swarm_opinion,
            market_price=market_price,
        )

    def _calculate_position_size(self, edge: float, confidence: float) -> float:
        base_size = 10.0

        win_prob = confidence
        lose_prob = 1.0 - win_prob
        odds = (1.0 + edge) / max(0.01, 1.0 - edge) if edge > 0 else 1.0

        if odds > 0 and lose_prob > 0:
            kelly = (win_prob * odds - lose_prob) / odds
        else:
            kelly = 0.0

        kelly = max(0.0, kelly)
        fractional_kelly = kelly * self._config.kelly_fraction
        kelly_size = base_size * max(0.1, min(3.0, fractional_kelly * 10))

        edge_multiplier = min(3.0, 1.0 + edge * 10)
        return round(kelly_size * edge_multiplier, 2)

    def get_state_summary(self) -> dict[str, Any]:
        return {
            "initialized": self._initialized,
            "signals_generated": self._state.signals_generated,
            "last_simulation_rounds": (
                self._state.last_simulation.total_rounds
                if self._state.last_simulation
                else 0
            ),
            "last_consensus": (
                self._state.last_simulation.final_consensus.to_dict()
                if self._state.last_simulation
                else None
            ),
        }
