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


class _RecordingDeduplicator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def add(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_deduplicator_records_implied_signal_without_scraped_data() -> None:
    from fishhook.market.models import Market, MarketStatus

    dedup = _RecordingDeduplicator()
    engine = _engine(cooldown_seconds=0)
    engine._deduplicator = dedup
    market = Market(
        id="m1",
        question="Will X happen?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.55, 0.45],
        volume=100.0,
        liquidity=10.0,
        status=MarketStatus.ACTIVE,
    )

    await engine._compute_market_signal(market, None)

    assert dedup.calls, "implied_probability signal must be deduped even without scraped data"
    assert dedup.calls[-1]["source"] == "implied_probability"
    assert dedup.calls[-1]["metadata"] == {"market_id": "m1"}