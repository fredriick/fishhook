"""Market data models for Polymarket."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MarketStatus(Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


@dataclass
class Market:
    id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    volume: float
    liquidity: float
    status: MarketStatus
    end_date: datetime | None = None
    category: str = ""
    tags: list[str] = field(default_factory=list)
    condition_id: str = ""
    slug: str = ""

    @classmethod
    def from_gamma_api(cls, data: dict[str, Any]) -> Market:
        prices = []
        if "outcomePrices" in data:
            raw = data["outcomePrices"]
            if isinstance(raw, str):
                import json

                try:
                    prices = [float(p) for p in json.loads(raw)]
                except (json.JSONDecodeError, TypeError, ValueError):
                    prices = []
            elif isinstance(raw, list):
                prices = [float(p) for p in raw]

        outcomes = []
        if "outcomes" in data:
            raw = data["outcomes"]
            if isinstance(raw, str):
                import json

                try:
                    outcomes = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    outcomes = []
            elif isinstance(raw, list):
                outcomes = raw

        end_date = None
        if data.get("endDate"):
            try:
                end_date = datetime.fromisoformat(
                    data["endDate"].replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        return cls(
            id=data.get("id", ""),
            question=data.get("question", ""),
            outcomes=outcomes,
            outcome_prices=prices,
            volume=float(data.get("volume", 0)),
            liquidity=float(data.get("liquidity", 0)),
            status=MarketStatus.ACTIVE,
            end_date=end_date,
            category=data.get("category", ""),
            condition_id=data.get("conditionId", ""),
            slug=data.get("slug", ""),
        )

    @property
    def yes_price(self) -> float:
        if self.outcome_prices:
            return self.outcome_prices[0]
        return 0.0

    @property
    def no_price(self) -> float:
        if len(self.outcome_prices) > 1:
            return self.outcome_prices[1]
        return 1.0 - self.yes_price

    @property
    def implied_probability(self) -> float:
        return self.yes_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "outcomes": self.outcomes,
            "prices": self.outcome_prices,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "status": self.status.value,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
        }


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    token_id: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_clob_api(cls, data: dict[str, Any]) -> OrderBook:
        bids = []
        asks = []
        for b in data.get("bids", []):
            bids.append(
                OrderBookLevel(
                    price=float(b.get("price", 0)),
                    size=float(b.get("size", 0)),
                )
            )
        for a in data.get("asks", []):
            asks.append(
                OrderBookLevel(
                    price=float(a.get("price", 0)),
                    size=float(a.get("size", 0)),
                )
            )
        return cls(
            token_id=data.get("token_id", ""),
            bids=bids,
            asks=asks,
        )

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def bid_depth(self) -> float:
        return sum(l.size for l in self.bids[:5])

    @property
    def ask_depth(self) -> float:
        return sum(l.size for l in self.asks[:5])


@dataclass
class Position:
    market_id: str
    token_id: str
    outcome: str
    size: float
    avg_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    @property
    def pnl_percent(self) -> float:
        if self.avg_price == 0:
            return 0.0
        return ((self.current_price - self.avg_price) / self.avg_price) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "outcome": self.outcome,
            "size": self.size,
            "avg_price": self.avg_price,
            "current_price": self.current_price,
            "pnl_percent": round(self.pnl_percent, 2),
        }


@dataclass
class TradeSignal:
    market_id: str
    side: OrderSide
    price: float
    size: float
    confidence: float
    edge: float
    reason: str
    swarm_signal: float
    market_price: float
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_actionable(self) -> bool:
        return self.edge > 0 and self.confidence > 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "side": self.side.value,
            "price": self.price,
            "size": self.size,
            "confidence": round(self.confidence, 4),
            "edge": round(self.edge, 4),
            "reason": self.reason,
            "swarm_signal": round(self.swarm_signal, 4),
            "market_price": self.market_price,
            "timestamp": self.timestamp.isoformat(),
            "actionable": self.is_actionable,
        }
