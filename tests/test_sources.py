"""Tests for the structured API sources and the signal source manager."""

import pytest

from fishhook.ingestion.sources import (
    DataSource,
    DuneAnalytics,
    NansenQuery,
    SignalSourceManager,
    SourceSignal,
)


class _FakeSource(DataSource):
    def __init__(self, name: str, value: float, boom: bool = False) -> None:
        super().__init__(name=name)
        self._value = value
        self._boom = boom

    async def fetch_signals(
        self, market_id: str | None = None, **kwargs
    ) -> list[SourceSignal]:
        if self._boom:
            raise RuntimeError("source failure")
        return [
            SourceSignal(
                value=self._value,
                confidence=0.8,
                source_name=self.name,
                category="test",
            )
        ]


@pytest.mark.asyncio
async def test_manager_aggregates_sources() -> None:
    manager = SignalSourceManager()
    manager.register(_FakeSource("alpha", value=0.5))
    manager.register(_FakeSource("beta", value=-0.4))

    results = await manager.fetch_all(market_id="m1")

    assert set(results.keys()) == {"alpha", "beta"}
    assert results["alpha"][0].value == 0.5
    assert results["beta"][0].value == -0.4


@pytest.mark.asyncio
async def test_manager_isolates_source_failures() -> None:
    manager = SignalSourceManager()
    manager.register(_FakeSource("good", value=0.2))
    manager.register(_FakeSource("bad", value=0.0, boom=True))

    results = await manager.fetch_all(market_id="m1")

    assert "good" in results
    assert "bad" not in results


@pytest.mark.asyncio
async def test_duplicate_source_name_is_overridden() -> None:
    manager = SignalSourceManager()
    manager.register(_FakeSource("dup", value=0.1))
    manager.register(_FakeSource("dup", value=0.9))

    results = await manager.fetch_all(market_id="m1")
    assert results["dup"][0].value == 0.9


@pytest.mark.asyncio
async def test_dune_fetch_uses_row_signals(monkeypatch) -> None:
    dune = DuneAnalytics(api_key="key", query_ids=[1])

    async def fake_query(query_id: int) -> dict:
        return {
            "rows": [
                {"signal_value": 0.3, "confidence": 0.7, "category": "on_chain"},
                {"signal_value": -0.2, "confidence": 0.5},
            ]
        }

    monkeypatch.setattr(dune, "execute_query", fake_query)
    signals = await dune.fetch_signals()

    assert len(signals) == 2
    assert signals[0].value == 0.3
    assert signals[0].source_name == "dune"
    assert signals[0].category == "on_chain"
    assert signals[1].value == -0.2


@pytest.mark.asyncio
async def test_dune_skipped_without_api_key() -> None:
    dune = DuneAnalytics(api_key="", query_ids=[1])
    assert await dune.fetch_signals() == []


# --- Nansen ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_nansen_skipped_without_api_key() -> None:
    source = NansenQuery(api_key="")
    assert await source.fetch_signals(token_addresses=["0xabc"]) == []


@pytest.mark.asyncio
async def test_nansen_inert_without_addresses() -> None:
    source = NansenQuery(api_key="key")
    assert await source.fetch_signals(market_id="m1") == []


@pytest.mark.asyncio
async def test_nansen_fetch_uses_activity_rows(monkeypatch) -> None:
    source = NansenQuery(api_key="key")

    async def fake_fetch(address: str, chain: str, days: int) -> dict:
        return {
            "items": [
                {"tx_type": "received", "value": 900.0},
                {"tx_type": "sent", "value": 100.0},
                {"tx_type": "sent", "value": 100.0},
            ]
        }

    monkeypatch.setattr(source, "_fetch_token_activity", fake_fetch)
    signals = await source.fetch_signals(token_addresses=["0xabc"])

    assert len(signals) == 1
    assert signals[0].source_name == "nansen"
    assert signals[0].category == "on_chain"
    assert 0.0 < signals[0].value < 1.0
    assert signals[0].metadata["transactions"] == 3
    assert signals[0].metadata["received_value"] == 900.0
    assert signals[0].metadata["address"] == "0xabc"


@pytest.mark.asyncio
async def test_nansen_signal_none_for_empty_activity(monkeypatch) -> None:
    source = NansenQuery(api_key="key")

    async def empty_fetch(address: str, chain: str, days: int) -> dict:
        return {"items": []}

    monkeypatch.setattr(source, "_fetch_token_activity", empty_fetch)
    assert await source.fetch_signals(token_addresses=["0xabc"]) == []


@pytest.mark.asyncio
async def test_nansen_uses_x_api_key_header() -> None:
    source = NansenQuery(api_key="kys", base_url="https://example.test")

    client = await source._get_client()

    assert client.headers["X-API-Key"] == "kys"
    assert "Authorization" not in client.headers
    await source.close()


@pytest.mark.asyncio
async def test_strategy_consumes_all_registered_sources() -> None:
    from fishhook.config.settings import StrategyConfig
    from fishhook.market.models import Market, MarketStatus
    from fishhook.strategy.engine import StrategyEngine

    manager = SignalSourceManager()
    manager.register(_FakeSource("orderbook", value=0.5))
    manager.register(_FakeSource("dune", value=-0.3))

    engine = StrategyEngine(
        config=StrategyConfig(cooldown_seconds=0), source_manager=manager
    )
    market = Market(
        id="m1",
        question="Will X happen?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.55, 0.45],
        volume=100.0,
        liquidity=10.0,
        status=MarketStatus.ACTIVE,
    )

    with_sources = await engine._compute_market_signal(market, None)
    without = await StrategyEngine(
        config=StrategyConfig()
    )._compute_market_signal(market, None)

    assert with_sources != without
    assert -1.0 <= with_sources <= 1.0