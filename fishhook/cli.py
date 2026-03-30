"""CLI entry point for fishhook."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from fishhook.config.settings import PipelineConfig
from fishhook.orchestrator import PipelineOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fishhook",
        description="Data ingestion + Swarm simulation + Polymarket execution pipeline",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config.yaml",
        help="Path to configuration YAML file",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Run in testnet mode (no real trades)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    run_parser = subparsers.add_parser("run", help="Run the pipeline once")
    run_parser.add_argument(
        "--markets", "-m", type=int, default=10, help="Max markets to analyze"
    )
    run_parser.add_argument(
        "--category", type=str, default=None, help="Market category filter"
    )
    run_parser.add_argument(
        "--testnet", action="store_true", help="Run in testnet mode (no real trades)"
    )

    loop_parser = subparsers.add_parser("loop", help="Run the pipeline in a loop")
    loop_parser.add_argument(
        "--interval", "-i", type=int, default=60, help="Seconds between runs"
    )
    loop_parser.add_argument(
        "--markets", "-m", type=int, default=10, help="Max markets per run"
    )
    loop_parser.add_argument(
        "--category", type=str, default=None, help="Market category filter"
    )
    loop_parser.add_argument(
        "--testnet", action="store_true", help="Run in testnet mode (no real trades)"
    )

    sim_parser = subparsers.add_parser("simulate", help="Run swarm simulation only")
    sim_parser.add_argument(
        "--agents", "-a", type=int, default=1000, help="Number of agents"
    )
    sim_parser.add_argument(
        "--rounds", "-r", type=int, default=50, help="Max simulation rounds"
    )
    sim_parser.add_argument(
        "--signal", "-s", type=float, default=0.0, help="External signal (-1 to 1)"
    )

    scrape_parser = subparsers.add_parser("scrape", help="Scrape URLs and extract data")
    scrape_parser.add_argument("urls", nargs="+", help="URLs to scrape")

    status_parser = subparsers.add_parser("status", help="Show pipeline status")

    dash_parser = subparsers.add_parser("dashboard", help="Launch web dashboard")
    dash_parser.add_argument(
        "--port", "-p", type=int, default=8787, help="Dashboard port"
    )
    dash_parser.add_argument(
        "--host", type=str, default="127.0.0.1", help="Dashboard host"
    )

    tui_parser = subparsers.add_parser("tui", help="Launch terminal dashboard")
    tui_parser.add_argument(
        "--refresh", type=float, default=2.0, help="Refresh interval in seconds"
    )

    bt_parser = subparsers.add_parser(
        "backtest", help="Backtest swarm against resolved markets"
    )
    bt_parser.add_argument(
        "--markets",
        "-m",
        type=int,
        default=50,
        help="Number of resolved markets to test",
    )
    bt_parser.add_argument(
        "--agents", "-a", type=int, default=500, help="Agents per simulation"
    )
    bt_parser.add_argument(
        "--rounds", "-r", type=int, default=30, help="Rounds per simulation"
    )
    bt_parser.add_argument(
        "--min-volume", type=float, default=1000.0, help="Min market volume filter"
    )
    bt_parser.add_argument(
        "--category", type=str, default=None, help="Market category filter"
    )
    bt_parser.add_argument(
        "--sweep", action="store_true", help="Run parameter sweep (agents x thresholds)"
    )

    halt_parser = subparsers.add_parser(
        "halt", help="Manually halt trading via circuit breaker"
    )
    halt_parser.add_argument(
        "--reason", type=str, default="Manual halt", help="Reason for halting"
    )

    resume_parser = subparsers.add_parser(
        "resume", help="Resume trading after circuit breaker halt"
    )

    return parser


async def cmd_run(args: argparse.Namespace, config: PipelineConfig) -> None:
    if getattr(args, "testnet", False):
        config.polymarket.testnet = True

    orchestrator = PipelineOrchestrator(config)
    await orchestrator.start()

    categories = [args.category] if args.category else None
    run = await orchestrator.run_once(categories=categories, max_markets=args.markets)

    print(json.dumps(run.to_dict(), indent=2))

    await orchestrator.stop()


async def cmd_loop(args: argparse.Namespace, config: PipelineConfig) -> None:
    if getattr(args, "testnet", False):
        config.polymarket.testnet = True

    orchestrator = PipelineOrchestrator(config)
    categories = [args.category] if args.category else None
    await orchestrator.run_loop(
        interval_seconds=args.interval,
        categories=categories,
        max_markets=args.markets,
    )


async def cmd_simulate(args: argparse.Namespace, config: PipelineConfig) -> None:
    orchestrator = PipelineOrchestrator(config)
    result = await orchestrator.run_simulation_only(
        signal=args.signal,
        agents=args.agents,
        rounds=args.rounds,
    )
    print(json.dumps(result, indent=2))


async def cmd_scrape(args: argparse.Namespace, config: PipelineConfig) -> None:
    orchestrator = PipelineOrchestrator(config)
    await orchestrator._scraper.start()

    results = await orchestrator.scrape_and_cache(args.urls)
    for url, data in results.items():
        print(f"\n--- {url} ---")
        print(
            json.dumps(
                {k: v for k, v in data.items() if k != "api_responses"}, indent=2
            )
        )
        if data.get("api_responses"):
            print(f"  API responses captured: {len(data['api_responses'])}")

    await orchestrator._scraper.stop()


async def cmd_status(args: argparse.Namespace, config: PipelineConfig) -> None:
    orchestrator = PipelineOrchestrator(config)
    status = orchestrator.get_status()
    print(json.dumps(status, indent=2))


async def cmd_dashboard(args: argparse.Namespace, config: PipelineConfig) -> None:
    from fishhook.dashboard.server import DashboardServer

    orchestrator = PipelineOrchestrator(config)
    await orchestrator.start()

    server = DashboardServer(orchestrator, host=args.host, port=args.port)
    await server.start()

    print(f"Dashboard running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await server.stop()
        await orchestrator.stop()


async def cmd_tui(args: argparse.Namespace, config: PipelineConfig) -> None:
    from fishhook.dashboard.terminal import TerminalDashboard

    orchestrator = PipelineOrchestrator(config)
    await orchestrator.start()

    dashboard = TerminalDashboard(orchestrator)
    print("Starting terminal dashboard... Press Ctrl+C to quit")

    try:
        await dashboard.run(refresh_seconds=args.refresh)
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.stop()
        await orchestrator.stop()


async def cmd_backtest(args: argparse.Namespace, config: PipelineConfig) -> None:
    from fishhook.backtest.engine import BacktestEngine

    engine = BacktestEngine(
        swarm_config=config.swarm,
        strategy_config=config.strategy,
    )

    if args.sweep:
        print("Running parameter sweep...")
        results = await engine.run_sweep(
            num_markets=args.markets,
            min_volume=args.min_volume,
            category=args.category,
        )
        print("\n=== SWEEP RESULTS ===\n")
        for key, result in sorted(results.items()):
            m = result.metrics
            print(
                f"{key}: trades={m.total_trades} win_rate={m.win_rate:.2%} pnl={m.total_pnl:.2f} sharpe={m.sharpe_ratio:.2f}"
            )
    else:
        print(f"Backtesting {args.markets} markets with {args.agents} agents...")
        result = await engine.run(
            num_markets=args.markets,
            min_volume=args.min_volume,
            category=args.category,
            agents=args.agents,
            rounds=args.rounds,
        )
        print(json.dumps(result.to_dict(), indent=2))


async def cmd_halt(args: argparse.Namespace, config: PipelineConfig) -> None:
    orchestrator = PipelineOrchestrator(config)
    if orchestrator._circuit_breaker:
        orchestrator._circuit_breaker.force_open(args.reason)
        print(json.dumps(orchestrator._circuit_breaker.get_status(), indent=2))
    else:
        print("Circuit breaker is not enabled in config")


async def cmd_resume(args: argparse.Namespace, config: PipelineConfig) -> None:
    orchestrator = PipelineOrchestrator(config)
    if orchestrator._circuit_breaker:
        orchestrator._circuit_breaker.force_close("Manual resume")
        print(json.dumps(orchestrator._circuit_breaker.get_status(), indent=2))
    else:
        print("Circuit breaker is not enabled in config")


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = PipelineConfig.from_yaml(args.config)

    commands = {
        "run": cmd_run,
        "loop": cmd_loop,
        "simulate": cmd_simulate,
        "scrape": cmd_scrape,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
        "tui": cmd_tui,
        "backtest": cmd_backtest,
        "halt": cmd_halt,
        "resume": cmd_resume,
    }

    handler = commands.get(args.command)
    if handler:
        await handler(args, config)
    else:
        parser.print_help()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
