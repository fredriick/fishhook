"""Circuit breaker - halts trading when risk limits are breached."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("market.circuit_breaker")


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class BreakerEvent:
    reason: str
    state: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "state": self.state, "timestamp": self.timestamp}


class CircuitBreaker:
    def __init__(
        self,
        max_drawdown_pct: float = 10.0,
        drawdown_window_hours: float = 4.0,
        max_consecutive_losses: int = 5,
        max_api_errors_per_hour: int = 10,
        cooldown_seconds: int = 300,
    ) -> None:
        self._max_drawdown_pct = max_drawdown_pct
        self._drawdown_window_hours = drawdown_window_hours
        self._max_consecutive_losses = max_consecutive_losses
        self._max_api_errors_per_hour = max_api_errors_per_hour
        self._cooldown_seconds = cooldown_seconds

        self._state = BreakerState.CLOSED
        self._opened_at: float = 0
        self._peak_value: float = 0
        self._current_value: float = 0
        self._consecutive_losses: int = 0
        self._api_errors: list[float] = []
        self._events: list[BreakerEvent] = []
        self._trades: list[dict[str, Any]] = []

    @property
    def state(self) -> BreakerState:
        if self._state == BreakerState.OPEN:
            if time.time() - self._opened_at >= self._cooldown_seconds:
                self._state = BreakerState.HALF_OPEN
                self._log_event("Entering half-open state after cooldown")
        return self._state

    @property
    def is_trading_allowed(self) -> bool:
        return self.state in (BreakerState.CLOSED, BreakerState.HALF_OPEN)

    @property
    def current_drawdown_pct(self) -> float:
        if self._peak_value <= 0:
            return 0.0
        return ((self._peak_value - self._current_value) / self._peak_value) * 100

    def check_before_trade(self) -> tuple[bool, str]:
        state = self.state

        if state == BreakerState.OPEN:
            return False, f"Circuit breaker OPEN: trading halted"

        if state == BreakerState.HALF_OPEN:
            logger.info("Circuit breaker in half-open state, allowing single trade")
            return True, "Half-open: single trade allowed"

        drawdown = self.current_drawdown_pct
        if drawdown > self._max_drawdown_pct:
            self._trip(
                f"Drawdown {drawdown:.1f}% exceeds limit {self._max_drawdown_pct}%"
            )
            return False, f"Drawdown {drawdown:.1f}% exceeds limit"

        if self._consecutive_losses >= self._max_consecutive_losses:
            self._trip(
                f"Consecutive losses {self._consecutive_losses} >= {self._max_consecutive_losses}"
            )
            return False, f"Too many consecutive losses: {self._consecutive_losses}"

        recent_errors = self._get_recent_api_errors()
        if recent_errors >= self._max_api_errors_per_hour:
            self._trip(
                f"API errors {recent_errors}/hr exceeds limit {self._max_api_errors_per_hour}"
            )
            return False, f"API error rate too high: {recent_errors}/hr"

        return True, "OK"

    def record_trade(self, pnl: float, market_id: str = "") -> None:
        self._trades.append({"pnl": pnl, "market_id": market_id, "time": time.time()})
        self._current_value += pnl

        if self._current_value > self._peak_value:
            self._peak_value = self._current_value

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._state == BreakerState.HALF_OPEN:
            if pnl > 0:
                self._reset("Trade profitable in half-open state")
            else:
                self._trip("Loss in half-open state, reopening breaker")

    def record_api_error(self) -> None:
        self._api_errors.append(time.time())
        self._api_errors = [t for t in self._api_errors if time.time() - t < 3600]

    def force_open(self, reason: str = "Manual halt") -> None:
        self._trip(reason)

    def force_close(self, reason: str = "Manual resume") -> None:
        self._reset(reason)

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.time()
        self._log_event(reason)
        logger.warning(f"Circuit breaker TRIPPED: {reason}")

    def _reset(self, reason: str) -> None:
        self._state = BreakerState.CLOSED
        self._consecutive_losses = 0
        self._log_event(reason)
        logger.info(f"Circuit breaker RESET: {reason}")

    def _log_event(self, reason: str) -> None:
        self._events.append(BreakerEvent(reason=reason, state=self._state.value))
        if len(self._events) > 100:
            self._events = self._events[-100:]

    def _get_recent_api_errors(self) -> int:
        cutoff = time.time() - 3600
        self._api_errors = [t for t in self._api_errors if t > cutoff]
        return len(self._api_errors)

    def get_status(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "trading_allowed": self.is_trading_allowed,
            "drawdown_pct": round(self.current_drawdown_pct, 2),
            "max_drawdown_pct": self._max_drawdown_pct,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self._max_consecutive_losses,
            "api_errors_last_hour": self._get_recent_api_errors(),
            "total_trades": len(self._trades),
            "events": [e.to_dict() for e in self._events[-5:]],
        }
