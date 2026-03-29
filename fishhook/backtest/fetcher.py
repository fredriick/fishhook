"""Fetches historical/resolved Polymarket data for backtesting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from fishhook.config.settings import PolymarketConfig
from fishhook.utils.logging import get_logger

logger = get_logger("backtest.fetcher")


@dataclass
class ResolvedMarket:
    id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    resolution_price: float
    resolved_outcome: str
    volume: float
    liquidity: float
    end_date: datetime | None
    category: str
    slug: str
    condition_id: str

    @property
    def was_yes_winner(self) -> bool:
        if len(self.outcome_prices) >= 2:
            return self.outcome_prices[0] > self.outcome_prices[1]
        return self.resolution_price > 0.5

    @property
    def closing_price(self) -> float:
        return self.outcome_prices[0] if self.outcome_prices else 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "outcomes": self.outcomes,
            "prices": self.outcome_prices,
            "resolution_price": self.resolution_price,
            "resolved": self.resolved_outcome,
            "volume": self.volume,
            "closing_price": self.closing_price,
            "was_yes": self.was_yes_winner,
        }


class HistoricalDataFetcher:
    def __init__(self, config: PolymarketConfig | None = None) -> None:
        self._config = config or PolymarketConfig()
        self._cache_dir = Path("fishhook/data/backtest_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_resolved_markets(
        self,
        limit: int = 100,
        category: str | None = None,
        min_volume: float = 1000.0,
    ) -> list[ResolvedMarket]:
        cache_file = self._cache_dir / f"resolved_{limit}_{category or 'all'}.json"
        if cache_file.exists():
            age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hours < 24:
                logger.info(f"Loading resolved markets from cache: {cache_file}")
                return self._load_from_cache(cache_file)

        markets = await self._fetch_from_api(limit, category, min_volume)
        self._save_to_cache(markets, cache_file)
        return markets

    async def fetch_recent_markets(
        self,
        limit: int = 50,
        category: str | None = None,
        min_volume: float = 1000.0,
        include_closed: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch recent markets with live price data for signal validation."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            params: dict[str, Any] = {
                "limit": limit,
                "order": "volume",
                "ascending": False,
            }
            if category:
                params["tag"] = category
            if include_closed:
                params["closed"] = True

            try:
                resp = await client.get(
                    f"{self._config.gamma_api_url}/markets",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                results = []
                for item in data:
                    volume = float(item.get("volume", 0))
                    if volume < min_volume:
                        continue

                    outcomes = item.get("outcomes", [])
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)

                    prices = item.get("outcomePrices", [])
                    if isinstance(prices, str):
                        prices = [float(p) for p in json.loads(prices)]
                    elif isinstance(prices, list):
                        prices = [float(p) for p in prices]

                    last_trade = float(item.get("lastTradePrice", 0))
                    one_day_change = float(item.get("oneDayPriceChange", 0))
                    one_hour_change = float(item.get("oneHourPriceChange", 0))
                    spread = float(item.get("spread", 0))
                    is_closed = item.get("closed", False)

                    results.append(
                        {
                            "id": item.get("id", ""),
                            "question": item.get("question", ""),
                            "outcomes": outcomes,
                            "prices": prices,
                            "last_trade_price": last_trade,
                            "one_day_change": one_day_change,
                            "one_hour_change": one_hour_change,
                            "spread": spread,
                            "volume": volume,
                            "liquidity": float(item.get("liquidity", 0)),
                            "closed": is_closed,
                            "end_date": item.get("endDate"),
                        }
                    )

                logger.info(f"Fetched {len(results)} markets with price data")
                return results

            except Exception as e:
                logger.error(f"Failed to fetch markets: {e}")
                return []

    async def _fetch_from_api(
        self,
        limit: int,
        category: str | None,
        min_volume: float,
    ) -> list[ResolvedMarket]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            all_markets = []
            offset = 0
            batch_size = min(limit, 100)

            while len(all_markets) < limit:
                params: dict[str, Any] = {
                    "limit": batch_size,
                    "offset": offset,
                    "closed": True,
                    "order": "volume",
                    "ascending": False,
                }
                if category:
                    params["tag"] = category

                try:
                    resp = await client.get(
                        f"{self._config.gamma_api_url}/markets",
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    if not data:
                        break

                    for item in data:
                        market = self._parse_market(item, min_volume)
                        if market:
                            all_markets.append(market)

                    offset += batch_size
                    logger.info(
                        f"Fetched {len(all_markets)} resolved markets so far..."
                    )

                except Exception as e:
                    logger.error(f"Failed to fetch markets: {e}")
                    break

            logger.info(f"Total resolved markets fetched: {len(all_markets)}")
            return all_markets[:limit]

    def _parse_market(
        self, data: dict[str, Any], min_volume: float
    ) -> ResolvedMarket | None:
        try:
            volume = float(data.get("volume", 0))
            if volume < min_volume:
                return None

            outcomes = []
            raw_outcomes = data.get("outcomes")
            if isinstance(raw_outcomes, str):
                outcomes = json.loads(raw_outcomes)
            elif isinstance(raw_outcomes, list):
                outcomes = raw_outcomes

            prices = []
            raw_prices = data.get("outcomePrices")
            if isinstance(raw_prices, str):
                prices = [float(p) for p in json.loads(raw_prices)]
            elif isinstance(raw_prices, list):
                prices = [float(p) for p in raw_prices]

            if len(prices) < 2:
                return None

            end_date = None
            if data.get("endDate"):
                try:
                    end_date = datetime.fromisoformat(
                        data["endDate"].replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass

            # Determine resolution: highest price = winning outcome
            resolution_price = max(prices)
            resolved_idx = prices.index(resolution_price)
            resolved_outcome = (
                outcomes[resolved_idx] if resolved_idx < len(outcomes) else "unknown"
            )

            return ResolvedMarket(
                id=data.get("id", ""),
                question=data.get("question", ""),
                outcomes=outcomes,
                outcome_prices=prices,
                resolution_price=resolution_price,
                resolved_outcome=resolved_outcome,
                volume=volume,
                liquidity=float(data.get("liquidity", 0)),
                end_date=end_date,
                category=data.get("category", ""),
                slug=data.get("slug", ""),
                condition_id=data.get("conditionId", ""),
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.debug(f"Skipping market: {e}")
            return None

    def _save_to_cache(self, markets: list[ResolvedMarket], path: Path) -> None:
        data = [m.to_dict() for m in markets]
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Cached {len(markets)} markets to {path}")

    def _load_from_cache(self, path: Path) -> list[ResolvedMarket]:
        with open(path) as f:
            data = json.load(f)
        markets = []
        for item in data:
            markets.append(
                ResolvedMarket(
                    id=item["id"],
                    question=item["question"],
                    outcomes=item["outcomes"],
                    outcome_prices=item["prices"],
                    resolution_price=item["resolution_price"],
                    resolved_outcome=item["resolved"],
                    volume=item["volume"],
                    liquidity=0,
                    end_date=None,
                    category="",
                    slug="",
                    condition_id="",
                )
            )
        return markets

    async def fetch_with_price_history(
        self,
        market_id: str,
    ) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(
                    f"{self._config.gamma_api_url}/markets",
                    params={"id": market_id},
                )
                resp.raise_for_status()
                data = resp.json()
                if data:
                    return data[0]
                return None
            except Exception as e:
                logger.error(f"Failed to fetch market {market_id}: {e}")
                return None
