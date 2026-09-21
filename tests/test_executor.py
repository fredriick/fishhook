"""Regression tests for hourly trade rate limiting in the executor."""

import pytest

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