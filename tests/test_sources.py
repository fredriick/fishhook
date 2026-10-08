"""Tests for the structured API sources and the signal source manager."""

import os

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


def _nansen_payload(rows: list[dict]) -> dict:
    return {"pagination": {"page": 1, "per_page": 20, "is_last_page": True}, "data": rows}


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
        return _nansen_payload(
            [
                {
                    "method": "transfer(address,uint256)",
                    "tokens_received": [
                        {"token_symbol": "USDC", "token_amount": 900.0, "value_usd": 900.0}
                    ],
                    "tokens_sent": [],
                    "volume_usd": 900.0,
                },
                {
                    "method": "transfer(address,uint256)",
                    "tokens_received": [],
                    "tokens_sent": [
                        {"token_symbol": "USDC", "token_amount": 100.0, "value_usd": 100.0}
                    ],
                    "volume_usd": 100.0,
                },
                {
                    "method": "approve(address,uint256)",
                    "tokens_received": [],
                    "tokens_sent": [
                        {"token_symbol": "USDC", "token_amount": 100.0, "value_usd": 100.0}
                    ],
                    "volume_usd": 100.0,
                },
            ]
        )

    monkeypatch.setattr(source, "_fetch_token_activity", fake_fetch)
    signals = await source.fetch_signals(token_addresses=["0xabc"])

    assert len(signals) == 1
    assert signals[0].source_name == "nansen"
    assert signals[0].category == "on_chain"
    assert 0.0 < signals[0].value < 1.0
    assert signals[0].metadata["transactions"] == 3
    assert signals[0].metadata["received_value"] == 900.0
    assert signals[0].metadata["sent_value"] == 200.0
    assert signals[0].metadata["address"] == "0xabc"
    assert signals[0].metadata["basis"] == "usd"


@pytest.mark.asyncio
async def test_nansen_signal_none_for_empty_activity(monkeypatch) -> None:
    source = NansenQuery(api_key="key")

    async def empty_fetch(address: str, chain: str, days: int) -> dict:
        return _nansen_payload([])

    monkeypatch.setattr(source, "_fetch_token_activity", empty_fetch)
    assert await source.fetch_signals(token_addresses=["0xabc"]) == []


@pytest.mark.asyncio
async def test_nansen_parses_real_api_payload(monkeypatch) -> None:
    """Shape captured from the live API: raw contract methods, null USD fields."""
    source = NansenQuery(api_key="key")

    async def real_fetch(address: str, chain: str, days: int) -> dict:
        return _nansen_payload(
            [
                {
                    "chain": "ethereum",
                    "method": "depositWithAuthorization(address,...)",
                    "tokens_sent": [
                        {
                            "token_symbol": "",
                            "token_amount": -3.958331e-8,
                            "price_usd": None,
                            "value_usd": None,
                            "token_address": "0x03743372098aa51e1fce537d51025f08b55c4144",
                            "chain": "ethereum",
                            "from_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                            "to_address": "0x0f7ae28de1c8532170ad4ee566b5801485c13a0e",
                        }
                    ],
                    "tokens_received": [],
                    "volume_usd": None,
                    "block_timestamp": "2026-10-07T21:39:47",
                    "transaction_hash": "0xc4bcb280d4948ac21ed72ac921a83598d8343fbe49c40ed3930f0b29134699e4",
                    "source_type": "transfer",
                },
                {
                    "chain": "ethereum",
                    "method": "execute302((address,(uint32,bytes32,uint64),bytes32,bytes,bytes,uint256))",
                    "tokens_sent": [],
                    "tokens_received": [
                        {
                            "token_symbol": "EBTC",
                            "token_amount": 0.011326,
                            "price_usd": None,
                            "value_usd": None,
                            "token_address": "0x657e8c867d8b37dcc18fa4caead9c45eb088c642",
                            "chain": "ethereum",
                            "from_address": "0x0000000000000000000000000000000000000000",
                            "to_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        }
                    ],
                    "volume_usd": 950.3094201949207,
                    "block_timestamp": "2026-10-07T08:22:11",
                    "transaction_hash": "0xa1292ca668647fd9fef9d6c08388a9138f5a7d23f9197a1f1f4a69e7bcf985b6",
                    "source_type": "transfer",
                },
            ]
        )

    monkeypatch.setattr(source, "_fetch_token_activity", real_fetch)
    signals = await source.fetch_signals(token_addresses=["0xa0b86991"])

    assert len(signals) == 1
    signal = signals[0]
    assert signal.value == 1.0
    assert signal.metadata["transactions"] == 2
    assert signal.metadata["received_value"] == 950.30942
    assert signal.metadata["sent_value"] == 0.0
    assert signal.metadata["sent_count"] == 1
    assert signal.metadata["received_count"] == 1
    assert signal.metadata["basis"] == "usd"


@pytest.mark.asyncio
async def test_nansen_falls_back_to_transfer_counts(monkeypatch) -> None:
    """Unpriced rows still yield a directional signal from transfer counts."""
    source = NansenQuery(api_key="key")

    async def unpriced_fetch(address: str, chain: str, days: int) -> dict:
        return _nansen_payload(
            [
                {"method": "transfer", "tokens_received": [{"token_symbol": "X"}], "tokens_sent": []},
                {"method": "transfer", "tokens_received": [{"token_symbol": "X"}], "tokens_sent": []},
                {"method": "transfer", "tokens_received": [], "tokens_sent": [{"token_symbol": "X"}]},
            ]
        )

    monkeypatch.setattr(source, "_fetch_token_activity", unpriced_fetch)
    signals = await source.fetch_signals(token_addresses=["0xabc"])

    assert len(signals) == 1
    assert signals[0].value == pytest.approx(1 / 3)
    assert signals[0].metadata["basis"] == "count"


@pytest.mark.asyncio
async def test_nansen_signal_none_when_direction_unknown(monkeypatch) -> None:
    source = NansenQuery(api_key="key")

    async def unknown_fetch(address: str, chain: str, days: int) -> dict:
        return _nansen_payload(
            [{"method": "multicall", "tokens_received": [], "tokens_sent": [], "volume_usd": None}]
        )

    monkeypatch.setattr(source, "_fetch_token_activity", unknown_fetch)
    assert await source.fetch_signals(token_addresses=["0xabc"]) == []


@pytest.mark.asyncio
async def test_nansen_posts_real_endpoint_with_contract_body(monkeypatch) -> None:
    source = NansenQuery(api_key="key", page_size=500)
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return _nansen_payload([])

    async def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(source, "_rate_limited_post", fake_post)
    await source.fetch_signals(token_addresses=["0xdeadbeef"])

    assert captured["url"].endswith("/api/v1/profiler/address/transactions")
    body = captured["json"]
    assert body["address"] == "0xdeadbeef"
    assert body["chain"] == "ethereum"
    assert body["hide_spam_token"] is True
    assert set(body["date"]) == {"from", "to"}
    assert body["pagination"] == {"page": 1, "per_page": 100}


@pytest.mark.asyncio
async def test_nansen_uses_apikey_header() -> None:
    source = NansenQuery(api_key="kys", base_url="https://example.test")

    client = await source._get_client()

    assert client.headers["apikey"] == "kys"
    assert "Authorization" not in client.headers
    await source.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("NANSEN_LIVE_TEST_KEY"),
    reason="NANSEN_LIVE_TEST_KEY not set; live Nansen integration test skipped",
)
async def test_nansen_live_integration() -> None:
    """Opt-in live test against the real API (never runs in CI)."""
    source = NansenQuery(api_key=os.environ["NANSEN_LIVE_TEST_KEY"])
    try:
        signals = await source.fetch_signals(
            token_addresses=["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]
        )
    finally:
        await source.close()

    assert len(signals) == 1
    signal = signals[0]
    assert -1.0 <= signal.value <= 1.0
    assert 0.0 < signal.confidence <= 1.0
    assert signal.metadata["transactions"] >= 1
    assert signal.metadata["basis"] in {"usd", "count"}


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