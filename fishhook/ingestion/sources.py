"""Structured API data sources - supplements Playwright scraping with direct API integrations."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.sources")


@dataclass
class SourceSignal:
    value: float
    confidence: float
    source_name: str
    category: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def is_stale(self, ttl_seconds: int) -> bool:
        return (time.time() - self.timestamp) > ttl_seconds


class DataSource(ABC):
    def __init__(self, name: str, api_key: str = "", base_url: str = "") -> None:
        self.name = name
        self._api_key = api_key
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0
        self._min_interval: float = 1.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                timeout=30.0, headers=headers, follow_redirects=True
            )
        return self._client

    async def _rate_limited_get(self, url: str, **kwargs: Any) -> httpx.Response:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            import asyncio

            await asyncio.sleep(self._min_interval - elapsed)
        client = await self._get_client()
        self._last_request_time = time.time()
        return await client.get(url, **kwargs)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @abstractmethod
    async def fetch_signals(
        self, market_id: str | None = None, **kwargs: Any
    ) -> list[SourceSignal]: ...

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "base_url": self._base_url}


class DuneAnalytics(DataSource):
    def __init__(self, api_key: str = "", query_ids: list[int] | None = None) -> None:
        super().__init__(
            name="dune",
            api_key=api_key,
            base_url="https://api.dune.com/api/v1",
        )
        self._query_ids = query_ids or []
        self._min_interval = 2.0

    async def execute_query(self, query_id: int) -> dict[str, Any] | None:
        if not self._api_key:
            return None
        try:
            resp = await self._rate_limited_get(
                f"{self._base_url}/query/{query_id}/results",
                params={"limit": 100},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {})
        except Exception as e:
            logger.warning(f"Dune query {query_id} failed: {e}")
            return None

    async def fetch_signals(
        self, market_id: str | None = None, **kwargs: Any
    ) -> list[SourceSignal]:
        signals = []
        if not self._api_key:
            logger.debug("Dune API key not configured, skipping")
            return signals

        for query_id in self._query_ids:
            result = await self.execute_query(query_id)
            if not result or "rows" not in result:
                continue
            for row in result["rows"]:
                value = float(row.get("signal_value", 0))
                confidence = float(row.get("confidence", 0.5))
                category = str(row.get("category", "on_chain"))
                signals.append(
                    SourceSignal(
                        value=value,
                        confidence=confidence,
                        source_name="dune",
                        category=category,
                        metadata={"query_id": query_id, "row": row},
                    )
                )
        return signals

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["query_ids"] = self._query_ids
        return d


class OrderBookSignalSource(DataSource):
    def __init__(self, client: Any) -> None:
        super().__init__(name="orderbook")
        self._market_client = client

    async def fetch_signals(
        self, market_id: str | None = None, **kwargs: Any
    ) -> list[SourceSignal]:
        signals = []
        if not market_id:
            return signals

        token_ids: list[str] = kwargs.get("token_ids", [market_id])
        for token_id in token_ids:
            try:
                order_book = await self._market_client.get_order_book(token_id)
                if not order_book or (not order_book.bids and not order_book.asks):
                    continue

                bid_depth = order_book.bid_depth
                ask_depth = order_book.ask_depth
                total_depth = bid_depth + ask_depth

                if total_depth > 0:
                    imbalance = (bid_depth - ask_depth) / total_depth
                else:
                    imbalance = 0.0

                spread = order_book.spread
                spread_signal = max(0.0, 1.0 - spread * 20)

                signals.append(
                    SourceSignal(
                        value=imbalance,
                        confidence=min(1.0, spread_signal * 0.5 + 0.3),
                        source_name="orderbook",
                        category="liquidity",
                        metadata={
                            "bid_depth": bid_depth,
                            "ask_depth": ask_depth,
                            "spread": spread,
                            "mid_price": order_book.mid_price,
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"Order book signal failed for {token_id}: {e}")

        return signals


class SignalSourceManager:
    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}

    def register(self, source: DataSource) -> None:
        self._sources[source.name] = source
        logger.info(f"Registered data source: {source.name}")

    async def fetch_all(
        self, market_id: str | None = None, **kwargs: Any
    ) -> dict[str, list[SourceSignal]]:
        results = {}
        for name, source in self._sources.items():
            try:
                signals = await source.fetch_signals(market_id, **kwargs)
                if signals:
                    results[name] = signals
            except Exception as e:
                logger.warning(f"Source {name} failed: {e}")
        return results

    async def close(self) -> None:
        for source in self._sources.values():
            await source.close()

    def to_dict(self) -> dict[str, Any]:
        return {name: source.to_dict() for name, source in self._sources.items()}
