"""Regression tests for signal staleness handling in the strategy engine."""

import time
from types import SimpleNamespace

import pytest

from fishhook.config.settings import StrategyConfig
from fishhook.strategy.engine import StrategyEngine


def _engine(**overrides: object) -> StrategyEngine:
    engine = StrategyEngine(config=StrategyConfig(**overrides))
    engine._initialized = True
    return engine


def test_unseen_market_is_not_stale() -> None:
    engine = _engine(signal_ttl_seconds=300)
    assert engine._is_signal_stale("m1") is False


def test_recent_signal_is_not_stale() -> None:
    engine = _engine(signal_ttl_seconds=300)
    engine._state.signal_timestamps["m1"] = time.time()
    assert engine._is_signal_stale("m1") is False


def test_market_becomes_stale_after_ttl() -> None:
    engine = _engine(signal_ttl_seconds=5)
    engine._state.signal_timestamps["m1"] = time.time() - 100
    assert engine._is_signal_stale("m1") is True


def test_disabled_ttl_never_stale() -> None:
    engine = _engine(signal_ttl_seconds=0)
    engine._state.signal_timestamps["m1"] = time.time() - 100
    assert engine._is_signal_stale("m1") is False


@pytest.mark.asyncio
async def test_analyze_market_skips_stale_signal() -> None:
    engine = _engine(cooldown_seconds=0, signal_ttl_seconds=5)
    engine._state.last_signal_time = time.time() - 10
    engine._state.signal_timestamps["m1"] = time.time() - 100

    signal = await engine.analyze_market(SimpleNamespace(id="m1"))

    assert signal is None
    assert "m1" not in engine._state.market_signals
    assert engine._state.signals_generated == 0