"""Regression tests for hourly trade rate limiting in the executor."""

import pytest

from fishhook.config.settings import PolymarketConfig
from fishhook.market.executor import TradeExecutor
from fishhook.market.models import OrderSide, TradeSignal


def _signal(market_id: str = "m1") -> TradeSignal:
    return TradeSignal(
        market_id=market_id,
        side=OrderSide.BUY,
        price=0.5,
        size=1.0,
        confidence=0.8,
        edge=0.2,
        reason="test",
        swarm_signal=0.5,
        market_price=0.5,
    )


def test_default_rate_limit_is_ten() -> None:
    executor = TradeExecutor(client=object())
    assert executor.trades_remaining_this_hour == 10


def test_rate_limit_honors_configured_value() -> None:
    executor = TradeExecutor(client=object(), max_trades_per_hour=3)
    assert executor.trades_remaining_this_hour == 3

    executor._trades_this_hour = 2
    assert executor.trades_remaining_this_hour == 1

    executor._trades_this_hour = 5
    assert executor.trades_remaining_this_hour == 0


def test_rate_limit_clamped_to_at_least_one() -> None:
    executor = TradeExecutor(client=object(), max_trades_per_hour=0)
    assert executor.trades_remaining_this_hour == 1


@pytest.mark.asyncio
async def test_execute_signal_blocks_after_limit() -> None:
    executor = TradeExecutor(client=object(), max_trades_per_hour=2)

    first = await executor.execute_signal(_signal("m1"))
    second = await executor.execute_signal(_signal("m2"))
    third = await executor.execute_signal(_signal("m3"))

    assert first is not None
    assert second is not None
    assert third is None
    assert executor.total_trades == 2


def test_paper_trading_flag_enables_paper_mode() -> None:
    executor = TradeExecutor(client=object(), paper_trading=True)
    assert executor.is_paper_trading is True


def test_paper_mode_uses_real_mainnet_prices_when_requested() -> None:
    config = PolymarketConfig(testnet=False, paper_trading=True)
    executor = TradeExecutor(client=object(), config=config)
    assert executor.is_paper_trading is True


def test_mainnet_without_paper_flag_is_live() -> None:
    config = PolymarketConfig(testnet=False, paper_trading=False)
    executor = TradeExecutor(client=object(), config=config)
    assert executor.is_paper_trading is False


def test_testnet_still_forces_paper_mode() -> None:
    config = PolymarketConfig(testnet=True, paper_trading=False)
    executor = TradeExecutor(client=object(), config=config)
    assert executor.is_paper_trading is True