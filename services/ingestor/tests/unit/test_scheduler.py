"""Scheduler wiring: the right jobs, with settings that stop runs from piling up."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from finstream_ingestor.scheduler import (
    BACKFILL_JOB_ID,
    DAILY_JOB_ID,
    HEARTBEAT_JOB_ID,
    INTRADAY_JOB_ID,
    build_scheduler,
    job_defaults,
    touch_heartbeat,
)
from finstream_ingestor.sources.base import PriceBar


class DummySource:
    name = "dummy"

    def fetch(self, symbol: str, *, interval: str, period: str) -> list[PriceBar]:
        return []


def build(settings) -> object:
    return build_scheduler(settings, engine=None, source=DummySource())  # type: ignore[arg-type]


def job_ids(scheduler) -> set[str]:
    return {job.id for job in scheduler.get_jobs()}


def test_all_jobs_are_registered_by_default(make_settings) -> None:
    scheduler = build(make_settings(backfill_on_start=True))
    assert job_ids(scheduler) == {INTRADAY_JOB_ID, DAILY_JOB_ID, BACKFILL_JOB_ID, HEARTBEAT_JOB_ID}


def test_disabled_jobs_are_not_scheduled(make_settings) -> None:
    scheduler = build(make_settings(intraday_enabled=False, daily_enabled=False))
    assert job_ids(scheduler) == {HEARTBEAT_JOB_ID}, "the heartbeat always runs"


def test_backfill_is_off_unless_asked_for(make_settings) -> None:
    assert BACKFILL_JOB_ID not in job_ids(build(make_settings()))


def test_job_defaults_prevent_overlap_and_pile_up(make_settings) -> None:
    """max_instances=1 and coalesce keep a slow fetch from stacking runs."""
    settings = make_settings(scheduler_misfire_grace_seconds=123)
    assert job_defaults(settings) == {
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": 123,
    }


def test_the_scheduler_is_given_those_defaults(make_settings) -> None:
    """Jobs are still pending here.

    APScheduler copies the defaults onto each job only when they move into the job store on
    start(), so a pending job carries none of them yet. What can be asserted before starting is
    that the scheduler itself holds them.
    """
    settings = make_settings(scheduler_misfire_grace_seconds=123)
    scheduler = build(settings)
    assert scheduler._job_defaults == job_defaults(settings)


def test_trigger_types(make_settings) -> None:
    scheduler = build(make_settings(backfill_on_start=True))
    triggers = {job.id: job.trigger for job in scheduler.get_jobs()}
    assert isinstance(triggers[INTRADAY_JOB_ID], IntervalTrigger)
    assert isinstance(triggers[DAILY_JOB_ID], CronTrigger)
    assert isinstance(triggers[BACKFILL_JOB_ID], DateTrigger)
    assert isinstance(triggers[HEARTBEAT_JOB_ID], IntervalTrigger)


def test_intraday_interval_comes_from_settings(make_settings) -> None:
    scheduler = build(make_settings(intraday_every_minutes=15))
    trigger = next(j.trigger for j in scheduler.get_jobs() if j.id == INTRADAY_JOB_ID)
    assert trigger.interval.total_seconds() == 15 * 60


def test_named_weekdays_do_not_fire_at_the_weekend(make_settings) -> None:
    """Regression guard for the APScheduler numbering trap (research R2).

    `from_crontab` keeps APScheduler's numbering, where 0 is Monday, so "1-5" means Tue-Sat.
    Config rejects numeric weekdays; this proves why with the real library.
    """
    scheduler = build(make_settings(daily_cron="30 22 * * mon-fri"))
    trigger = next(j.trigger for j in scheduler.get_jobs() if j.id == DAILY_JOB_ID)

    utc = ZoneInfo("UTC")
    friday = datetime(2026, 9, 18, 0, 0, tzinfo=utc)
    saturday_evening = datetime(2026, 9, 19, 22, 30, tzinfo=utc)

    assert trigger.get_next_fire_time(None, friday) == datetime(2026, 9, 18, 22, 30, tzinfo=utc)
    assert trigger.get_next_fire_time(None, friday) != saturday_evening

    numeric = CronTrigger.from_crontab("30 22 * * 1-5", timezone="UTC")
    assert (
        numeric.get_next_fire_time(None, datetime(2026, 9, 19, 0, 0, tzinfo=utc))
        == saturday_evening
    ), "numeric weekdays really do fire on Saturday, which is why settings reject them"


def test_heartbeat_file_is_created_and_refreshed(tmp_path: Path) -> None:
    beat = tmp_path / "nested" / "heartbeat"
    touch_heartbeat(beat)
    assert beat.exists()

    first = beat.stat().st_mtime
    touch_heartbeat(beat)
    assert beat.stat().st_mtime >= first
