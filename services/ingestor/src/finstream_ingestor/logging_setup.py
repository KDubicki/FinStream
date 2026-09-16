"""Structured logging, configured once at startup (see the development skill).

JSON in containers, plain text for local debugging. Context belongs in `extra`, e.g.
`logger.info("ingest finished", extra={"job": job, "symbol": symbol})`.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

JSON_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"

#: Third-party loggers that are too chatty at INFO.
NOISY_LOGGERS = ("urllib3", "yfinance", "peewee")


def build_handler(log_format: str) -> logging.Handler:
    """Create the single stdout handler used by the service."""
    handler = logging.StreamHandler(stream=sys.stdout)
    if log_format == "json":
        handler.setFormatter(
            JsonFormatter(JSON_FORMAT, rename_fields={"asctime": "timestamp", "levelname": "level"})
        )
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    return handler


def configure_logging(log_level: str, log_format: str) -> None:
    """Install the root handler. Safe to call more than once (tests do)."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(build_handler(log_format))
    root.setLevel(log_level)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
