"""Persistent portfolio and trade ledger.

Appends every executed trade and periodic portfolio snapshots to JSONL
files under ``data_dir/portfolio`` so trade history, positions, and realized
P&L survive restarts. State (realized P&L) is kept in a small JSON file and
rewritten after each trade.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("portfolio.ledger")


class PortfolioLedger:
    def __init__(self, data_dir: Path) -> None:
        self._dir = Path(data_dir) / "portfolio"
        self._trades_path = self._dir / "trades.jsonl"
        self._snapshots_path = self._dir / "snapshots.jsonl"
        self._state_path = self._dir / "state.json"
        self._last_snapshot: dict[str, Any] | None = None

    @property
    def dir(self) -> Path:
        return self._dir

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError as e:
            logger.warning(f"Could not append to {path}: {e}")

    def record_trade(self, trade: Any) -> None:
        self._append(self._trades_path, trade.to_dict())

    def record_snapshot(self, portfolio: dict[str, Any]) -> None:
        record = {"ts": time.time(), "portfolio": portfolio}
        self._last_snapshot = record
        self._append(self._snapshots_path, record)

    def save_state(self, realized_pnl: float) -> None:
        state: dict[str, Any] = {
            "realized_pnl": round(float(realized_pnl), 4),
            "updated_at": time.time(),
        }
        if self._last_snapshot:
            state["last_snapshot_ts"] = self._last_snapshot["ts"]
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            tmp.replace(self._state_path)
        except OSError as e:
            logger.warning(f"Could not persist state to {self._state_path}: {e}")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read {path}: {e}")
        return rows

    def _read_state(self) -> dict[str, Any]:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def load(self) -> dict[str, Any]:
        """Load persisted trades, realized P&L, and snapshot history."""
        state = self._read_state()
        return {
            "trades": self._read_jsonl(self._trades_path),
            "realized_pnl": float(state.get("realized_pnl", 0.0)),
            "snapshots": self._read_jsonl(self._snapshots_path),
        }