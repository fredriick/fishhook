"""Terminal dashboard using Rich for live pipeline visualization.

Interactive parity with the web dashboard: type a command and press Enter to
run a pipeline, run a swarm simulation, scrape URLs, halt/resume the circuit
breaker, view the active config, or run a backtest.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fishhook.orchestrator import PipelineOrchestrator

_HELP = (
    "Commands:\n"
    "  r [markets]     run the full pipeline once (default 10 markets)\n"
    "  r live [...]    confirm a run in LIVE trading mode\n"
    "  s [sig] [a] [r] run a swarm simulation (signal, agents, rounds)\n"
    "  u <urls...>     scrape and cache one or more URLs\n"
    "  b [markets]     run a backtest (default 20 markets)\n"
    "  h [reason]      halt trading (force-open circuit breaker)\n"
    "  n               resume trading (reset circuit breaker)\n"
    "  c               show active config summary\n"
    "  ?               this help\n"
    "  q | Ctrl+C      quit"
)


class TerminalDashboard:
    MAX_MESSAGES = 12

    def __init__(self, orchestrator: PipelineOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._console = Console()
        self._running = False
        self._busy = False
        self._messages: list[str] = []
        self._queue: asyncio.Queue[tuple[str, str]] | None = None

    def build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="console", size=8),
            Layout(name="footer", size=3),
        )
        layout["body"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=1),
        )
        layout["left"].split_column(
            Layout(name="swarm", ratio=2),
            Layout(name="markets", ratio=1),
        )
        layout["right"].split_column(
            Layout(name="trades", ratio=1),
            Layout(name="network", ratio=1),
        )
        return layout

    def render_header(self) -> Panel:
        status = self._orchestrator.get_status()
        running = (
            "[green]RUNNING[/green]" if status["running"] else "[red]STOPPED[/red]"
        )
        trades = status["total_trades"]
        runs = status["total_runs"]
        text = Text.from_markup(
            f" [bold]FISHHOOK[/bold]  |  Status: {running}  |  "
            f"Runs: {runs}  |  Trades: {trades}  |  "
            f"Cached: {status['cached_data']}"
        )
        return Panel(text, style="bold blue")

    def render_swarm(self, strategy_state: dict[str, Any]) -> Panel:
        consensus = strategy_state.get("last_consensus")
        if not consensus:
            return Panel("[dim]No simulation run yet[/dim]", title="Swarm Consensus")

        dist = consensus.get("distribution", {})
        direction = consensus.get("direction", "neutral")
        dir_color = {
            "bullish": "green",
            "bearish": "red",
            "neutral": "yellow",
        }.get(direction, "white")

        bars = self._build_opinion_bar(dist)

        lines = [
            f"Direction: [{dir_color}]{direction.upper()}[/{dir_color}]  "
            f"Mean: {consensus['mean_opinion']:+.4f}  "
            f"Confidence: {consensus['confidence']:.2%}",
            f"Agreement: {consensus['agreement_ratio']:.2%}  "
            f"Polarization: {consensus['polarization']:.4f}  "
            f"Strength: {consensus['strength']:.4f}",
            f"Rounds: {consensus['round']}  "
            f"Groups: {consensus['groups']}  "
            f"Std Dev: {consensus['std_dev']:.4f}",
            "",
            f"  [dim]Opinion Distribution[/dim]",
            bars,
        ]
        return Panel("\n".join(lines), title="[bold]Swarm Consensus[/bold]")

    def _build_opinion_bar(self, dist: dict[str, int]) -> str:
        total = sum(dist.values()) or 1
        segments = []
        labels = [
            ("strong_bear", "red"),
            ("bear", "red"),
            ("neutral", "yellow"),
            ("bull", "green"),
            ("strong_bull", "green"),
        ]
        bar_width = 50
        for key, color in labels:
            count = dist.get(key, 0)
            width = max(1, int(count / total * bar_width)) if count > 0 else 0
            if width > 0:
                segments.append(f"[{color}]{'█' * width}[/{color}]")

        bar = "".join(segments)
        sb = dist.get("strong_bear", 0)
        b = dist.get("bear", 0)
        n = dist.get("neutral", 0)
        bu = dist.get("bull", 0)
        sbu = dist.get("strong_bull", 0)
        labels_text = (
            f"  [red]{sb}[/red]+[red]{b}[/red]  "
            f"[yellow]{n}[/yellow]  "
            f"[green]{bu}[/green]+[green]{sbu}[/green]"
        )
        return f"  {bar}\n{labels_text}"

    def render_network(self, status: dict[str, Any]) -> Panel:
        strategy = status.get("strategy", {})
        sim_rounds = strategy.get("last_simulation_rounds", 0)
        agent_count = self._orchestrator._config.swarm.num_agents
        lines = [
            f"Agents: {agent_count}",
            f"Sim Rounds: {sim_rounds}",
            f"Signals Generated: {strategy.get('signals_generated', 0)}",
            f"Initialized: {strategy.get('initialized', False)}",
        ]
        return Panel("\n".join(lines), title="[bold]Network[/bold]")

    def render_trades(self, status: dict[str, Any]) -> Panel:
        portfolio = status.get("portfolio", {})
        lines = [
            f"Positions: {portfolio.get('positions', 0)}",
            f"Total Value: ${portfolio.get('total_value', 0):.2f}",
            f"P&L: ${portfolio.get('total_pnl', 0):.2f}",
            f"Winning: {portfolio.get('winning_positions', 0)}  "
            f"Losing: {portfolio.get('losing_positions', 0)}",
            f"Remaining/hr: {portfolio.get('trades_remaining_hour', 10)}",
        ]
        return Panel("\n".join(lines), title="[bold]Portfolio[/bold]")

    def render_markets(self) -> Panel:
        runs = self._orchestrator.runs
        if not runs:
            return Panel("[dim]No runs yet[/dim]", title="Market Runs")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Run", width=4)
        table.add_column("Markets", width=7)
        table.add_column("Signals", width=7)
        table.add_column("Trades", width=6)
        table.add_column("Time", width=6)
        table.add_column("Errors", width=6)

        for run in runs[-5:]:
            err_style = "red" if run.errors else "green"
            table.add_row(
                str(run.run_id),
                str(run.markets_analyzed),
                str(run.signals_generated),
                str(run.trades_executed),
                f"{run.elapsed_seconds:.1f}s",
                f"[{err_style}]{len(run.errors)}[/{err_style}]",
            )

        return Panel(table, title="[bold]Recent Runs[/bold]")

    def render_console(self) -> Panel:
        text = "\n".join(self._messages) if self._messages else "[dim]No activity yet[/dim]"
        busy = "[yellow]busy[/yellow]" if self._busy else "[green]idle[/green]"
        return Panel(text, title=f"[bold]Commands[/bold] ({busy})")

    def render_footer(self) -> Panel:
        text = Text.from_markup(
            " [dim][r]un  [s]imulation  [u]rl  [b]acktest  [h]alt  r[e]sume  "
            "[c]onfig  [?]help  q quit[/dim]"
        )
        return Panel(text, style="dim")

    def render(self) -> Layout:
        layout = self.build_layout()
        status = self._orchestrator.get_status()
        strategy = status.get("strategy", {})

        layout["header"].update(self.render_header())
        layout["swarm"].update(self.render_swarm(strategy))
        layout["markets"].update(self.render_markets())
        layout["trades"].update(self.render_trades(status))
        layout["network"].update(self.render_network(status))
        layout["console"].update(self.render_console())
        layout["footer"].update(self.render_footer())
        return layout

    def _push(self, message: str, color: str = "white") -> None:
        self._messages.append(f"[{color}]{message}[/{color}]")
        if len(self._messages) > self.MAX_MESSAGES:
            self._messages = self._messages[-self.MAX_MESSAGES:]

    def _trading_mode(self) -> str:
        config = self._orchestrator._config
        if config.polymarket.paper_trading:
            return "paper"
        if config.polymarket.testnet:
            return "testnet"
        return "live"

    async def _read_console(self) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                self._queue.put_nowait(("quit", ""))
                return
            line = line.strip()
            if not line:
                continue
            cmd, _, arg = line.partition(" ")
            self._queue.put_nowait((cmd.lower(), arg.strip()))

    def _spawn(self, runnable: Any, busy_message: str) -> None:
        if self._busy:
            runnable.close()
            self._push("One command at a time; previous still running.", "yellow")
            return
        self._busy = True
        self._push(busy_message, "yellow")

        async def _wrapper() -> None:
            try:
                await runnable
            except Exception as e:
                self._push(f"Command failed: {e}", "red")
            finally:
                self._busy = False

        asyncio.create_task(_wrapper())

    def _handle_halt(self, reason: str) -> None:
        breaker = self._orchestrator._circuit_breaker
        if breaker is None:
            self._push("Circuit breaker is not enabled in config.", "red")
            return
        breaker.force_open(reason or "Manual halt (TUI)")
        state = breaker.get_status()
        self._push(f"Halted: {state['state']} - {state['reason']}", "green")

    def _handle_resume(self) -> None:
        breaker = self._orchestrator._circuit_breaker
        if breaker is None:
            self._push("Circuit breaker is not enabled in config.", "red")
            return
        breaker.force_close("Manual resume (TUI)")
        state = breaker.get_status()
        self._push(f"Resumed: {state['state']}", "green")

    async def _run_pipeline(self, arg: str) -> None:
        mode = self._trading_mode()
        markets = 10
        for token in arg.split():
            if token.isdigit():
                markets = int(token)
                break
        if mode == "live" and "live" not in arg.split():
            self._push(
                "Refusing to run: pipeline is in LIVE trading mode. "
                "Use: r live to explicitly confirm real orders.",
                "red",
            )
            return

        run = await self._orchestrator.run_once(max_markets=markets)
        errors = len(run.errors)
        err = f" errors: {errors}" if errors else ""
        self._push(
            f"Run #{run.run_id}: {run.markets_analyzed} markets, "
            f"{run.signals_generated} signals, {run.trades_executed} trades, "
            f"{run.elapsed_seconds:.1f}s ({mode}){err}",
            "green",
        )

    async def _run_simulation(self, arg: str) -> None:
        signal, agents, rounds = 0.0, int(self._orchestrator._config.swarm.num_agents), 30
        tokens = arg.split()
        if tokens:
            signal = float(tokens[0])
        if len(tokens) > 1:
            agents = int(tokens[1])
        if len(tokens) > 2:
            rounds = int(tokens[2])

        result = await self._orchestrator.run_simulation_only(
            signal=signal,
            agents=agents,
            rounds=rounds,
        )
        consensus = result.get("consensus", {})
        self._push(
            f"Simulation: {result.get('rounds', rounds)} rounds, "
            f"direction={consensus.get('direction', 'neutral')} "
            f"agreement={consensus.get('agreement_ratio', 0.0):.2%} "
            f"divergence={consensus.get('divergence', 0):.2f}",
            "green",
        )

    async def _scrape_urls(self, arg: str) -> None:
        urls = arg.split()
        if not urls:
            self._push("Provide URLs: u https://... [https://...]", "yellow")
            return

        scraper = self._orchestrator._scraper
        await scraper.start()
        try:
            results = await self._orchestrator.scrape_and_cache(urls)
        finally:
            await scraper.stop()

        scraped = sum(1 for url in urls if url in results)
        self._push(
            f"Scraped {scraped}/{len(urls)} URLs (cached {len(results)} pages)",
            "green",
        )

    async def _run_backtest(self, arg: str) -> None:
        from fishhook.backtest.engine import BacktestEngine

        markets = 20
        for token in arg.split():
            if token.isdigit():
                markets = int(token)
                break

        engine = BacktestEngine(
            swarm_config=self._orchestrator._swarm._config,
            strategy_config=self._orchestrator._strategy._config,
        )
        result = await engine.run(num_markets=markets)
        data = result.to_dict()
        metrics = data.get("metrics", {})
        self._push(
            f"Backtest ({metrics.get('total_markets', data.get('markets_tested', 0))} "
            f"markets): win_rate={metrics.get('win_rate', 0.0):.2%} "
            f"sharpe={metrics.get('sharpe_ratio', 0.0)} "
            f"pnl={metrics.get('total_pnl', 0.0):+.2f} "
            f"profit_factor={metrics.get('profit_factor', 0.0)}",
            "green",
        )

    def _show_config(self) -> None:
        config = self._orchestrator._config
        mode = self._trading_mode()
        lines = [
            f"[cyan]config_tag:[/cyan] {config.config_tag()}",
            f"[cyan]version:[/cyan] {config.fingerprint()[:12]}",
            f"[cyan]trading_mode:[/cyan] {mode}",
            f"[cyan]agents:[/cyan] {config.swarm.num_agents}  "
            f"[cyan]heterogeneity:[/cyan] {config.swarm.heterogeneity.enabled}  "
            f"[cyan]consensus_threshold:[/cyan] "
            f"{config.swarm.consensus_threshold}",
            f"[cyan]breaker:[/cyan] {config.circuit_breaker.enabled}  "
            f"[cyan]nansen_key:[/cyan] "
            f"{'<set>' if config.data_sources.nansen.api_key else '<unset>'}  "
            f"[cyan]scraper_tokens:[/cyan] "
            f"{len(self._orchestrator._scraper.get_dynamic_tokens())}",
            f"[cyan]snapshot:[/cyan] "
            f"{config.data_dir / 'config_snapshots' / f'{config.fingerprint()}.json'}",
        ]
        self._push("\n".join(lines), "white")

    async def _dispatch(self, command: str, arg: str) -> None:
        if command in ("q", "quit", "exit"):
            self._running = False
        elif command in ("?", "help"):
            self._push(_HELP, "cyan")
        elif command in ("h", "halt"):
            self._handle_halt(arg)
        elif command in ("n", "resume"):
            self._handle_resume()
        elif command in ("c", "config"):
            self._show_config()
        elif command in ("r", "run"):
            self._spawn(self._run_pipeline(arg), f"Running pipeline ({arg or '10 markets'})...")
        elif command in ("s", "sim"):
            self._spawn(self._run_simulation(arg), "Running swarm simulation...")
        elif command in ("u", "url"):
            self._spawn(self._scrape_urls(arg), f"Scraping {len(arg.split())} URL(s)...")
        elif command in ("b", "backtest"):
            self._spawn(self._run_backtest(arg), "Running backtest...")
        else:
            self._push(f"Unknown command: {command!r}. Type ? for help.", "red")

    async def run(self, refresh_seconds: float = 2.0) -> None:
        self._running = True
        self._queue = asyncio.Queue()
        self._push("Interactive TUI ready - type ? for commands.", "cyan")
        reader = asyncio.create_task(self._read_console())

        with Live(self.render(), console=self._console, refresh_per_second=4) as live:
            while self._running:
                live.update(self.render())
                try:
                    command, arg = await asyncio.wait_for(
                        self._queue.get(), timeout=refresh_seconds
                    )
                except asyncio.TimeoutError:
                    continue
                await self._dispatch(command, arg)

        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        self._running = False