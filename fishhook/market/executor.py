"""Trade executor - manages order lifecycle and position tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fishhook.config.settings import PolymarketConfig
from fishhook.market.circuit_breaker import CircuitBreaker
from fishhook.market.client import PolymarketClient
from fishhook.market.models import OrderSide, OrderType, Position, TradeSignal
from fishhook.market.slippage import SlippageEstimate, SlippageModel
from fishhook.utils.logging import get_logger

logger = get_logger("market.executor")


@dataclass
class ExecutedTrade:
    order_id: str
    market_id: str
    side: str
    price: float
    size: float
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "pending"
    fill_price: float | None = None
    fill_size: float | None = None
    paper: bool = False
    slippage_cost: float = 0.0
    pre_edge: float = 0.0
    post_edge: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "market_id": self.market_id,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status,
            "fill_price": self.fill_price,
            "fill_size": self.fill_size,
            "paper": self.paper,
            "slippage_cost": round(self.slippage_cost, 4),
            "pre_edge": round(self.pre_edge, 4),
            "post_edge": round(self.post_edge, 4),
        }


class TradeExecutor:
    def __init__(
        self,
        client: PolymarketClient,
        config: PolymarketConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        paper_trading: bool = False,
        slippage_model: SlippageModel | None = None,
    ) -> None:
        self._client = client
        self._config = config or PolymarketConfig()
        self._positions: dict[str, Position] = {}
        self._trade_history: list[ExecutedTrade] = []
        self._last_trade_time: float = 0
        self._trades_this_hour: int = 0
        self._hour_start: float = time.time()
        self._circuit_breaker = circuit_breaker
        self._paper_trading = paper_trading or self._config.testnet
        self._paper_positions: dict[str, Position] = {}
        self._slippage_model = slippage_model

    @property
    def positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def trade_history(self) -> list[ExecutedTrade]:
        return list(self._trade_history)

    @property
    def total_trades(self) -> int:
        return len(self._trade_history)

    @property
    def is_paper_trading(self) -> bool:
        return self._paper_trading

    @property
    def trades_remaining_this_hour(self) -> int:
        elapsed = time.time() - self._hour_start
        if elapsed > 3600:
            self._trades_this_hour = 0
            self._hour_start = time.time()
        return max(0, 10 - self._trades_this_hour)

    def _check_rate_limits(self) -> bool:
        if self.trades_remaining_this_hour <= 0:
            logger.warning("Hourly trade limit reached")
            return False
        return True

    def _check_position_size(self, price: float, size: float) -> bool:
        total_value = price * size
        if total_value > self._config.max_position_size:
            logger.warning(
                f"Position size ${total_value:.2f} exceeds max ${self._config.max_position_size}"
            )
            return False
        return True

    async def _estimate_slippage(self, signal: TradeSignal) -> SlippageEstimate | None:
        if not self._slippage_model:
            return None
        try:
            order_book = await self._client.get_order_book(signal.market_id)
            return self._slippage_model.estimate(
                order_book=order_book,
                side=signal.side.value,
                price=signal.price,
                size=signal.size,
                edge=signal.edge,
            )
        except Exception as e:
            logger.warning(f"Slippage estimation failed: {e}")
            return None

    async def execute_signal(self, signal: TradeSignal) -> ExecutedTrade | None:
        if not signal.is_actionable:
            logger.info(
                f"Signal not actionable: edge={signal.edge:.4f}, confidence={signal.confidence:.4f}"
            )
            return None

        if self._circuit_breaker:
            allowed, reason = self._circuit_breaker.check_before_trade()
            if not allowed:
                logger.warning(f"Circuit breaker blocked trade: {reason}")
                return None

        if not self._check_rate_limits():
            return None

        if not self._check_position_size(signal.price, signal.size):
            return None

        slippage = await self._estimate_slippage(signal)
        if slippage and not slippage.accept:
            logger.info(
                f"Slippage rejected trade for {signal.market_id}: {slippage.reason}"
            )
            return None

        if signal.edge < self._config.min_edge_threshold:
            logger.info(
                f"Edge {signal.edge:.4f} below threshold {self._config.min_edge_threshold}"
            )
            return None

        slippage_cost = slippage.total_slippage_cost if slippage else 0.0
        post_edge = slippage.post_edge if slippage else signal.edge

        if self._paper_trading:
            return self._execute_paper_trade(signal, slippage_cost, post_edge)

        result = await self._client.place_order(
            token_id=signal.market_id,
            side=signal.side.value,
            price=signal.price,
            size=signal.size,
        )

        if result:
            trade = ExecutedTrade(
                order_id=result.get("orderId", f"local_{int(time.time())}"),
                market_id=signal.market_id,
                side=signal.side.value,
                price=signal.price,
                size=signal.size,
                status=result.get("status", "submitted"),
                slippage_cost=slippage_cost,
                pre_edge=signal.edge,
                post_edge=post_edge,
            )
            self._trade_history.append(trade)
            self._trades_this_hour += 1
            self._last_trade_time = time.time()

            self._update_position(signal)

            logger.info(
                f"Executed: {signal.side.value} {signal.size} @ ${signal.price} "
                f"(edge={signal.edge:.4f}, post_edge={post_edge:.4f}, slip={slippage_cost:.4f})"
            )
            return trade

        return None

    def _execute_paper_trade(
        self, signal: TradeSignal, slippage_cost: float, post_edge: float
    ) -> ExecutedTrade:
        trade = ExecutedTrade(
            order_id=f"paper_{int(time.time())}",
            market_id=signal.market_id,
            side=signal.side.value,
            price=signal.price,
            size=signal.size,
            status="paper_filled",
            fill_price=signal.price,
            fill_size=signal.size,
            paper=True,
            slippage_cost=slippage_cost,
            pre_edge=signal.edge,
            post_edge=post_edge,
        )
        self._trade_history.append(trade)
        self._trades_this_hour += 1
        self._last_trade_time = time.time()
        self._update_position(signal)

        logger.info(
            f"[PAPER] {signal.side.value} {signal.size} @ ${signal.price} "
            f"(edge={signal.edge:.4f}, post_edge={post_edge:.4f}, slip={slippage_cost:.4f})"
        )
        return trade

    async def execute_signals(self, signals: list[TradeSignal]) -> list[ExecutedTrade]:
        executed = []
        for signal in signals:
            trade = await self.execute_signal(signal)
            if trade:
                executed.append(trade)
            if not self._check_rate_limits():
                break
        return executed

    def _update_position(self, signal: TradeSignal) -> None:
        key = signal.market_id
        if key in self._positions:
            pos = self._positions[key]
            if signal.side == OrderSide.BUY:
                total_cost = pos.avg_price * pos.size + signal.price * signal.size
                total_size = pos.size + signal.size
                pos.avg_price = (
                    total_cost / total_size if total_size > 0 else pos.avg_price
                )
                pos.size = total_size
            else:
                pos.size = max(0, pos.size - signal.size)
                if pos.size == 0:
                    del self._positions[key]
                    return
            pos.current_price = signal.price
            pos.unrealized_pnl = (pos.current_price - pos.avg_price) * pos.size
        else:
            if signal.side == OrderSide.BUY:
                pos = Position(
                    market_id=signal.market_id,
                    token_id=signal.market_id,
                    outcome="yes",
                    size=signal.size,
                    avg_price=signal.price,
                    current_price=signal.price,
                )
                pos.unrealized_pnl = (pos.current_price - pos.avg_price) * pos.size
                self._positions[key] = pos

    def get_portfolio_summary(self) -> dict[str, Any]:
        total_value = sum(p.size * p.current_price for p in self._positions.values())
        total_pnl = sum(p.unrealized_pnl for p in self._positions.values())
        winning = sum(1 for p in self._positions.values() if p.unrealized_pnl > 0)
        losing = sum(1 for p in self._positions.values() if p.unrealized_pnl < 0)

        total_slippage = sum(
            t.slippage_cost for t in self._trade_history if not t.paper
        )

        result: dict[str, Any] = {
            "positions": len(self._positions),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "winning_positions": winning,
            "losing_positions": losing,
            "total_trades": self.total_trades,
            "trades_remaining_hour": self.trades_remaining_this_hour,
            "paper_trading": self._paper_trading,
            "total_slippage_cost": round(total_slippage, 4),
        }

        if self._circuit_breaker:
            result["circuit_breaker"] = self._circuit_breaker.get_status()

        return result
