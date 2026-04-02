"""Alerting system - sends notifications via Telegram bot or webhook."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from fishhook.utils.logging import get_logger

logger = get_logger("utils.alerting")


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    title: str
    message: str
    severity: AlertSeverity
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def format_text(self) -> str:
        icon = {
            "info": "\U0001f535",
            "warning": "\U0001f7e1",
            "critical": "\U0001f534",
        }.get(self.severity.value, "")
        ts = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return (
            f"{icon} [{self.severity.value.upper()}] {ts}\n{self.title}\n{self.message}"
        )


class AlertChannel:
    async def send(self, alert: Alert) -> bool:
        raise NotImplementedError


class TelegramChannel(AlertChannel):
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def send(self, alert: Alert) -> bool:
        if not self._bot_token or not self._chat_id:
            return False
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": alert.format_text(),
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")
            return False

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class WebhookChannel(AlertChannel):
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = headers or {"Content-Type": "application/json"}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def send(self, alert: Alert) -> bool:
        if not self._url:
            return False
        try:
            client = await self._get_client()
            resp = await client.post(
                self._url,
                json=alert.to_dict(),
                headers=self._headers,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Webhook alert failed: {e}")
            return False

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class AlertManager:
    def __init__(
        self,
        min_severity: AlertSeverity = AlertSeverity.WARNING,
        rate_limit_seconds: int = 60,
    ) -> None:
        self._channels: list[AlertChannel] = []
        self._min_severity = min_severity
        self._rate_limit = rate_limit_seconds
        self._last_alert_time: dict[str, float] = {}
        self._history: list[Alert] = []

    def add_channel(self, channel: AlertChannel) -> None:
        self._channels.append(channel)

    async def send(self, alert: Alert) -> int:
        if alert.severity.value < self._min_severity.value:
            return 0

        key = f"{alert.severity.value}:{alert.title}"
        now = time.time()
        if key in self._last_alert_time:
            if now - self._last_alert_time[key] < self._rate_limit:
                return 0

        self._last_alert_time[key] = now
        self._history.append(alert)
        if len(self._history) > 200:
            self._history = self._history[-200:]

        sent = 0
        for channel in self._channels:
            try:
                if await channel.send(alert):
                    sent += 1
            except Exception as e:
                logger.warning(f"Alert channel error: {e}")

        if sent > 0:
            logger.info(f"Alert sent: {alert.title} ({alert.severity.value})")
        return sent

    async def info(self, title: str, message: str, **metadata: Any) -> int:
        return await self.send(
            Alert(
                title=title,
                message=message,
                severity=AlertSeverity.INFO,
                metadata=metadata,
            )
        )

    async def warning(self, title: str, message: str, **metadata: Any) -> int:
        return await self.send(
            Alert(
                title=title,
                message=message,
                severity=AlertSeverity.WARNING,
                metadata=metadata,
            )
        )

    async def critical(self, title: str, message: str, **metadata: Any) -> int:
        return await self.send(
            Alert(
                title=title,
                message=message,
                severity=AlertSeverity.CRITICAL,
                metadata=metadata,
            )
        )

    async def close(self) -> None:
        for channel in self._channels:
            try:
                await channel.close()
            except Exception:
                pass

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._history[-limit:]]
