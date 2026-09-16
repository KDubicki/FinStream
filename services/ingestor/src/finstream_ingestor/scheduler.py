"""Scheduling: which jobs run, how often, and what happens when one misbehaves.

APScheduler 3.11 with an in-process BlockingScheduler and the in-memory job store (ADR-0003).
Schedule state is deliberately not persisted: every job also runs at startup, and the lookback
windows overlap, so a restart heals any gap through idempotent upserts (GR-2).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.engine import Engine
from tenacity import Retrying

from finstream_ingestor.config import Settings
from finstream_ingestor.jobs import JobSummary, ingest
from finstream_ingestor.sources.base import PriceSource

logger = logging.getLogger(__name__)

INTRADAY_JOB_ID = "intraday"
DAILY_JOB_ID = "daily"
BACKFILL_JOB_ID = "backfill"
HEARTBEAT_JOB_ID = "heartbeat"

#: Backfill and the daily job both work on daily bars.
DAILY_INTERVAL = "1d"

#: Jobs that also run once at startup. ADR-0003 relies on this: the in-memory job store
#: forgets the schedule on restart, and an immediate run plus overlapping lookbacks is what
#: heals the gap (GR-2 makes the repeat harmless).
RUN_AT_STARTUP = (INTRADAY_JOB_ID, DAILY_JOB_ID, HEARTBEAT_JOB_ID)


def job_defaults(settings: Settings) -> dict[str, object]:
    """Defaults every job inherits.

    One run at a time (`max_instances`), a backlog collapses into a single run (`coalesce`),
    and a late trigger may still fire within the grace period.
    """
    return {
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": settings.scheduler_misfire_grace_seconds,
    }


def touch_heartbeat(path: Path) -> None:
    """Prove the scheduler is still running; the Docker healthcheck reads this file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def on_job_problem(event: JobExecutionEvent) -> None:
    """Defence in depth: jobs are not supposed to raise, so shout if one does (GR-3)."""
    if event.exception is not None:
        logger.error(
            "job raised into the scheduler, which should not happen",
            extra={"job_id": event.job_id},
            exc_info=(type(event.exception), event.exception, event.traceback),
        )
    else:
        logger.warning("job missed its scheduled run", extra={"job_id": event.job_id})


def build_scheduler(
    settings: Settings,
    *,
    engine: Engine,
    source: PriceSource,
    db_retryer: Retrying | None = None,
) -> BlockingScheduler:
    """Wire up every enabled job. Jobs never overlap themselves and never pile up."""
    scheduler = BlockingScheduler(
        timezone=settings.scheduler_timezone,
        job_defaults=job_defaults(settings),
    )
    scheduler.add_listener(on_job_problem, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    now = datetime.now(UTC)

    def ingestion(interval: str, period: str, job: str) -> partial[JobSummary]:
        return partial(
            ingest,
            engine=engine,
            source=source,
            job=job,
            symbols=settings.symbols,
            interval=interval,
            period=period,
            db_retryer=db_retryer,
        )

    if settings.intraday_enabled:
        scheduler.add_job(
            ingestion(settings.intraday_interval, settings.intraday_lookback, INTRADAY_JOB_ID),
            IntervalTrigger(minutes=settings.intraday_every_minutes),
            id=INTRADAY_JOB_ID,
            name=f"intraday {settings.intraday_interval} bars",
            next_run_time=now,  # don't wait a whole interval for the first run
        )

    if settings.daily_enabled:
        scheduler.add_job(
            ingestion(DAILY_INTERVAL, settings.daily_lookback, DAILY_JOB_ID),
            # Day-of-week must be named: APScheduler counts 0 as Monday while classic cron counts
            # it as Sunday, so "1-5" would silently mean Tue-Sat. Settings rejects numeric values.
            CronTrigger.from_crontab(settings.daily_cron, timezone=settings.scheduler_timezone),
            id=DAILY_JOB_ID,
            name="daily bars after the close",
            next_run_time=now,  # catch up immediately rather than waiting for the next close
        )

    if settings.backfill_on_start:
        scheduler.add_job(
            ingestion(DAILY_INTERVAL, settings.backfill_period, BACKFILL_JOB_ID),
            DateTrigger(run_date=now),
            id=BACKFILL_JOB_ID,
            name=f"one-off backfill of {settings.backfill_period}",
        )

    scheduler.add_job(
        partial(touch_heartbeat, settings.heartbeat_file),
        IntervalTrigger(seconds=settings.heartbeat_every_seconds),
        id=HEARTBEAT_JOB_ID,
        name="heartbeat",
        next_run_time=now,
    )

    logger.info(
        "scheduler configured",
        extra={
            "jobs": [job.id for job in scheduler.get_jobs()],
            "timezone": settings.scheduler_timezone,
            "symbols": len(settings.symbols),
        },
    )
    return scheduler
