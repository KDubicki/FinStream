"""The job boundary: one bad symbol must never stop the others, or the scheduler (GR-3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from finstream_ingestor.jobs import ingest
from finstream_ingestor.sources.base import (
    PermanentSourceError,
    PriceBar,
    RateLimitedError,
    TransientSourceError,
)


class FakeSource:
    """A PriceSource whose behaviour is scripted per symbol."""

    name = "fake"

    def __init__(self, behaviour: dict[str, Any]) -> None:
        self.behaviour = behaviour
        self.calls: list[str] = []

    def fetch(self, symbol: str, *, interval: str, period: str) -> list[PriceBar]:
        self.calls.append(symbol)
        outcome = self.behaviour.get(symbol, [])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_bar(symbol: str = "SPY") -> PriceBar:
    return PriceBar(
        source="fake",
        symbol=symbol,
        bar_interval="1d",
        ts=datetime(2026, 9, 15, tzinfo=UTC),
        close=1.0,
    )


class RecordingDb:
    """Stands in for the database layer, recording what the job wrote."""

    def __init__(self, *, upsert_error: BaseException | None = None) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.upserted: list[PriceBar] = []
        self.upsert_error = upsert_error
        self._next_id = 0

    def start_run(self, engine: Any, **kwargs: Any) -> str:
        self._next_id += 1
        run_id = f"run-{self._next_id}"
        self.runs[run_id] = {**kwargs, "status": "running"}
        return run_id

    def finish_run(self, engine: Any, run_id: str, **kwargs: Any) -> None:
        self.runs[run_id].update(kwargs)

    def upsert_bars(self, engine: Any, bars: list[PriceBar], **kwargs: Any) -> int:
        if self.upsert_error is not None:
            raise self.upsert_error
        self.upserted.extend(bars)
        return len(bars)

    def statuses(self) -> list[str]:
        return [run["status"] for run in self.runs.values()]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> RecordingDb:
    recorder = RecordingDb()
    monkeypatch.setattr("finstream_ingestor.jobs.start_run", recorder.start_run)
    monkeypatch.setattr("finstream_ingestor.jobs.finish_run", recorder.finish_run)
    monkeypatch.setattr("finstream_ingestor.jobs.upsert_bars", recorder.upsert_bars)
    return recorder


def run_job(source: FakeSource, symbols: list[str]) -> Any:
    return ingest(
        engine=None,  # type: ignore[arg-type]  # the database layer is faked
        source=source,
        job="daily",
        symbols=symbols,
        interval="1d",
        period="10d",
    )


def test_one_failing_symbol_does_not_stop_the_others(db: RecordingDb) -> None:
    """The headline guarantee of GR-3."""
    source = FakeSource(
        {
            "A": [make_bar("A")],
            "B": TransientSourceError("network went away"),
            "C": [make_bar("C")],
        }
    )

    summary = run_job(source, ["A", "B", "C"])

    assert source.calls == ["A", "B", "C"], "every symbol must be attempted"
    assert (summary.succeeded, summary.failed, summary.empty) == (2, 1, 0)
    assert sorted(db.statuses()) == ["failed", "success", "success"]
    assert {bar.symbol for bar in db.upserted} == {"A", "C"}


def test_the_job_never_raises(db: RecordingDb) -> None:
    """A raising job would kill the scheduler, and with it all data collection."""
    source = FakeSource({"A": RuntimeError("something unexpected")})
    summary = run_job(source, ["A"])
    assert summary.failed == 1


def test_no_bars_is_recorded_as_empty_not_failed(db: RecordingDb) -> None:
    """A holiday is not an outage."""
    source = FakeSource({"A": []})
    summary = run_job(source, ["A"])

    assert (summary.empty, summary.failed, summary.succeeded) == (1, 0, 0)
    assert db.statuses() == ["empty"]


def test_exhausted_retries_are_recorded_with_the_error(db: RecordingDb) -> None:
    source = FakeSource({"A": RateLimitedError("still rate limited after 5 attempts")})
    summary = run_job(source, ["A"])

    run = next(iter(db.runs.values()))
    assert summary.failed == 1
    assert run["status"] == "failed"
    assert "rate limited" in run["error"]


def test_permanent_errors_are_recorded_per_symbol(db: RecordingDb) -> None:
    source = FakeSource(
        {"GOOD": [make_bar("GOOD")], "NOPE": PermanentSourceError("NOPE: delisted")}
    )
    summary = run_job(source, ["GOOD", "NOPE"])
    assert (summary.succeeded, summary.failed) == (1, 1)


def test_counts_rows_actually_written(db: RecordingDb) -> None:
    source = FakeSource({"A": [make_bar("A"), make_bar("A")]})
    summary = run_job(source, ["A"])
    assert summary.rows_upserted == 2
    assert summary.symbols == 1


def test_a_database_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the database breaks mid-job, the job still returns and records what it can."""
    recorder = RecordingDb(upsert_error=RuntimeError("connection lost"))
    monkeypatch.setattr("finstream_ingestor.jobs.start_run", recorder.start_run)
    monkeypatch.setattr("finstream_ingestor.jobs.finish_run", recorder.finish_run)
    monkeypatch.setattr("finstream_ingestor.jobs.upsert_bars", recorder.upsert_bars)

    summary = run_job(FakeSource({"A": [make_bar("A")]}), ["A"])
    assert summary.failed == 1
    assert recorder.statuses() == ["failed"]


def test_bookkeeping_failure_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even recording the failure can fail; the loop must survive that too."""
    recorder = RecordingDb()

    def exploding_finish(engine: Any, run_id: str, **kwargs: Any) -> None:
        raise RuntimeError("cannot write the run record")

    monkeypatch.setattr("finstream_ingestor.jobs.start_run", recorder.start_run)
    monkeypatch.setattr("finstream_ingestor.jobs.finish_run", exploding_finish)
    monkeypatch.setattr("finstream_ingestor.jobs.upsert_bars", recorder.upsert_bars)

    summary = run_job(FakeSource({"A": [make_bar("A")]}), ["A"])
    assert summary.failed == 1
