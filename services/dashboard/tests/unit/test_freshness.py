"""Staleness detection: catch a real stall without false-alarming on a closed market."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from finstream_dashboard import freshness

NOW = pd.Timestamp("2026-09-20 12:00", tz="UTC")


def coverage(rows: list[tuple[str, str, pd.Timestamp]]) -> pd.DataFrame:
    """Coverage frame shaped like `queries.coverage`: symbol, interval, bars, first_ts, last_ts."""
    return pd.DataFrame(
        {
            "symbol": [symbol for symbol, _, _ in rows],
            "bar_interval": [interval for _, interval, _ in rows],
            "bars": [10] * len(rows),
            "first_ts": [last - timedelta(days=5) for _, _, last in rows],
            "last_ts": [last for _, _, last in rows],
        }
    )


def states(assessed: pd.DataFrame) -> dict[str, str]:
    return dict(zip(assessed["symbol"], assessed["state"], strict=True))


def test_fresh_data_flags_nothing() -> None:
    frame = coverage(
        [
            ("GC=F", "1h", NOW - timedelta(hours=1)),
            ("SI=F", "1h", NOW - timedelta(hours=1)),
            ("BTC-USD", "1h", NOW - timedelta(minutes=20)),
        ]
    )
    assert freshness.overall(frame, now=NOW).is_stale is False
    assert set(states(freshness.assess(frame, now=NOW)).values()) == {freshness.OK}


def test_a_whole_stack_stall_is_caught() -> None:
    """The 20-hour suspended-laptop incident: everything aged together while runs said success."""
    frame = coverage(
        [
            ("GC=F", "1h", NOW - timedelta(hours=20)),
            ("SI=F", "1h", NOW - timedelta(hours=20)),
        ]
    )
    state = freshness.overall(frame, now=NOW)
    assert state.is_stale is True
    assert freshness.humanise(state.age) == "20h 0m"


def test_a_stack_wide_stall_flags_no_individual_series() -> None:
    """When everything is equally behind, no single series is the odd one out — the banner is."""
    frame = coverage(
        [
            ("GC=F", "1h", NOW - timedelta(hours=20)),
            ("SI=F", "1h", NOW - timedelta(hours=20)),
        ]
    )
    assert freshness.behind_series(freshness.assess(frame, now=NOW)) == []


def test_one_series_stuck_behind_its_peers_is_flagged() -> None:
    frame = coverage(
        [
            ("GC=F", "1h", NOW - timedelta(hours=1)),
            ("SI=F", "1h", NOW - timedelta(hours=1)),
            ("STUCK", "1h", NOW - timedelta(days=3)),
        ]
    )

    assessed = freshness.assess(frame, now=NOW)

    assert states(assessed) == {
        "GC=F": freshness.OK,
        "SI=F": freshness.OK,
        "STUCK": freshness.BEHIND,
    }
    assert freshness.behind_series(assessed) == ["STUCK"]
    assert freshness.overall(frame, now=NOW).is_stale is False, "collection itself is still running"


def test_a_247_series_does_not_flag_every_closed_market() -> None:
    """Regression: on a Sunday, Bitcoin as the reference flagged 25 of 50 live series.

    Measured against the freshest peer, one 24/7 instrument makes every market that is merely
    closed for the weekend look stalled. The median absorbs it.
    """
    frame = coverage(
        [
            ("BTC-USD", "1h", NOW - timedelta(minutes=20)),
            ("^GSPC", "1h", NOW - timedelta(days=2)),
            ("SPY", "1h", NOW - timedelta(days=2)),
            ("GC=F", "1h", NOW - timedelta(days=2)),
            ("EURUSD=X", "1h", NOW - timedelta(days=2)),
        ]
    )

    assessed = freshness.assess(frame, now=NOW)

    assert freshness.behind_series(assessed) == []
    assert freshness.overall(frame, now=NOW).is_stale is False


def test_a_series_fresher_than_the_median_is_never_flagged() -> None:
    frame = coverage(
        [
            ("BTC-USD", "1d", NOW),
            ("^GSPC", "1d", NOW - timedelta(days=3)),
            ("SPY", "1d", NOW - timedelta(days=3)),
        ]
    )
    assessed = freshness.assess(frame, now=NOW)
    btc = assessed[assessed["symbol"] == "BTC-USD"].iloc[0]
    assert btc["behind_by"] < timedelta(0)
    assert btc["state"] == freshness.OK


def test_a_closed_market_over_a_weekend_is_not_flagged() -> None:
    """Daily bars for a stock are legitimately days old on a Monday; that must not alarm."""
    frame = coverage(
        [
            ("^GSPC", "1d", NOW - timedelta(days=3)),
            ("SPY", "1d", NOW - timedelta(days=3)),
            ("BTC-USD", "1d", NOW - timedelta(days=2)),
        ]
    )
    assert freshness.behind_series(freshness.assess(frame, now=NOW)) == []


def test_intervals_are_judged_separately() -> None:
    """A daily series must never be measured against an hourly one."""
    frame = coverage(
        [
            ("GC=F", "1h", NOW - timedelta(hours=1)),
            ("GC=F", "1d", NOW - timedelta(days=1)),
        ]
    )
    assert set(states(freshness.assess(frame, now=NOW)).values()) == {freshness.OK}


def test_a_lone_series_is_never_behind_itself() -> None:
    frame = coverage([("GC=F", "1d", NOW - timedelta(days=30))])
    assessed = freshness.assess(frame, now=NOW)
    assert assessed["state"].tolist() == [freshness.OK]
    assert assessed["behind_by"].tolist() == [timedelta(0)]


def test_a_lone_stale_series_still_trips_the_overall_signal() -> None:
    """Nothing to compare with is not a reason to stay quiet about a dead collection."""
    frame = coverage([("GC=F", "1d", NOW - timedelta(days=30))])
    assert freshness.overall(frame, now=NOW).is_stale is True


def test_empty_coverage_is_not_stale_and_does_not_raise() -> None:
    state = freshness.overall(pd.DataFrame(), now=NOW)
    assert (state.newest, state.age, state.is_stale) == (None, None, False)
    assert freshness.assess(pd.DataFrame(), now=NOW).empty
    assert freshness.behind_series(pd.DataFrame()) == []


def test_coverage_of_all_null_timestamps_is_handled() -> None:
    frame = coverage([("GC=F", "1d", NOW)])
    frame["last_ts"] = [None]
    assert freshness.overall(frame, now=NOW).age is None


def test_tolerance_is_tighter_for_hourly_than_daily() -> None:
    assert freshness.tolerance("1h") < freshness.tolerance("1d")
    assert freshness.tolerance("5m") == freshness.DEFAULT_BEHIND_TOLERANCE


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (None, "n/a"),
        (timedelta(minutes=41), "41m"),
        (timedelta(hours=5, minutes=12), "5h 12m"),
        (timedelta(days=2, hours=3), "2d 3h"),
        (timedelta(seconds=-30), "0m"),
    ],
)
def test_humanise(age: timedelta | None, expected: str) -> None:
    assert freshness.humanise(age) == expected
