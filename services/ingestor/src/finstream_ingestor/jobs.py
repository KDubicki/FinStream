"""Ingestion jobs: fetch one symbol, store it, record the attempt.

This module is where GR-3 lives. A failure for one symbol is logged and written to
`raw.ingestion_runs`, and the loop moves on to the next symbol. Nothing here may raise into
the scheduler, because a scheduler that dies stops collecting data entirely.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from tenacity import Retrying

from finstream_ingestor.db import finish_run, start_run, upsert_bars
from finstream_ingestor.sources.base import PriceSource

logger = logging.getLogger(__name__)


@dataclass
class JobSummary:
    """What one pass over the configured symbols achieved."""

    job: str
    interval: str
    succeeded: int = 0
    empty: int = 0
    failed: int = 0
    rows_upserted: int = 0

    @property
    def symbols(self) -> int:
        return self.succeeded + self.empty + self.failed

    def as_log_context(self) -> dict[str, int | str]:
        return {
            "job": self.job,
            "interval": self.interval,
            "symbols": self.symbols,
            "succeeded": self.succeeded,
            "empty": self.empty,
            "failed": self.failed,
            "rows_upserted": self.rows_upserted,
        }


def _record_failure(
    engine: Engine,
    run_id: object,
    error: BaseException,
    retryer: Retrying | None,
) -> None:
    """Write the failure down, but never let bookkeeping break the job loop."""
    if run_id is None:
        return
    try:
        finish_run(engine, run_id, status="failed", error=repr(error), retryer=retryer)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - the database is already misbehaving; only log
        logger.exception("could not record the failed run", extra={"run_id": str(run_id)})


def ingest(
    *,
    engine: Engine,
    source: PriceSource,
    job: str,
    symbols: Sequence[str],
    interval: str,
    period: str,
    db_retryer: Retrying | None = None,
) -> JobSummary:
    """Fetch and store every symbol, isolating failures. Never raises (GR-3)."""
    summary = JobSummary(job=job, interval=interval)
    logger.info(
        "job started",
        extra={"job": job, "interval": interval, "period": period, "symbols": len(symbols)},
    )

    for symbol in symbols:
        context = {"job": job, "source": source.name, "symbol": symbol, "interval": interval}
        run_id = None
        try:
            run_id = start_run(
                engine,
                job=job,
                source=source.name,
                symbol=symbol,
                bar_interval=interval,
                retryer=db_retryer,
            )
            bars = source.fetch(symbol, interval=interval, period=period)
            written = upsert_bars(engine, bars, retryer=db_retryer)

            if bars:
                summary.succeeded += 1
                summary.rows_upserted += written
                status = "success"
            else:
                summary.empty += 1
                status = "empty"

            finish_run(
                engine,
                run_id,
                status=status,
                rows_received=len(bars),
                rows_upserted=written,
                retryer=db_retryer,
            )
            logger.info("symbol ingested", extra={**context, "status": status, "rows": written})

        except Exception as exc:  # noqa: BLE001 - deliberate job boundary (GR-3)
            summary.failed += 1
            logger.exception("symbol failed", extra={**context, "run_id": str(run_id)})
            _record_failure(engine, run_id, exc, db_retryer)

    log = logger.warning if summary.failed else logger.info
    log("job finished", extra=summary.as_log_context())
    return summary
