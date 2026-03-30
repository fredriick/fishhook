"""Terminal dashboard using Rich for live pipeline visualization."""

from __future__ import annotations

import asyncio
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fishhook.orchestrator import PipelineOrchestrator


class TerminalDashboard:
    def __init__(self, orchestrator: PipelineOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._console = Console()
        self._running = False

    def build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
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

    def render_footer(self) -> Panel:
        text = Text.from_markup(
            " [dim]Ctrl+C to quit  |  Data refreshes every cycle[/dim]"
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
        layout["footer"].update(self.render_footer())
        return layout

    async def run(self, refresh_seconds: float = 2.0) -> None:
        self._running = True
        with Live(self.render(), console=self._console, refresh_per_second=4) as live:
            while self._running:
                live.update(self.render())
                await asyncio.sleep(refresh_seconds)

    def stop(self) -> None:
        self._running = False
