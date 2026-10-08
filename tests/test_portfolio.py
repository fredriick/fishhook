"""Tests for portfolio P&L tracking and the persistent trade ledger."""

from __future__ import annotations

import asyncio

import pytest

from fishhook.config.settings import PipelineConfig
from fishhook.market.executor import ExecutedTrade, TradeExecutor
from fishhook.market.models import OrderSide, TradeSignal
from fishhook.orchestrator import PipelineOrchestrator
from fishhook.portfolio.ledger import PortfolioLedger


def _signal(
    market_id: str,
    side: OrderSide,
    price: float,
    size: float,
) -> TradeSignal:
    return TradeSignal(
        market_id=market_id,
        side=side,
        price=price,
        size=size,
        confidence=0.8,
        edge=0.2,
        reason="test",
        swarm_signal=0.5,
        market_price=price,
    )


def _trade(**overrides) -> ExecutedTrade:
    base = {
        "order_id": "o1",
        "market_id": "m1",
        "side": "BUY",
        "price": 0.5,
        "size": 2.0,
    }
    base.update(overrides)
    return ExecutedTrade(**base)


def test_executed_trade_dict_roundtrip() -> None:
    trade = _trade(order_id="abc", side="SELL", price=0.7, size=1.5, paper=True)
    restored = ExecutedTrade.from_dict(trade.to_dict())
    assert restored.order_id == trade.order_id
    assert restored.market_id == trade.market_id
    assert restored.side == trade.side
    assert restored.price == trade.price
    assert restored.size == trade.size
    assert restored.paper == trade.paper


def test_ledger_persists_trades_snapshots_and_state(tmp_path) -> None:
    ledger = PortfolioLedger(tmp_path)
    ledger.record_trade(_trade())
    ledger.record_snapshot({"positions": 1, "total_pnl": 0.5})
    ledger.save_state(0.2)

    loaded = PortfolioLedger(tmp_path).load()
    assert len(loaded["trades"]) == 1
    assert loaded["trades"][0]["order_id"] == "o1"
    assert loaded["realized_pnl"] == pytest.approx(0.2)
    assert len(loaded["snapshots"]) == 1
    assert loaded["snapshots"][0]["portfolio"]["positions"] == 1


def test_ledger_tolerates_missing_files(tmp_path) -> None:
    loaded = PortfolioLedger(tmp_path / "empty").load()
    assert loaded["trades"] == []
    assert loaded["realized_pnl"] == 0.0
    assert loaded["snapshots"] == []


@pytest.mark.asyncio
async def test_executor_realized_pnl_on_partial_close() -> None:
    executor = TradeExecutor(client=object(), paper_trading=True)
    await executor.execute_signal(_signal("m1", OrderSide.BUY, 0.5, 2.0))
    await executor.execute_signal(_signal("m1", OrderSide.SELL, 0.7, 1.0))

    summary = executor.get_portfolio_summary()
    assert summary["realized_pnl"] == pytest.approx(0.2)
    assert summary["unrealized_pnl"] == pytest.approx(0.2)
    assert summary["total_pnl"] == pytest.approx(0.4)
    assert summary["positions"] == 1


@pytest.mark.asyncio
async def test_executor_full_close_sets_realized_pnl() -> None:
    executor = TradeExecutor(client=object(), paper_trading=True)
    await executor.execute_signal(_signal("m1", OrderSide.BUY, 0.5, 2.0))
    await executor.execute_signal(_signal("m1", OrderSide.SELL, 0.8, 2.0))

    summary = executor.get_portfolio_summary()
    assert summary["realized_pnl"] == pytest.approx(0.6)
    assert summary["unrealized_pnl"] == pytest.approx(0.0)
    assert summary["positions"] == 0


def test_executor_restore_rebuilds_history_positions_and_pnl(tmp_path) -> None:
    ledger = PortfolioLedger(tmp_path)
    ledger.record_trade(_trade(order_id="b1", price=0.5, size=2.0))
    ledger.record_trade(_trade(order_id="s1", side="SELL", price=0.7, size=1.0))
    ledger.save_state(0.2)

    data = ledger.load()
    executor = TradeExecutor(client=object())
    count = executor.restore(data["trades"], data["realized_pnl"])

    assert count == 2
    assert executor.total_trades == 2
    assert executor.realized_pnl == pytest.approx(0.2)
    summary = executor.get_portfolio_summary()
    assert summary["positions"] == 1
    assert summary["unrealized_pnl"] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_orchestrator_restores_ledger_on_start(tmp_path) -> None:
    ledger = PortfolioLedger(tmp_path)
    ledger.record_trade(_trade(order_id="paper_1", market_id="m9", price=0.5, size=1.0))
    ledger.save_state(0.0)

    orchestrator = PipelineOrchestrator(PipelineConfig(data_dir=tmp_path))

    async def _noop() -> None:
        pass

    orchestrator._scraper.start = _noop
    orchestrator._scraper.stop = _noop

    await orchestrator.start()
    try:
        assert orchestrator._executor.total_trades == 1
        assert orchestrator._executor.trade_history[0].order_id == "paper_1"
        assert orchestrator._ledger is not None
    finally:
        await orchestrator.stop()


def test_ledger_disabled_when_config_off(tmp_path) -> None:
    config = PipelineConfig(data_dir=tmp_path, portfolio={"enabled": False})
    orchestrator = PipelineOrchestrator(config)
    assert orchestrator._ledger is None