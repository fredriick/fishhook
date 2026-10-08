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
    def __init__(
        self,
        name: str,
        api_key: str = "",
        base_url: str = "",
        auth_header: str | None = None,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._base_url = base_url
        self._auth_header = auth_header
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0
        self._min_interval: float = 1.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/json"}
            if self._api_key:
                if self._auth_header:
                    headers[self._auth_header] = self._api_key
                else:
                    headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                timeout=30.0, headers=headers, follow_redirects=True
            )
        return self._client

    async def _wait_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            import asyncio

            await asyncio.sleep(self._min_interval - elapsed)

    async def _rate_limited_get(self, url: str, **kwargs: Any) -> httpx.Response:
        await self._wait_rate_limit()
        client = await self._get_client()
        self._last_request_time = time.time()
        return await client.get(url, **kwargs)

    async def _rate_limited_post(self, url: str, **kwargs: Any) -> httpx.Response:
        await self._wait_rate_limit()
        client = await self._get_client()
        self._last_request_time = time.time()
        return await client.post(url, **kwargs)

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
            auth_header="X-Dune-API-Key",
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


class NansenQuery(DataSource):
    """Structured on-chain intelligence from the Nansen Query API.

    Uses ``POST /api/v1/profiler/address/transactions`` with the ``apikey``
    header for authentication (Nansen does not use Bearer tokens). The source
    is inert until a real ``api_key`` is configured, so tests and CI can run
    without credentials.
    """

    _ENDPOINT = "/api/v1/profiler/address/transactions"
    _MAX_PAGE_SIZE = 100
    _MAX_RETRY_AFTER = 30

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.nansen.ai",
        chain: str = "ethereum",
        window_days: int = 7,
        page_size: int = 100,
        category: str = "on_chain",
    ) -> None:
        super().__init__(
            name="nansen",
            api_key=api_key,
            base_url=base_url,
            auth_header="apikey",
        )
        self._chain = chain
        self._window_days = window_days
        self._page_size = page_size
        self._category = category
        self._min_interval = 1.5

    def _request_body(self, address: str, chain: str, days: int) -> dict[str, Any]:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(1, days))
        per_page = max(1, min(self._page_size, self._MAX_PAGE_SIZE))
        return {
            "address": address,
            "chain": chain,
            "date": {
                "from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "pagination": {"page": 1, "per_page": per_page},
            "hide_spam_token": True,
        }

    async def _fetch_token_activity(
        self, address: str, chain: str, days: int
    ) -> dict[str, Any] | None:
        """Fetch recent transactions for an address from the profiler endpoint."""
        if not self._api_key:
            return None
        url = f"{self._base_url}{self._ENDPOINT}"
        body = self._request_body(address, chain, days)
        try:
            resp = await self._rate_limited_post(url, json=body)
            if resp.status_code == 429:
                import asyncio

                retry_after = resp.headers.get("Retry-After", "")
                try:
                    delay = min(float(retry_after), float(self._MAX_RETRY_AFTER))
                except ValueError:
                    delay = float(self._MAX_RETRY_AFTER)
                logger.debug(f"Nansen rate limited, retrying in {delay}s")
                await asyncio.sleep(delay)
                resp = await self._rate_limited_post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                logger.debug(f"Nansen activity response for {address} unexpected shape")
                return None
            return data
        except Exception as e:
            logger.warning(f"Nansen query for {address} failed: {e}")
            return None

    async def fetch_signals(
        self, market_id: str | None = None, **kwargs: Any
    ) -> list[SourceSignal]:
        signals = []
        if not self._api_key:
            logger.debug("Nansen API key not configured, skipping")
            return signals

        addresses = kwargs.get("token_addresses") or kwargs.get("addresses") or []
        if not addresses:
            return signals

        chain = str(kwargs.get("chain", self._chain))
        days = int(kwargs.get("window_days", self._window_days))

        for address in addresses:
            data = await self._fetch_token_activity(address, chain, days)
            signal = self._activity_to_signal(address, data)
            if signal is not None:
                signals.append(signal)
        return signals

    @staticmethod
    def _token_list_value(tokens: Any) -> tuple[float, int]:
        """USD value and entry count for a ``tokens_sent``/``tokens_received`` list."""
        total = 0.0
        count = 0
        if not isinstance(tokens, list):
            return total, count
        for token in tokens:
            if not isinstance(token, dict):
                continue
            count += 1
            value = token.get("value_usd")
            if not isinstance(value, (int, float)):
                price = token.get("price_usd")
                amount = token.get("token_amount")
                if isinstance(price, (int, float)) and isinstance(amount, (int, float)):
                    value = abs(float(amount)) * float(price)
            if isinstance(value, (int, float)):
                total += abs(float(value))
        return total, count

    def _activity_to_signal(
        self, address: str, data: dict[str, Any] | None
    ) -> SourceSignal | None:
        if not isinstance(data, dict):
            return None

        rows = data.get("data")
        if not isinstance(rows, list):
            return None

        received_value = 0.0
        sent_value = 0.0
        received_count = 0
        sent_count = 0
        tx_count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            tx_count += 1

            recv_usd, recv_n = self._token_list_value(row.get("tokens_received"))
            sent_usd, sent_n = self._token_list_value(row.get("tokens_sent"))
            if recv_usd <= 0 and sent_usd <= 0:
                # No priced transfers: fall back to the row-level USD volume,
                # which belongs to whichever side of the transfer is populated.
                volume = row.get("volume_usd")
                if isinstance(volume, (int, float)) and volume > 0:
                    if recv_n and not sent_n:
                        recv_usd = float(volume)
                    elif sent_n and not recv_n:
                        sent_usd = float(volume)

            received_value += recv_usd
            sent_value += sent_usd
            if recv_n:
                received_count += 1
            if sent_n:
                sent_count += 1

        if received_value + sent_value > 0:
            recv, sent = received_value, sent_value
            basis = "usd"
        else:
            # No priced flow at all: net direction of transfer counts.
            recv, sent = float(received_count), float(sent_count)
            basis = "count"

        total = recv + sent
        if tx_count == 0 or total <= 0:
            return None

        net_flow = (recv - sent) / total
        confidence = min(1.0, 0.3 + (tx_count / 50.0))

        return SourceSignal(
            value=max(-1.0, min(1.0, net_flow)),
            confidence=confidence,
            source_name="nansen",
            category=self._category,
            metadata={
                "address": address,
                "chain": self._chain,
                "received_value": round(received_value, 6),
                "sent_value": round(sent_value, 6),
                "received_count": received_count,
                "sent_count": sent_count,
                "transactions": tx_count,
                "window_days": self._window_days,
                "basis": basis,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["chain"] = self._chain
        d["window_days"] = self._window_days
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
