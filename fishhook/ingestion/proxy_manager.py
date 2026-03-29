"""Proxy manager - handles IP rotation and proxy pool management."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from itertools import cycle

from fishhook.config.settings import ProxyConfig
from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.proxy")


@dataclass
class ProxyEntry:
    url: str
    protocol: str = "http"
    fail_count: int = 0
    last_used: float = 0.0
    avg_latency_ms: float = 0.0
    is_banned: bool = False

    @classmethod
    def from_url(cls, url: str) -> ProxyEntry:
        if url.startswith("socks"):
            protocol = "socks5"
        elif url.startswith("https"):
            protocol = "https"
        else:
            protocol = "http"
        return cls(url=url, protocol=protocol)

    @property
    def is_available(self) -> bool:
        return not self.is_banned and self.fail_count < 5


class ProxyManager:
    def __init__(self, config: ProxyConfig | None = None) -> None:
        self._config = config or ProxyConfig()
        self._proxies: list[ProxyEntry] = []
        self._current_index = 0
        self._rotation_interval = self._config.rotation_interval_seconds
        self._last_rotation = time.time()

        if self._config.proxies:
            for url in self._config.proxies:
                self._proxies.append(ProxyEntry.from_url(url))
            logger.info(f"Initialized proxy pool with {len(self._proxies)} proxies")

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled and len(self._proxies) > 0

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._proxies if p.is_available)

    def add_proxy(self, url: str) -> None:
        entry = ProxyEntry.from_url(url)
        self._proxies.append(entry)
        logger.debug(f"Added proxy: {url}")

    def get_proxy(self) -> str | None:
        if not self.is_enabled:
            return None

        available = [p for p in self._proxies if p.is_available]
        if not available:
            logger.warning("No available proxies, resetting banned list")
            for p in self._proxies:
                p.is_banned = False
                p.fail_count = 0
            available = self._proxies

        now = time.time()
        if now - self._last_rotation > self._rotation_interval:
            self._current_index = (self._current_index + 1) % len(available)
            self._last_rotation = now

        proxy = available[self._current_index % len(available)]
        proxy.last_used = now
        return proxy.url

    def get_proxy_playwright(self) -> dict[str, str] | None:
        url = self.get_proxy()
        if url is None:
            return None
        return {"server": url}

    def report_success(self, url: str, latency_ms: float) -> None:
        for p in self._proxies:
            if p.url == url:
                p.fail_count = max(0, p.fail_count - 1)
                alpha = 0.3
                p.avg_latency_ms = alpha * latency_ms + (1 - alpha) * p.avg_latency_ms
                break

    def report_failure(self, url: str) -> None:
        for p in self._proxies:
            if p.url == url:
                p.fail_count += 1
                if p.fail_count >= 5:
                    p.is_banned = True
                    logger.warning(f"Proxy banned after {p.fail_count} failures: {url}")
                break

    def get_stats(self) -> dict:
        return {
            "total": len(self._proxies),
            "available": self.available_count,
            "banned": sum(1 for p in self._proxies if p.is_banned),
            "enabled": self.is_enabled,
        }
