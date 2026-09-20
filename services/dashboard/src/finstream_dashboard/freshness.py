"""Is data still arriving?

A suspended laptop once left the database 20 hours stale while every ingestion run still reported
`success` (`docs/architecture.md`). Plan 0005 fixed the cause; this module makes the symptom
visible, so a stall shows on the page instead of in the logs.

**Why series are compared with their peers rather than with the clock.** A calendar rule — "an
hourly series older than two hours is stale" — is wrong for anything that does not trade around the
clock: an index is legitimately 65 hours old between Friday's close and Monday's open, so a
threshold loose enough to avoid false alarms is far too loose to catch a real stall. Measuring a
series against its peers needs no trading calendar, and it is exactly the signal that failed in
that incident, where everything fell behind together.

**Why the median peer, not the freshest one.** Measured against the *freshest* series, a single
24/7 instrument poisons the comparison: run against live data on a Sunday, Bitcoin was an hour old
and it flagged 25 of 50 series, every one of them a market that was merely closed for the weekend.
The median absorbs both a 24/7 outlier and a lone straggler, so what stands out is a series that
has genuinely stopped while its peers carried on.

This is a heuristic and is presented as one. Proper trading calendars belong with instrument
metadata in the processing layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

#: Nothing collected anywhere for this long means collection has stopped, whatever the calendar:
#: at least one instrument in any reasonable set trades often enough to beat it.
GLOBAL_STALE_AFTER = timedelta(hours=12)

#: How far behind the median series of its interval one series may fall before it is flagged.
#: Generous on purpose: exchanges close at different times, and Tokyo can trail New York by most of
#: a day without anything being wrong.
BEHIND_TOLERANCE: dict[str, timedelta] = {"1h": timedelta(hours=12)}
DEFAULT_BEHIND_TOLERANCE = timedelta(days=2)

OK = "ok"
BEHIND = "behind"

_ASSESSED_COLUMNS = ["age", "behind_by", "state"]


@dataclass(frozen=True, slots=True)
class Overall:
    """Whether the collection as a whole is still moving."""

    newest: pd.Timestamp | None
    age: timedelta | None
    is_stale: bool


def tolerance(bar_interval: str) -> timedelta:
    """How far behind the median peer a series of this interval may fall before it is flagged."""
    return BEHIND_TOLERANCE.get(bar_interval, DEFAULT_BEHIND_TOLERANCE)


def humanise(age: timedelta | None) -> str:
    """A compact age for a metric tile: `2d 3h`, `5h 12m`, `41m`."""
    if age is None:
        return "n/a"
    # A clock skew between the database and this process should not print a negative age.
    seconds = max(int(age.total_seconds()), 0)
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def overall(coverage: pd.DataFrame, *, now: pd.Timestamp) -> Overall:
    """Age of the newest bar anywhere. This is the "did collection stop?" signal."""
    if coverage.empty or "last_ts" not in coverage:
        return Overall(newest=None, age=None, is_stale=False)
    stamps = pd.to_datetime(coverage["last_ts"], utc=True).dropna()
    if stamps.empty:
        return Overall(newest=None, age=None, is_stale=False)
    newest = pd.Timestamp(stamps.max())
    age = now - newest
    return Overall(newest=newest, age=age, is_stale=age > GLOBAL_STALE_AFTER)


def assess(coverage: pd.DataFrame, *, now: pd.Timestamp) -> pd.DataFrame:
    """Coverage with `age`, `behind_by` and `state` added, one row per series.

    `behind_by` is measured against the *median* series of the same interval, so an hourly series is
    never judged against a daily one, and a single 24/7 instrument cannot make every closed market
    look stalled. A series fresher than the median gets a negative `behind_by` and is never flagged.
    A lone series is its own median and so is never flagged — with nothing to compare against,
    silence is the honest answer.
    """
    if coverage.empty or not {"last_ts", "bar_interval"} <= set(coverage.columns):
        empty = coverage.copy()
        for column in _ASSESSED_COLUMNS:
            empty[column] = pd.Series(dtype="object")
        return empty

    frame = coverage.copy()
    last = pd.to_datetime(frame["last_ts"], utc=True)
    reference = last.groupby(frame["bar_interval"]).transform("median")

    frame["age"] = now - last
    frame["behind_by"] = reference - last
    allowed = frame["bar_interval"].map(tolerance)
    frame["state"] = (frame["behind_by"] > allowed).map({True: BEHIND, False: OK})
    return frame


def behind_series(assessed: pd.DataFrame) -> list[str]:
    """Symbols flagged as behind their peers, for a message that names them."""
    if assessed.empty or "state" not in assessed:
        return []
    return [str(symbol) for symbol in assessed.loc[assessed["state"] == BEHIND, "symbol"]]
