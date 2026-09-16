"""Chart construction, kept out of the page so it can be tested without Streamlit.

The important choice here is `zero=False`. A price series near 80 plotted against a zero
baseline is a flat line: the axis has to follow the data, not the origin.
"""

from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd

#: How much of the data to show when the page first opens, if more than this exists.
DEFAULT_WINDOW_DAYS = 30
#: Padding above and below the series, as a fraction of its range.
PRICE_PADDING = 0.05


def as_date(value: object) -> date | None:
    """Convert a loosely typed timestamp cell to a date, or None when it is not one.

    Values arrive from DataFrame cells, so anything can turn up; a bad value should disable a
    bound rather than raise on the page.
    """
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)  # type: ignore[arg-type]  # cells are loosely typed
    except (TypeError, ValueError):
        return None
    return None if pd.isna(stamp) else stamp.date()


def default_date_range(
    first: object, last: object, *, today: date | None = None
) -> tuple[date, date]:
    """Pick an opening window from the data itself rather than a fixed 90 days.

    An empty or unknown range falls back to the last `DEFAULT_WINDOW_DAYS` up to today.
    """
    today = today or date.today()
    last_day = as_date(last) or today
    first_day = as_date(first) or last_day
    window_start = max(first_day, last_day - timedelta(days=DEFAULT_WINDOW_DAYS))
    return window_start, last_day


def last_valid(series: pd.Series) -> float | None:
    """The most recent non-null value.

    Several indices return a NaN close for the newest intraday bar, and "nan" on a metric tile
    is worse than showing the last real print.
    """
    cleaned = series.dropna()
    return float(cleaned.iloc[-1]) if len(cleaned) else None


def price_domain(frame: pd.DataFrame, columns: list[str]) -> list[float]:
    """A y-axis domain that frames the movement, with a little breathing room."""
    values = pd.concat([frame[column] for column in columns]).dropna()
    if values.empty:
        return [0.0, 1.0]
    low, high = float(values.min()), float(values.max())
    # A perfectly flat series still needs a visible band.
    padding = (abs(low) * PRICE_PADDING or 1.0) if low == high else (high - low) * PRICE_PADDING
    return [low - padding, high + padding]


def needs_adjusted_line(frame: pd.DataFrame) -> bool:
    """Only plot adj_close when it actually differs; otherwise it is a duplicate line."""
    if "adj_close" not in frame or "close" not in frame:
        return False
    both = frame[["close", "adj_close"]].dropna()
    if both.empty:
        return False
    return bool((both["close"] - both["adj_close"]).abs().max() > 1e-9)


def price_chart(frame: pd.DataFrame, *, title: str) -> alt.LayerChart:
    """Close over time, with an optional adjusted-close line and a hover tooltip."""
    columns = ["close"] + (["adj_close"] if needs_adjusted_line(frame) else [])
    long = frame.melt(
        id_vars="ts", value_vars=columns, var_name="series", value_name="price"
    ).dropna(subset=["price"])

    base = alt.Chart(long).encode(
        x=alt.X("ts:T", axis=alt.Axis(title=None, labelOverlap=True, format="%d %b %H:%M")),
        y=alt.Y(
            "price:Q",
            title=None,
            scale=alt.Scale(zero=False, domain=price_domain(frame, columns), nice=False),
            axis=alt.Axis(format=",.2f"),
        ),
        color=alt.Color("series:N", title=None, legend=alt.Legend(orient="top-left")),
    )
    line = base.mark_line(strokeWidth=1.8)
    hover = (
        base.mark_circle(size=55)
        .encode(
            opacity=alt.condition(
                alt.selection_point(on="pointerover", empty=False), alt.value(1), alt.value(0)
            ),
            tooltip=[
                alt.Tooltip("ts:T", title="Time (UTC)", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("price:Q", title="Price", format=",.2f"),
                alt.Tooltip("series:N", title="Series"),
            ],
        )
        .add_params(alt.selection_point(on="pointerover", empty=False))
    )
    chart: alt.LayerChart = (
        (line + hover).properties(height=340, title=title).interactive(bind_y=False)
    )
    return chart


def volume_chart(frame: pd.DataFrame) -> alt.Chart:
    """Volume as bars under the price, sharing the same time axis."""
    chart: alt.Chart = (
        alt.Chart(frame.dropna(subset=["volume"]))
        .mark_bar(opacity=0.65)
        .encode(
            x=alt.X("ts:T", axis=alt.Axis(title=None, labelOverlap=True, format="%d %b")),
            y=alt.Y("volume:Q", title=None, axis=alt.Axis(format="~s")),
            tooltip=[
                alt.Tooltip("ts:T", title="Time (UTC)", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("volume:Q", title="Volume", format=",.0f"),
            ],
        )
        .properties(height=110)
    )
    return chart
