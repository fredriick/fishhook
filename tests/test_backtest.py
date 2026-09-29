"""Tests for the backtest engine, fetcher, and metrics."""

from datetime import datetime

import pytest

from fishhook.backtest.engine import DEFAULT_ENTRY_PRICE, BacktestEngine, BacktestTrade
from fishhook.backtest.fetcher import HistoricalDataFetcher, ResolvedMarket
from fishhook.backtest.metrics import BacktestMetrics


def _market(
    outcome_prices: list[float],
    category: str = "crypto",
    condition_id: str = "cond-1",
) -> ResolvedMarket:
    outcomes = ["Yes", "No"]
    return ResolvedMarket(
        id="m1",
        question="Will X happen?",
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        resolution_price=max(outcome_prices),
        resolved_outcome=outcomes[outcome_prices.index(max(outcome_prices))],
        volume=5000.0,
        liquidity=200.0,
        end_date=datetime(2026, 8, 1),
        category=category,
        slug="will-x-happen",
        condition_id=condition_id,
    )


# --- fetcher -----------------------------------------------------------------


def test_cache_round_trip_preserves_fields(tmp_path) -> None:
    fetcher = HistoricalDataFetcher()
    source = _market([0.97, 0.03], category="politics")

    cache_file = tmp_path / "c.json"
    fetcher._save_to_cache([source], cache_file)
    loaded = fetcher._load_from_cache(cache_file)

    restored = loaded[0]
    assert restored.category == "politics"
    assert restored.condition_id == "cond-1"
    assert restored.liquidity == 200.0
    assert restored.slug == "will-x-happen"
    assert restored.end_date == datetime(2026, 8, 1)
    assert restored.was_yes_winner is True
    assert restored.closing_price == 0.97


def test_market_category_falls_back_to_tags() -> None:
    fetcher = HistoricalDataFetcher()
    assert fetcher._market_category({"category": "sports"}) == "sports"
    assert fetcher._market_category({"tags": ["crypto", "solana"]}) == "crypto"
    assert fetcher._market_category({"tags": "sports"}) == "sports"


@pytest.mark.asyncio
async def test_fetch_entry_price_returns_none_without_condition_id() -> None:
    fetcher = HistoricalDataFetcher()
    assert await fetcher.fetch_entry_price("") is None


# --- metrics -----------------------------------------------------------------


def _trade(pnl: float, category: str, edge: float = 0.1) -> BacktestTrade:
    return BacktestTrade(
        market_id="m",
        question="q",
        direction="BUY",
        swarm_signal=0.3,
        market_price=0.55,
        edge=edge,
        confidence=0.7,
        outcome_direction="UP",
        pnl=pnl,
        correct=pnl > 0,
        size=10.0,
        category=category,
    )


def test_metrics_category_breakdown() -> None:
    trades = [
        _trade(10.0, "crypto"),
        _trade(-5.0, "crypto"),
        _trade(20.0, "politics"),
    ]
    metrics = BacktestMetrics.compute(trades)

    assert metrics.trades_by_category == {"crypto": 2, "politics": 1}
    assert metrics.accuracy_by_category["crypto"] == 0.5
    assert metrics.accuracy_by_category["politics"] == 1.0
    assert metrics.pnl_by_category["crypto"] == pytest.approx(5.0)
    assert metrics.pnl_by_category["politics"] == pytest.approx(20.0)


def test_metrics_empty_result() -> None:
    metrics = BacktestMetrics.compute([])
    assert metrics.trades_by_category == {}
    assert metrics.accuracy_by_category == {}
    assert metrics.pnl_by_category == {}


# --- engine -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_record_uses_open_price_without_hint(monkeypatch) -> None:
    engine = BacktestEngine()

    async def no_hint(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(engine._fetcher, "fetch_entry_price", no_hint)

    rec = await engine._resolved_record(_market([0.97, 0.03]))
    assert rec["current_price"] == pytest.approx(DEFAULT_ENTRY_PRICE)
    assert rec["one_day_change"] == pytest.approx(0.47)
    assert rec["outcome_direction"] == "UP"
    assert rec["category"] == "crypto"


@pytest.mark.asyncio
async def test_resolved_record_uses_price_hint(monkeypatch) -> None:
    engine = BacktestEngine()

    async def hint(*args, **kwargs) -> float:
        return 0.6

    monkeypatch.setattr(engine._fetcher, "fetch_entry_price", hint)

    rec = await engine._resolved_record(_market([0.9, 0.1]))
    assert rec["current_price"] == pytest.approx(0.6)


def test_live_record_skips_no_movement() -> None:
    engine = BacktestEngine()
    assert engine._live_record({"id": "x", "prices": [0.55, 0.45], "one_day_change": 0.0}) is None


def test_live_record_direction() -> None:
    engine = BacktestEngine()
    rec = engine._live_record(
        {
            "id": "x",
            "question": "q",
            "prices": [0.55, 0.45],
            "one_day_change": 0.05,
            "category": "sports",
        }
    )
    assert rec["outcome_direction"] == "UP"
    assert rec["current_price"] == pytest.approx(0.55)
    assert rec["category"] == "sports"


@pytest.mark.asyncio
async def test_engine_run_resolved_with_mocked_fetcher(monkeypatch) -> None:
    engine = BacktestEngine()
    markets = [_market([0.9, 0.1], category="crypto")]

    async def fake_fetch(limit=50, category=None, min_volume=1000.0):
        return markets

    async def no_hint(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(engine._fetcher, "fetch_resolved_markets", fake_fetch)
    monkeypatch.setattr(engine._fetcher, "fetch_entry_price", no_hint)

    result = await engine.run(num_markets=5, agents=50, rounds=3)

    assert result.markets_tested == 1
    assert result.config_used["mode"] == "resolved"
    assert result.config_used["entry_assumption"] == "open_price_0.5"
    assert result.raw_accuracy >= 0.0
    assert result.accuracy_by_category_raw.get("crypto", 0.0) >= 0.0