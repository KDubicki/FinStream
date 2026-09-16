"""Logging must be structured and carry the context passed via `extra`."""

from __future__ import annotations

import io
import json
import logging

from finstream_ingestor.logging_setup import build_handler, configure_logging


def _emit(log_format: str, **extra: object) -> str:
    handler = build_handler(log_format)
    stream = io.StringIO()
    handler.setStream(stream)  # type: ignore[attr-defined]
    logger = logging.getLogger("finstream_test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("ingest finished", extra=extra)
    return stream.getvalue().strip()


def test_json_format_is_parseable_and_keeps_context() -> None:
    payload = json.loads(_emit("json", job="daily", symbol="SPY", rows=42))
    assert payload["message"] == "ingest finished"
    assert payload["level"] == "INFO"
    assert payload["job"] == "daily"
    assert payload["symbol"] == "SPY"
    assert payload["rows"] == 42
    assert payload["timestamp"]


def test_text_format_is_human_readable() -> None:
    line = _emit("text")
    assert "ingest finished" in line
    assert "INFO" in line
    assert not line.startswith("{")


def test_configure_logging_installs_single_handler() -> None:
    configure_logging("DEBUG", "json")
    configure_logging("WARNING", "text")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING
