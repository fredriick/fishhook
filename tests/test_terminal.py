"""Tests for the interactive terminal dashboard (parity with web dashboard)."""

from __future__ import annotations

import asyncio

import pytest

from fishhook.config.settings import PipelineConfig
from fishhook.dashboard.terminal import TerminalDashboard
from fishhook.orchestrator import PipelineOrchestrator


@pytest.fixture
def dterminal() -> TerminalDashboard:
    return TerminalDashboard(PipelineOrchestrator(PipelineConfig()))


class FakeRun:
    def __init__(self) -> None:
        self.run_id = 7
        self.markets_analyzed = 3
        self.signals_generated = 2
        self.trades_executed = 1
        self.elapsed_seconds = 1.5
        self.errors = []

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "markets_analyzed": self.markets_analyzed,
            "signals_generated": self.signals_generated,
            "trades_executed": self.trades_executed,
            "errors": self.errors,
        }


class FakeBreaker:
    def __init__(self) -> None:
        self.state = "closed"
        self.reason = ""

    def force_open(self, reason: str) -> None:
        self.state = "open"
        self.reason = reason

    def force_close(self, reason: str) -> None:
        self.state = "closed"

    def get_status(self) -> dict:
        return {"state": self.state, "reason": self.reason}


def test_layout_includes_console_panel(dterminal: TerminalDashboard) -> None:
    layout = dterminal.build_layout()
    names = [c.name for c in layout.children]
    assert "console" in names


@pytest.mark.parametrize("count,expected_last", [(0, None), (13, "message 12")])
def test_message_cap(
    dterminal: TerminalDashboard, count: int, expected_last: str | None
) -> None:
    for i in range(count):
        dterminal._push(f"message {i}")
    assert len(dterminal._messages) == min(count, TerminalDashboard.MAX_MESSAGES)
    rendered = dterminal.render_console().renderable
    if expected_last is None:
        assert "No activity yet" in rendered
        assert "message" not in rendered
    else:
        assert expected_last in rendered
        assert "message 0" not in rendered


def test_trading_mode_defaults_to_testnet(dterminal: TerminalDashboard) -> None:
    assert dterminal._trading_mode() == "testnet"


def test_trading_mode_live(dterminal: TerminalDashboard) -> None:
    config = dterminal._orchestrator._config
    config.polymarket.testnet = False
    config.polymarket.paper_trading = False
    assert dterminal._trading_mode() == "live"


@pytest.mark.asyncio
async def test_live_run_refused_without_confirmation(dterminal: TerminalDashboard) -> None:
    config = dterminal._orchestrator._config
    config.polymarket.testnet = False
    config.polymarket.paper_trading = False
    calls: list[int] = []

    async def fake_run_once(**kwargs) -> FakeRun:
        calls.append(kwargs.get("max_markets"))
        return FakeRun()

    dterminal._orchestrator.run_once = fake_run_once

    await dterminal._run_pipeline("5")
    await dterminal._run_pipeline("3")

    assert calls == []
    assert any("LIVE trading mode" in m for m in dterminal._messages)


@pytest.mark.asyncio
async def test_live_run_allowed_with_live_confirmation(dterminal: TerminalDashboard) -> None:
    config = dterminal._orchestrator._config
    config.polymarket.testnet = False
    config.polymarket.paper_trading = False
    calls: list[int] = []

    async def fake_run_once(**kwargs) -> FakeRun:
        calls.append(kwargs.get("max_markets"))
        return FakeRun()

    dterminal._orchestrator.run_once = fake_run_once

    await dterminal._run_pipeline("live 5")

    assert calls == [5]
    assert any("Run #7" in m for m in dterminal._messages)


def test_halt_without_breaker(dterminal: TerminalDashboard) -> None:
    dterminal._orchestrator._circuit_breaker = None
    dterminal._handle_halt("testing")
    assert any("not enabled" in m for m in dterminal._messages)


def test_halt_and_resume_with_breaker(dterminal: TerminalDashboard) -> None:
    breaker = FakeBreaker()
    dterminal._orchestrator._circuit_breaker = breaker

    dterminal._handle_halt("manual halt via tui")
    assert breaker.state == "open"
    assert any("manual halt via tui" in m for m in dterminal._messages)

    dterminal._handle_resume()
    assert breaker.state == "closed"
    assert any("Resumed" in m for m in dterminal._messages)


def test_show_config_summary(dterminal: TerminalDashboard) -> None:
    dterminal._show_config()
    joined = " ".join(dterminal._messages)
    assert "config_tag" in joined
    assert "trading_mode:" in joined
    assert "testnet" in joined


@pytest.mark.asyncio
async def test_busy_guard_queues_second_command(dterminal: TerminalDashboard) -> None:
    calls = []
    dterminal._busy = True

    async def fake_run(**kwargs) -> FakeRun:
        calls.append(1)
        return FakeRun()

    dterminal._orchestrator.run_once = fake_run

    dterminal._spawn(dterminal._run_pipeline("10"), "Running pipeline...")
    await asyncio.sleep(0.01)

    assert calls == []
    assert any("One command at a time" in m for m in dterminal._messages)