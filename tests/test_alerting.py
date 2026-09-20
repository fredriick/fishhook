"""Regression tests for alert severity gating."""

import pytest

from fishhook.utils.alerting import Alert, AlertManager, AlertSeverity


class RecordingChannel:
    def __init__(self) -> None:
        self.received: list[Alert] = []

    async def send(self, alert: Alert) -> bool:
        self.received.append(alert)
        return True


@pytest.mark.asyncio
async def test_critical_passes_warning_gate() -> None:
    manager = AlertManager(min_severity=AlertSeverity.WARNING)
    channel = RecordingChannel()
    manager.add_channel(channel)

    sent = await manager.send(
        Alert(title="halted", message="breaker open", severity=AlertSeverity.CRITICAL)
    )

    assert sent == 1
    assert len(channel.received) == 1


@pytest.mark.asyncio
async def test_warning_passes_warning_gate() -> None:
    manager = AlertManager(min_severity=AlertSeverity.WARNING)
    channel = RecordingChannel()
    manager.add_channel(channel)

    sent = await manager.send(
        Alert(title="slow", message="api latency up", severity=AlertSeverity.WARNING)
    )

    assert sent == 1
    assert len(channel.received) == 1


@pytest.mark.asyncio
async def test_info_dropped_at_warning_gate() -> None:
    manager = AlertManager(min_severity=AlertSeverity.WARNING)
    channel = RecordingChannel()
    manager.add_channel(channel)

    sent = await manager.send(
        Alert(title="tick", message="run finished", severity=AlertSeverity.INFO)
    )

    assert sent == 0
    assert channel.received == []


@pytest.mark.asyncio
async def test_critical_passes_critical_gate() -> None:
    manager = AlertManager(min_severity=AlertSeverity.CRITICAL)
    channel = RecordingChannel()
    manager.add_channel(channel)

    sent = await manager.send(
        Alert(title="halted", message="breaker open", severity=AlertSeverity.CRITICAL)
    )

    assert sent == 1
    assert len(channel.received) == 1


@pytest.mark.asyncio
async def test_warning_dropped_at_critical_gate() -> None:
    manager = AlertManager(min_severity=AlertSeverity.CRITICAL)
    channel = RecordingChannel()
    manager.add_channel(channel)

    sent = await manager.send(
        Alert(title="slow", message="api latency up", severity=AlertSeverity.WARNING)
    )

    assert sent == 0
    assert channel.received == []