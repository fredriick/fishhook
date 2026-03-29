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
