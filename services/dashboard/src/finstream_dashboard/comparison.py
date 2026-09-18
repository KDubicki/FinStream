"""Putting several instruments on one comparable scale.

Gold near 4,400 and silver near 52 cannot be read against each other on a price axis: the cheaper
series is a flat line at the bottom. Rebasing each series to a baseline turns both into percentage
changes, and the baseline is the **first bar the selected series have in common**, so a longer
history is not a head start. Bars before that moment are kept and simply sit off the 0% line.

This is display-time arithmetic over bars read out of `raw`. Nothing here is written back and the
Ingestor's extract-and-load boundary is untouched (GR-1, ADR-0005); it is the same kind of
computation as the "change over range" metric the Prices tab already shows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

#: Lines one percentage axis stays readable with. A UI cap, not deployment configuration.
MAX_COMPARE_SYMBOLS = 5
#: There is nothing to compare below this.
MIN_COMPARE_SYMBOLS = 2

#: The column every comparison is built from. Close is the only price every source reports.
PRICE_COLUMN = "close"

_BAR_COLUMNS = ["ts", PRICE_COLUMN]
_LONG_COLUMNS = ["ts", "symbol", "close", "percent"]


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    """One instrument's headline numbers over the compared window."""

    symbol: str
    baseline: float
    last_close: float
    percent_change: float


@dataclass(frozen=True, slots=True)
class Comparison:
    """Everything the page needs to draw and caption a comparison.

    `frame` is long-form (`ts`, `symbol`, `close`, `percent`), which is what Altair wants for a
    colour-encoded multi-series chart.
    """

    start: pd.Timestamp | None
    frame: pd.DataFrame
    summaries: tuple[SeriesSummary, ...]
    dropped: tuple[str, ...]

    @property
    def leader(self) -> SeriesSummary | None:
        """The instrument that gained most over the window, or None when nothing is comparable."""
        if not self.summaries:
            return None
        return max(self.summaries, key=lambda summary: summary.percent_change)


def _bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Just the timestamp and close, UTC-aware and in time order.

    Frames arrive from DataFrame cells and from tests, so the dtypes cannot be assumed. Sorting
    here is what makes "first" and "last" mean anything below.
    """
    if frame.empty or not set(_BAR_COLUMNS) <= set(frame.columns):
        return pd.DataFrame(
            {
                "ts": pd.Series(dtype="datetime64[ns, UTC]"),
                PRICE_COLUMN: pd.Series(dtype="float64"),
            }
        )
    bars = frame.loc[:, _BAR_COLUMNS].copy()
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars[PRICE_COLUMN] = pd.to_numeric(bars[PRICE_COLUMN], errors="coerce")
    return bars.sort_values("ts", ignore_index=True)


def first_valid_ts(frame: pd.DataFrame) -> pd.Timestamp | None:
    """When this series' usable history starts, ignoring leading gaps in the close."""
    priced = _bars(frame).dropna(subset=[PRICE_COLUMN])
    return None if priced.empty else pd.Timestamp(priced.iloc[0]["ts"])


def common_start(frames: Mapping[str, pd.DataFrame]) -> pd.Timestamp | None:
    """The earliest moment every usable series has a price for.

    Series with no usable price at all are ignored rather than allowed to veto the start: they are
    dropped from the comparison anyway, and letting them push the baseline later would shrink the
    window for no reason.
    """
    starts = [ts for ts in (first_valid_ts(frame) for frame in frames.values()) if ts is not None]
    return max(starts) if starts else None


def baseline_close(frame: pd.DataFrame, start: pd.Timestamp | None) -> float | None:
    """The price that counts as 0% for this series: its first real close at or after `start`.

    None when the series has nothing usable there, or when the baseline is zero — a percentage
    change from zero has no meaning, so the series is dropped instead of yielding infinities.
    """
    priced = _bars(frame).dropna(subset=[PRICE_COLUMN])
    if start is not None:
        priced = priced[priced["ts"] >= start]
    if priced.empty:
        return None
    baseline = float(priced.iloc[0][PRICE_COLUMN])
    return baseline if baseline != 0.0 else None


def compare(frames: Mapping[str, pd.DataFrame]) -> Comparison:
    """Rebase every series onto the shared baseline and summarise the result.

    Series that cannot be rebased are named in `dropped` so the page can say which instrument it
    left out and why, instead of quietly drawing fewer lines than were asked for.
    """
    bars = {symbol: _bars(frame) for symbol, frame in frames.items()}
    start = common_start(bars)

    parts: list[pd.DataFrame] = []
    summaries: list[SeriesSummary] = []
    dropped: list[str] = []

    for symbol, frame in bars.items():
        baseline = baseline_close(frame, start)
        if baseline is None:
            dropped.append(symbol)
            continue
        part = frame.dropna(subset=[PRICE_COLUMN]).copy()
        part["symbol"] = symbol
        part["percent"] = (part[PRICE_COLUMN] / baseline - 1.0) * 100.0
        parts.append(part.loc[:, _LONG_COLUMNS])

        last = part.iloc[-1]
        summaries.append(
            SeriesSummary(
                symbol=symbol,
                baseline=baseline,
                last_close=float(last[PRICE_COLUMN]),
                percent_change=float(last["percent"]),
            )
        )

    frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=_LONG_COLUMNS)
    return Comparison(start=start, frame=frame, summaries=tuple(summaries), dropped=tuple(dropped))


def ratio_series(numerator: pd.DataFrame, denominator: pd.DataFrame) -> pd.DataFrame:
    """One instrument priced in another, e.g. the gold/silver ratio. Columns: `ts`, `ratio`.

    Unlike the percentage chart this needs both prices at the *same* instant, so it inner-joins on
    the timestamp. Two series with disjoint stamps — a 24/7 crypto series against an index — give
    an empty frame, which the page reports rather than drawing as a blank panel.
    """
    left = _bars(numerator).dropna(subset=[PRICE_COLUMN])
    right = _bars(denominator).dropna(subset=[PRICE_COLUMN])
    merged = left.merge(right, on="ts", suffixes=("_num", "_den"))
    merged = merged[merged[f"{PRICE_COLUMN}_den"] != 0.0]
    if merged.empty:
        return pd.DataFrame(
            {"ts": pd.Series(dtype="datetime64[ns, UTC]"), "ratio": pd.Series(dtype="float64")}
        )
    return pd.DataFrame(
        {
            "ts": merged["ts"],
            "ratio": merged[f"{PRICE_COLUMN}_num"] / merged[f"{PRICE_COLUMN}_den"],
        }
    )
