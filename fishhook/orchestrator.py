"""Pipeline orchestrator - coordinates all layers."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fishhook.config.settings import PipelineConfig
from fishhook.ingestion.credibility import CredibilityScorer
from fishhook.ingestion.deduplicator import SignalDeduplicator
from fishhook.ingestion.engine import ScrapingEngine
from fishhook.ingestion.sources import OrderBookSignalSource, SignalSourceManager
from fishhook.market.circuit_breaker import CircuitBreaker
from fishhook.market.client import PolymarketClient
from fishhook.market.executor import TradeExecutor
from fishhook.market.models import TradeSignal
from fishhook.strategy.engine import StrategyEngine
from fishhook.swarm.world import SimulationWorld
from fishhook.utils.logging import get_logger, setup_logging

logger = get_logger("orchestrator")


@dataclass
class PipelineRun:
    run_id: int
    started_at: float
    markets_analyzed: int = 0
    signals_generated: int = 0
    trades_executed: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "markets_analyzed": self.markets_analyzed,
            "signals_generated": self.signals_generated,
            "trades_executed": self.trades_executed,
            "errors": len(self.errors),
            "elapsed": round(self.elapsed_seconds, 2),
        }


class PipelineOrchestrator:
    def __init__(self, config: PipelineConfig | None = None) -> None:
        self._config = config or PipelineConfig()
        self._logger = setup_logging(
            self._config.log_level,
            self._config.data_dir / "logs",
        )

        self._scraper = ScrapingEngine(self._config.scraper)
        self._market_client = PolymarketClient(self._config.polymarket)

        self._circuit_breaker = None
        if self._config.circuit_breaker.enabled:
            self._circuit_breaker = CircuitBreaker(
                max_drawdown_pct=self._config.circuit_breaker.max_drawdown_pct,
                drawdown_window_hours=self._config.circuit_breaker.drawdown_window_hours,
                max_consecutive_losses=self._config.circuit_breaker.max_consecutive_losses,
                max_api_errors_per_hour=self._config.circuit_breaker.max_api_errors_per_hour,
                cooldown_seconds=self._config.circuit_breaker.cooldown_seconds,
            )

        self._deduplicator = None
        if self._config.deduplicator.enabled:
            self._deduplicator = SignalDeduplicator(
                similarity_threshold=self._config.deduplicator.similarity_threshold,
                window_seconds=self._config.deduplicator.window_seconds,
            )

        self._credibility = None
        if self._config.credibility.enabled:
            self._credibility = CredibilityScorer(
                learning_rate=self._config.credibility.learning_rate,
            )

        self._orderbook_source = None
        if self._config.data_sources.orderbook_as_signal:
            self._orderbook_source = OrderBookSignalSource(self._market_client)

        self._source_manager = SignalSourceManager()
        if self._orderbook_source:
            self._source_manager.register(self._orderbook_source)

        self._executor = TradeExecutor(
            self._market_client,
            self._config.polymarket,
            circuit_breaker=self._circuit_breaker,
            paper_trading=self._config.polymarket.testnet,
        )
        self._swarm = SimulationWorld(self._config.swarm)
        self._strategy = StrategyEngine(
            self._config.strategy,
            self._swarm,
            deduplicator=self._deduplicator,
            credibility=self._credibility,
            orderbook_source=self._orderbook_source,
        )

        self._run_count = 0
        self._runs: list[PipelineRun] = []
        self._running = False
        self._scraped_data_cache: dict[str, dict[str, Any]] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def runs(self) -> list[PipelineRun]:
        return list(self._runs)

    async def start(self) -> None:
        logger.info("Starting pipeline orchestrator")
        self._running = True
        await self._scraper.start()
        await self._strategy.initialize(self._config.swarm.num_agents)
        logger.info("Pipeline orchestrator started")

    async def stop(self) -> None:
        logger.info("Stopping pipeline orchestrator")
        self._running = False
        await self._scraper.stop()
        await self._market_client.close()
        await self._source_manager.close()
        logger.info("Pipeline orchestrator stopped")

    async def run_once(
        self,
        categories: list[str] | None = None,
        max_markets: int = 10,
    ) -> PipelineRun:
        self._run_count += 1
        run = PipelineRun(run_id=self._run_count, started_at=time.time())

        try:
            markets = await self._market_client.get_markets(
                limit=max_markets,
                active=True,
                category=categories[0] if categories else None,
            )
            run.markets_analyzed = len(markets)

            for market in markets:
                scraped = self._scraped_data_cache.get(market.id)

                signal = await self._strategy.analyze_market(market, scraped)
                if signal:
                    run.signals_generated += 1

                    trade = await self._executor.execute_signal(signal)
                    if trade:
                        run.trades_executed += 1

        except Exception as e:
            error_msg = f"Run {run.run_id} error: {e}"
            logger.error(error_msg)
            run.errors.append(error_msg)
            if self._circuit_breaker:
                self._circuit_breaker.record_api_error()

        run.elapsed_seconds = time.time() - run.started_at
        self._runs.append(run)

        logger.info(
            f"Run {run.run_id} complete: {run.markets_analyzed} markets, "
            f"{run.signals_generated} signals, {run.trades_executed} trades "
            f"({run.elapsed_seconds:.1f}s)"
        )

        return run

    async def run_loop(
        self,
        interval_seconds: int = 60,
        categories: list[str] | None = None,
        max_markets: int = 10,
    ) -> None:
        await self.start()
        logger.info(f"Starting run loop (interval={interval_seconds}s)")

        try:
            while self._running:
                run = await self.run_once(categories, max_markets)
                if run.errors:
                    logger.warning(f"Run had {len(run.errors)} errors")

                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Run loop cancelled")
        finally:
            await self.stop()

    async def scrape_and_cache(
        self,
        urls: list[str],
    ) -> dict[str, dict[str, Any]]:
        results = await self._scraper.scrape_multiple(urls)
        for result in results:
            data = {
                "html_length": len(result.html),
                "api_responses": result.api_responses,
                "dynamic_tokens": result.dynamic_tokens,
                "timing_ms": result.timing_ms,
            }

            if result.api_responses:
                for api_resp in result.api_responses:
                    api_data = api_resp.get("data", {})
                    if isinstance(api_data, dict):
                        for key in ("sentiment", "volume_trend", "social_signals"):
                            if key in api_data:
                                data[key] = api_data[key]

            self._scraped_data_cache[result.url] = data

        return self._scraped_data_cache

    async def run_simulation_only(
        self,
        signal: float = 0.0,
        agents: int = 1000,
        rounds: int = 50,
    ) -> dict[str, Any]:
        swarm = SimulationWorld()
        swarm._config.num_agents = agents
        swarm._config.max_rounds = rounds
        swarm.initialize()

        result = await swarm.run_simulation(
            signals=[signal] * rounds,
            max_rounds=rounds,
        )

        return {
            "converged": result.converged,
            "rounds": result.total_rounds,
            "consensus": result.final_consensus.to_dict(),
            "swarm_signal": swarm.get_swarm_signal(),
            "social_network": result.social_network_stats,
            "elapsed": round(result.elapsed_seconds, 2),
        }

    def get_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "running": self._running,
            "total_runs": len(self._runs),
            "total_trades": self._executor.total_trades,
            "portfolio": self._executor.get_portfolio_summary(),
            "strategy": self._strategy.get_state_summary(),
            "scraper_tokens": len(self._scraper.get_dynamic_tokens()),
            "cached_data": len(self._scraped_data_cache),
        }

        if self._circuit_breaker:
            status["circuit_breaker"] = self._circuit_breaker.get_status()

        if self._deduplicator:
            status["deduplicator"] = {"active_signals": self._deduplicator.count}

        if self._credibility:
            status["credibility"] = self._credibility.to_dict()

        return status

    async def save_state(self, path: Path | None = None) -> None:
        path = path or self._config.data_dir / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "runs": [r.to_dict() for r in self._runs],
            "portfolio": self._executor.get_portfolio_summary(),
            "trades": [t.to_dict() for t in self._executor.trade_history],
            "status": self.get_status(),
            "saved_at": time.time(),
        }

        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)

        logger.info(f"State saved to {path}")
