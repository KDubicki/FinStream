"""Entrypoint: wire everything together and hand control to the scheduler.

Startup order matters. Configuration is validated first so a typo fails immediately, then the
database is waited for and the schema created, and only then does the scheduler start.
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from apscheduler.schedulers.blocking import BlockingScheduler

from finstream_ingestor.config import Settings
from finstream_ingestor.db import build_retryer as build_db_retryer
from finstream_ingestor.db import create_db_engine, init_schema, wait_for_db
from finstream_ingestor.logging_setup import configure_logging
from finstream_ingestor.scheduler import build_scheduler
from finstream_ingestor.sources.yahoo import YahooSource
from finstream_ingestor.sources.yahoo import build_retryer as build_source_retryer

logger = logging.getLogger(__name__)


def install_signal_handlers(scheduler: BlockingScheduler) -> None:
    """Stop promptly on SIGTERM/SIGINT so `docker compose down` is not a kill."""

    def handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("shutting down", extra={"signal": signal.Signals(signum).name})
        # wait=False: a job may be mid-retry, and an in-flight upsert is atomic either way (GR-2).
        scheduler.shutdown(wait=False)

    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, handle)


def run(settings: Settings) -> int:
    """Start the service. Returns a process exit code."""
    configure_logging(settings.log_level, settings.log_format)
    logger.info(
        "starting FinStream Ingestor",
        extra={"symbols": len(settings.symbols), "timescaledb": settings.timescaledb_enabled},
    )

    engine = create_db_engine(settings.database_url.get_secret_value())
    db_retryer = build_db_retryer(settings.retry_max_attempts, settings.retry_max_wait_seconds)

    try:
        # A database that never comes back should crash the container, not spin quietly:
        # Compose restarts it, and the failure stays visible.
        wait_for_db(engine, db_retryer)
        init_schema(engine, timescaledb_enabled=settings.timescaledb_enabled)

        source = YahooSource(
            retryer=build_source_retryer(
                settings.retry_max_attempts, settings.retry_max_wait_seconds
            )
        )
        scheduler = build_scheduler(settings, engine=engine, source=source, db_retryer=db_retryer)
        install_signal_handlers(scheduler)

        scheduler.start()  # blocks until shutdown
    except (KeyboardInterrupt, SystemExit):
        logger.info("interrupted")
    finally:
        engine.dispose()
        logger.info("stopped")
    return 0


def main() -> int:
    settings = Settings()  # type: ignore[call-arg]  # values come from the environment
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
