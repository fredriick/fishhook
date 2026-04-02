"""Structured logging for the pipeline with correlation IDs."""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(cid: str) -> None:
    _correlation_id.set(cid)


def generate_correlation_id() -> str:
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def clear_correlation_id() -> None:
    _correlation_id.set("")


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", "") or ""
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        extra = ""
        if hasattr(record, "structured_data"):
            sd = record.structured_data
            extra = " " + " ".join(f"{k}={v}" for k, v in sd.items())
        return f"{ts} | {record.levelname:<8} | {cid:<12} | {record.name} | {record.getMessage()}{extra}"


class StructuredLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, msg: str, **kwargs: object) -> None:
        record = self._logger.makeRecord(
            self._logger.name, logging.INFO, "", 0, msg, (), None
        )
        if kwargs:
            record.structured_data = kwargs
        self._logger.handle(record)

    def warning(self, msg: str, **kwargs: object) -> None:
        record = self._logger.makeRecord(
            self._logger.name, logging.WARNING, "", 0, msg, (), None
        )
        if kwargs:
            record.structured_data = kwargs
        self._logger.handle(record)

    def error(self, msg: str, **kwargs: object) -> None:
        record = self._logger.makeRecord(
            self._logger.name, logging.ERROR, "", 0, msg, (), None
        )
        if kwargs:
            record.structured_data = kwargs
        self._logger.handle(record)


def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | None = None,
) -> logging.Logger:
    logger = logging.getLogger("fishhook")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    correlation_filter = CorrelationFilter()

    console = Console(stderr=True)
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(logging.DEBUG)
    rich_handler.addFilter(correlation_filter)
    logger.addHandler(rich_handler)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "pipeline.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(StructuredFormatter())
        file_handler.addFilter(correlation_filter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"fishhook.{name}")


def get_structured_logger(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(f"fishhook.{name}"))
