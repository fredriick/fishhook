"""Core scraping engine - orchestrates browser automation with request interception."""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fishhook.config.settings import ScraperConfig
from fishhook.ingestion.dynamic_values import DynamicValueExtractor
from fishhook.ingestion.interceptor import InterceptedRequest, RequestInterceptor
from fishhook.ingestion.proxy_manager import ProxyManager
from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.engine")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


@dataclass
class ScrapeResult:
    url: str
    status_code: int
    html: str
    intercepted_requests: list[InterceptedRequest] = field(default_factory=list)
    api_responses: list[dict[str, Any]] = field(default_factory=list)
    dynamic_tokens: dict[str, str] = field(default_factory=dict)
    timing_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "html_length": len(self.html),
            "intercepted_count": len(self.intercepted_requests),
            "api_responses_count": len(self.api_responses),
            "dynamic_tokens": self.dynamic_tokens,
            "timing_ms": self.timing_ms,
            "metadata": self.metadata,
        }


class ScrapingEngine:
    def __init__(self, config: ScraperConfig | None = None) -> None:
        self._config = config or ScraperConfig()
        self._interceptor = RequestInterceptor()
        self._dynamic_extractor = DynamicValueExtractor()
        self._proxy_manager = ProxyManager(self._config.proxy)
        self._playwright = None
        self._browser = None
        self._user_agent_cycle = iter(itertools.cycle(USER_AGENTS))

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            proxy = (
                self._proxy_manager.get_proxy_playwright()
                if self._proxy_manager.is_enabled
                else None
            )
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
            self._browser = await self._playwright.chromium.launch(
                headless=self._config.headless,
                proxy=proxy,
                args=launch_args,
            )
            logger.info("Scraping engine started")
        except Exception as e:
            logger.error(f"Failed to start scraping engine: {e}")
            raise

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Scraping engine stopped")

    async def scrape(
        self,
        url: str,
        wait_for: str | None = None,
        actions: list[Callable] | None = None,
        extract_api: bool = True,
    ) -> ScrapeResult:
        return await self._do_scrape(url, wait_for, actions, extract_api)

    async def _do_scrape(
        self,
        url: str,
        wait_for: str | None,
        actions: list[Callable] | None,
        extract_api: bool,
    ) -> ScrapeResult:
        start = time.time()
        self._interceptor.clear()

        ua = (
            next(self._user_agent_cycle)
            if self._config.user_agent_rotation
            else USER_AGENTS[0]
        )

        context = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            java_script_enabled=True,
            bypass_csp=True,
        )

        page = await context.new_page()

        if self._config.intercept_requests:
            page.on(
                "request",
                lambda req: asyncio.ensure_future(self._interceptor.on_request(req)),
            )
            page.on(
                "response",
                lambda resp: asyncio.ensure_future(self._interceptor.on_response(resp)),
            )

        try:
            response = await page.goto(
                url, wait_until="networkidle", timeout=self._config.timeout_ms
            )
            status = response.status if response else 0

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=10000)
                except Exception:
                    pass

            if actions:
                for action in actions:
                    try:
                        await action(page)
                    except Exception as e:
                        logger.warning(f"Action failed: {e}")

            html = await page.content()

            if self._config.capture_dynamic_values:
                self._dynamic_extractor.extract_from_html(html, url)

            api_responses = []
            if extract_api:
                for req in self._interceptor.api_requests:
                    if req.response_body:
                        try:
                            data = json.loads(req.response_body)
                            api_responses.append(
                                {
                                    "url": req.url,
                                    "method": req.method,
                                    "data": data,
                                    "status": req.response_status,
                                }
                            )
                        except (json.JSONDecodeError, TypeError):
                            pass

            elapsed = (time.time() - start) * 1000

            result = ScrapeResult(
                url=url,
                status_code=status,
                html=html,
                intercepted_requests=self._interceptor.captured_requests,
                api_responses=api_responses,
                dynamic_tokens=self._dynamic_extractor.active_headers,
                timing_ms=elapsed,
                metadata={
                    "interceptor_summary": self._interceptor.summary(),
                    "user_agent": ua,
                },
            )

            logger.info(
                f"Scraped {url} in {elapsed:.0f}ms - {len(api_responses)} API responses captured"
            )
            return result

        finally:
            await context.close()

    async def scrape_multiple(
        self,
        urls: list[str],
        max_concurrent: int | None = None,
    ) -> list[ScrapeResult]:
        concurrency = max_concurrent or self._config.max_concurrent_pages
        sem = asyncio.Semaphore(concurrency)

        async def _scrape_one(u: str) -> ScrapeResult:
            async with sem:
                return await self._do_scrape(u, None, None, True)

        tasks = [_scrape_one(u) for u in urls]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def build_replayable_api(
        self,
        url: str,
        actions: list[Callable] | None = None,
    ) -> list[dict[str, Any]]:
        result = await self.scrape(url, actions=actions, extract_api=False)
        replayable = self._interceptor.get_replayable_requests()
        for entry in replayable:
            entry["source_page"] = url
            entry["dynamic_tokens"] = result.dynamic_tokens
        return replayable

    def get_dynamic_tokens(self) -> dict[str, str]:
        return self._dynamic_extractor.active_headers
