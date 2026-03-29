"""Structured logging for the pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | None = None,
) -> logging.Logger:
    logger = logging.getLogger("fishhook")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    console = Console(stderr=True)
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(logging.DEBUG)
    logger.addHandler(rich_handler)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "pipeline.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"fishhook.{name}")
