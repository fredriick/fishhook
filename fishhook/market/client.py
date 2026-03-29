"""Polymarket API client - interfaces with CLOB and Gamma APIs."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from fishhook.config.settings import PolymarketConfig
from fishhook.market.models import Market, OrderBook
from fishhook.utils.logging import get_logger

logger = get_logger("market.client")


class PolymarketClient:
    def __init__(self, config: PolymarketConfig | None = None) -> None:
        self._config = config or PolymarketConfig()
        self._client: httpx.AsyncClient | None = None
        self._session_token: str | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if self._config.api_key:
                headers["Authorization"] = f"Bearer {self._config.api_key}"
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _sign_request(
        self, timestamp: str, method: str, path: str, body: str = ""
    ) -> str:
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self._config.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return signature

    async def get_markets(
        self,
        limit: int = 50,
        active: bool = True,
        category: str | None = None,
    ) -> list[Market]:
        client = await self._get_client()
        params: dict[str, Any] = {"limit": limit}
        if active:
            params["active"] = True
        if category:
            params["tag"] = category

        try:
            resp = await client.get(
                f"{self._config.gamma_api_url}/markets",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            markets = [Market.from_gamma_api(m) for m in data]
            logger.info(f"Fetched {len(markets)} markets from Gamma API")
            return markets
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    async def get_market(self, market_id: str) -> Market | None:
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self._config.gamma_api_url}/markets",
                params={"id": market_id},
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                return Market.from_gamma_api(data[0])
            return None
        except Exception as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    async def get_order_book(self, token_id: str) -> OrderBook | None:
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self._config.api_base_url}/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return OrderBook.from_clob_api(data)
        except Exception as e:
            logger.error(f"Failed to fetch order book for {token_id}: {e}")
            return None

    async def get_prices(self, token_ids: list[str]) -> dict[str, dict[str, float]]:
        client = await self._get_client()
        prices = {}
        for token_id in token_ids:
            try:
                resp = await client.get(
                    f"{self._config.api_base_url}/price",
                    params={"token_id": token_id},
                )
                resp.raise_for_status()
                data = resp.json()
                prices[token_id] = {
                    "bid": float(data.get("bid", 0)),
                    "ask": float(data.get("ask", 0)),
                    "mid": float(data.get("mid", 0)),
                }
            except Exception as e:
                logger.warning(f"Failed to fetch price for {token_id}: {e}")
        return prices

    async def get_trades(
        self,
        market_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        params: dict[str, Any] = {"limit": limit}
        if market_id:
            params["market"] = market_id

        try:
            resp = await client.get(
                f"{self._config.api_base_url}/trades",
                params=params,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "LIMIT",
    ) -> dict[str, Any] | None:
        if self._config.testnet:
            logger.info(
                f"[TESTNET] Would place {side} order: {size} @ ${price} on {token_id}"
            )
            return {
                "orderId": f"test_{int(time.time())}",
                "status": "testnet",
                "side": side,
                "price": price,
                "size": size,
            }

        client = await self._get_client()
        timestamp = str(int(time.time()))
        path = "/order"
        body = json.dumps(
            {
                "token_id": token_id,
                "side": side,
                "price": str(price),
                "size": str(size),
                "order_type": order_type,
            }
        )

        signature = self._sign_request(timestamp, "POST", path, body)

        try:
            resp = await client.post(
                f"{self._config.api_base_url}{path}",
                content=body,
                headers={
                    "POLYMARKET-SIGNATURE": signature,
                    "POLYMARKET-TIMESTAMP": timestamp,
                    "POLYMARKET-API-KEY": self._config.api_key,
                    "POLYMARKET-PASSPHRASE": self._config.passphrase,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Order placed: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        if self._config.testnet:
            logger.info(f"[TESTNET] Would cancel order {order_id}")
            return True

        client = await self._get_client()
        timestamp = str(int(time.time()))
        path = f"/order/{order_id}"
        signature = self._sign_request(timestamp, "DELETE", path)

        try:
            resp = await client.delete(
                f"{self._config.api_base_url}{path}",
                headers={
                    "POLYMARKET-SIGNATURE": signature,
                    "POLYMARKET-TIMESTAMP": timestamp,
                    "POLYMARKET-API-KEY": self._config.api_key,
                    "POLYMARKET-PASSPHRASE": self._config.passphrase,
                },
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_active_orders(self) -> list[dict[str, Any]]:
        if self._config.testnet:
            return []

        client = await self._get_client()
        timestamp = str(int(time.time()))
        path = "/orders"
        signature = self._sign_request(timestamp, "GET", path)

        try:
            resp = await client.get(
                f"{self._config.api_base_url}{path}",
                headers={
                    "POLYMARKET-SIGNATURE": signature,
                    "POLYMARKET-TIMESTAMP": timestamp,
                    "POLYMARKET-API-KEY": self._config.api_key,
                    "POLYMARKET-PASSPHRASE": self._config.passphrase,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch active orders: {e}")
            return []
