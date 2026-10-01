"""Tests for config versioning and snapshot persistence."""

from fishhook.config.settings import PipelineConfig


def test_fingerprint_is_deterministic() -> None:
    a = PipelineConfig()
    b = PipelineConfig()

    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() == a.fingerprint()
    assert a.config_tag() == a.fingerprint()[:12]


def test_fingerprint_changes_with_parameters() -> None:
    base = PipelineConfig()
    modified = PipelineConfig()
    modified.strategy.divergence_threshold = 0.2

    assert base.fingerprint() != modified.fingerprint()


def test_fingerprint_ignores_secrets() -> None:
    base = PipelineConfig()
    with_secret = PipelineConfig()
    with_secret.polymarket.api_key = "sk-test"
    with_secret.alerting.telegram.bot_token = "tkn"
    with_secret.data_sources.dune.api_key = "dune-key"
    with_secret.data_sources.nansen.api_key = "nansen-key"

    assert base.fingerprint() == with_secret.fingerprint()


def test_snapshot_redacts_secrets() -> None:
    config = PipelineConfig()
    config.polymarket.api_key = "sk-test"
    config.polymarket.api_secret = "sec"
    config.polymarket.passphrase = "phrase"
    config.alerting.telegram.bot_token = "tkn"
    config.data_sources.nansen.api_key = "nansen-key"

    snap = config.snapshot()

    assert snap["polymarket"]["api_key"] == "***"
    assert snap["polymarket"]["api_secret"] == "***"
    assert snap["polymarket"]["passphrase"] == "***"
    assert snap["alerting"]["telegram"]["bot_token"] == "***"
    assert snap["data_sources"]["nansen"]["api_key"] == "***"


def test_orchestrator_persists_config_snapshot(tmp_path) -> None:
    import json

    from fishhook.orchestrator import PipelineOrchestrator

    config = PipelineConfig(data_dir=tmp_path)
    orchestrator = PipelineOrchestrator(config)

    version = config.fingerprint()
    snapshot_path = tmp_path / "config_snapshots" / f"{version}.json"

    assert snapshot_path.exists()
    data = json.loads(snapshot_path.read_text())
    assert data["config_version"] == version
    assert data["config"]["polymarket"]["api_key"] == "***"

    status = orchestrator.get_status()
    assert status["config_version"] == version
    assert "config_snapshot" in status