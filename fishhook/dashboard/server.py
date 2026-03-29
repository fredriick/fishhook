"""HTTP server for the web dashboard."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fishhook.orchestrator import PipelineOrchestrator
from fishhook.swarm.world import SimulationWorld
from fishhook.utils.logging import get_logger

logger = get_logger("dashboard.server")

STATIC_DIR = Path(__file__).parent / "static"


class DashboardServer:
    def __init__(
        self,
        orchestrator: PipelineOrchestrator,
        host: str = "127.0.0.1",
        port: int = 8787,
    ) -> None:
        self._orchestrator = orchestrator
        self._host = host
        self._port = port
        self._server = None
        self._simulation_history: list[dict[str, Any]] = []

    async def start(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/simulation", self._handle_simulation)
        app.router.add_get("/api/simulation/run", self._handle_run_simulation)
        app.router.add_get("/api/trades", self._handle_trades)
        app.router.add_get("/api/network", self._handle_network)
        app.router.add_get("/api/history", self._handle_history)
        app.router.add_get("/api/backtest", self._handle_backtest)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        self._server = runner
        logger.info(f"Dashboard server running at http://{self._host}:{self._port}")

    async def stop(self) -> None:
        if self._server:
            await self._server.cleanup()

    async def _handle_index(self, request: Any) -> Any:
        from aiohttp import web

        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return web.Response(text=index_path.read_text(), content_type="text/html")
        return web.Response(text="Dashboard not found", status=404)

    async def _handle_status(self, request: Any) -> Any:
        from aiohttp import web

        data = self._orchestrator.get_status()
        return web.json_response(data)

    async def _handle_simulation(self, request: Any) -> Any:
        from aiohttp import web

        strategy = self._orchestrator._strategy
        state = strategy.get_state_summary()

        swarm_signal = self._orchestrator._swarm.get_swarm_signal()
        consensus = state.get("last_consensus")

        result = {
            "swarm_signal": swarm_signal,
            "consensus": consensus,
            "initialized": state["initialized"],
            "signals_generated": state["signals_generated"],
        }

        if consensus:
            result["distribution"] = consensus.get("distribution", {})

        return web.json_response(result)

    async def _handle_run_simulation(self, request: Any) -> Any:
        from aiohttp import web

        agents = int(request.query.get("agents", 1000))
        rounds = int(request.query.get("rounds", 50))
        signal = float(request.query.get("signal", 0.0))

        result = await self._orchestrator.run_simulation_only(
            signal=signal,
            agents=agents,
            rounds=rounds,
        )

        self._simulation_history.append(result)
        if len(self._simulation_history) > 100:
            self._simulation_history = self._simulation_history[-100:]

        return web.json_response(result)

    async def _handle_trades(self, request: Any) -> Any:
        from aiohttp import web

        trades = [t.to_dict() for t in self._orchestrator._executor.trade_history]
        portfolio = self._orchestrator._executor.get_portfolio_summary()
        return web.json_response({"trades": trades, "portfolio": portfolio})

    async def _handle_network(self, request: Any) -> Any:
        from aiohttp import web

        swarm = self._orchestrator._swarm
        social_stats = swarm._social_network.get_stats()

        influencers = []
        try:
            top = swarm._social_network.get_influencers(top_k=10)
            influencers = [
                {"agent_id": nid, "centrality": round(score, 4)} for nid, score in top
            ]
        except Exception:
            pass

        communities = swarm._social_network.detect_communities()
        community_sizes = {}
        for cid in communities.values():
            community_sizes[cid] = community_sizes.get(cid, 0) + 1

        return web.json_response(
            {
                "stats": social_stats,
                "influencers": influencers,
                "community_sizes": community_sizes,
            }
        )

    async def _handle_history(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"history": self._simulation_history})

    async def _handle_backtest(self, request: Any) -> Any:
        from aiohttp import web

        from fishhook.backtest.engine import BacktestEngine

        markets = int(request.query.get("markets", 20))
        agents = int(request.query.get("agents", 300))
        rounds = int(request.query.get("rounds", 20))
        min_volume = float(request.query.get("min_volume", 1000))
        category = request.query.get("category")

        engine = BacktestEngine(
            swarm_config=self._orchestrator._swarm._config,
            strategy_config=self._orchestrator._strategy._config,
        )

        result = await engine.run(
            num_markets=markets,
            min_volume=min_volume,
            category=category,
            agents=agents,
            rounds=rounds,
        )

        return web.json_response(result.to_dict())
