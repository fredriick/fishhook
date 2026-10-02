"""Tests for the web dashboard API endpoints."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from fishhook.config.settings import PipelineConfig
from fishhook.dashboard.server import DashboardServer
from fishhook.orchestrator import PipelineOrchestrator


@pytest.fixture
def dserver(tmp_path):
    config = PipelineConfig(data_dir=tmp_path)
    orchestrator = PipelineOrchestrator(config)
    return DashboardServer(orchestrator)


async def _client(server: DashboardServer) -> TestClient:
    client = TestClient(TestServer(server._build_app()))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_config_endpoint_returns_version_and_redacts_secrets(dserver) -> None:
    config = dserver._orchestrator._config
    config.polymarket.api_key = "super-secret"

    client = await _client(dserver)
    try:
        resp = await client.get("/api/config")
        assert resp.status == 200
        data = await resp.json()

        assert data["config_version"] == config.fingerprint()
        assert data["config_tag"] == config.config_tag()
        assert data["config"]["polymarket"]["api_key"] == "***"

        raw = await resp.text()
        assert "super-secret" not in raw
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_status_endpoint_includes_meta(dserver) -> None:
    client = await _client(dserver)
    try:
        resp = await client.get("/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["config_version"]
        assert "data_sources" in data
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_halt_and_resume_toggle_breaker(dserver) -> None:
    client = await _client(dserver)
    try:
        resp = await client.post("/api/halt", json={"reason": "api test"})
        assert resp.status == 200
        status = await resp.json()
        assert status["state"] == "open"
        assert status["trading_allowed"] is False

        resp = await client.post("/api/resume", json={})
        assert resp.status == 200
        status = await resp.json()
        assert status["state"] == "closed"
        assert status["trading_allowed"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_run_endpoint_uses_orchestrator(dserver) -> None:
    @dataclass
    class _FakeRun:
        run_id: int = 7
        markets_analyzed: int = 3
        signals_generated: int = 2
        trades_executed: int = 1
        errors: list = field(default_factory=list)

        def to_dict(self) -> dict[str, Any]:
            return {
                "run_id": self.run_id,
                "markets_analyzed": self.markets_analyzed,
                "signals_generated": self.signals_generated,
                "trades_executed": self.trades_executed,
                "errors": self.errors,
            }

    async def fake_run_once(**kwargs) -> _FakeRun:
        return _FakeRun()

    dserver._orchestrator.run_once = fake_run_once

    client = await _client(dserver)
    try:
        resp = await client.post(
            "/api/run", json={"markets": 5, "category": "crypto"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["run_id"] == 7
        assert data["markets_analyzed"] == 3
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_scrape_requires_urls(dserver) -> None:
    client = await _client(dserver)
    try:
        resp = await client.post("/api/scrape", json={})
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_scrape_endpoint_calls_orchestrator(dserver) -> None:
    async def fake_scrape(urls: list[str]) -> dict[str, Any]:
        return {u: {"html_length": 1} for u in urls}

    class _FakeScraper:
        running = False

        async def start(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.running = False

    orch = dserver._orchestrator
    orch.scrape_and_cache = fake_scrape
    orch._scraper = _FakeScraper()

    client = await _client(dserver)
    try:
        resp = await client.post(
            "/api/scrape", json={"urls": ["https://example.com"]}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["results"]["https://example.com"]["html_length"] == 1
    finally:
        await client.close()