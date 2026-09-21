"""Regression tests for edge attribution direction tracking."""

from fishhook.market.attribution import EdgeAttributionTracker


def _record(
    tracker: EdgeAttributionTracker,
    order_id: str = "o1",
    side: str = "BUY",
) -> None:
    tracker.record(
        order_id=order_id,
        market_id="m1",
        side=side,
        predicted_edge=0.1,
        post_slippage_edge=0.08,
        signal_confidence=0.7,
        swarm_signal=0.5,
    )


def test_long_prediction_correct_on_uptick() -> None:
    tracker = EdgeAttributionTracker()
    _record(tracker)
    tracker.resolve_trade("o1", realized_pnl=50.0, actual_price_move=0.2)

    assert tracker._attributions[0].was_correct is True


def test_long_prediction_wrong_on_downtick() -> None:
    tracker = EdgeAttributionTracker()
    _record(tracker)
    tracker.resolve_trade("o1", realized_pnl=-40.0, actual_price_move=-0.2)

    assert tracker._attributions[0].was_correct is False


def test_short_prediction_correct_on_downtick() -> None:
    tracker = EdgeAttributionTracker()
    _record(tracker, side="SELL")
    tracker.resolve_trade("o1", realized_pnl=30.0, actual_price_move=-0.2)

    assert tracker._attributions[0].was_correct is True


def test_short_prediction_wrong_on_uptick() -> None:
    tracker = EdgeAttributionTracker()
    _record(tracker, side="SELL")
    tracker.resolve_trade("o1", realized_pnl=30.0, actual_price_move=0.2)

    assert tracker._attributions[0].was_correct is False


def test_correctness_tracks_direction_not_profit() -> None:
    tracker = EdgeAttributionTracker()
    _record(tracker)
    tracker.resolve_trade("o1", realized_pnl=-15.0, actual_price_move=0.2)

    assert tracker._attributions[0].was_correct is True


def test_flat_market_never_correct() -> None:
    tracker = EdgeAttributionTracker()
    _record(tracker)
    tracker.resolve_trade("o1", realized_pnl=0.0, actual_price_move=0.0)

    assert tracker._attributions[0].was_correct is False