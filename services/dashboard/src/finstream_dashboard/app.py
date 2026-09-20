"""The Streamlit page.

UI only: SQL lives in `queries.py`, chart construction in `charts.py`, and display names in
`instruments.py`. Results are cached with a TTL so moving a widget does not re-query the database.

Run locally:  streamlit run src/finstream_dashboard/app.py
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy.engine import Engine

from finstream_dashboard import charts, comparison, freshness, instruments, queries
from finstream_dashboard.config import Settings

#: Streamlit needs the cache TTL at decoration time. Reading the environment directly keeps
#: importing this module free of side effects; Settings still validates it for everything else.
CACHE_TTL_SECONDS = int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "60"))

PAGE_TITLE = "FinStream"
#: Opening pair on the Compare tab: the gold-versus-silver question this tab was built for.
DEFAULT_COMPARE_SYMBOLS = ("GC=F", "SI=F")
EMPTY_DATABASE_HINT = (
    "No bars stored yet. The Ingestor writes to `raw.market_prices` on its schedule; "
    "start it with `docker compose up -d ingestor` and check its logs if this stays empty."
)


@st.cache_resource
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


@st.cache_resource
def get_engine() -> Engine:
    settings = get_settings()
    return queries.create_read_engine(settings.database_url.get_secret_value())


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_coverage() -> pd.DataFrame:
    return queries.coverage(get_engine())


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_prices(
    symbol: str, bar_interval: str, start: date, end: date, max_rows: int
) -> pd.DataFrame:
    return queries.price_history(
        get_engine(),
        symbol=symbol,
        bar_interval=bar_interval,
        start=start,
        end=end,
        max_rows=max_rows,
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_latest_bars(symbol: str, bar_interval: str) -> pd.DataFrame:
    return queries.latest_bars(get_engine(), symbol=symbol, bar_interval=bar_interval)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_runs() -> pd.DataFrame:
    return queries.recent_runs(get_engine())


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_run_status_counts() -> pd.DataFrame:
    return queries.run_status_counts(get_engine())


def series_span(coverage: pd.DataFrame, symbol: str, bar_interval: str) -> tuple[object, object]:
    """First and last timestamp stored for one series, so the date range can follow the data."""
    row = coverage[(coverage["symbol"] == symbol) & (coverage["bar_interval"] == bar_interval)]
    if row.empty:
        return None, None
    return row.iloc[0]["first_ts"], row.iloc[0]["last_ts"]


def render_prices(coverage: pd.DataFrame, settings: Settings) -> None:
    symbols = instruments.ordered_symbols(coverage["symbol"].unique())
    intervals = sorted(coverage["bar_interval"].unique())
    default_interval = (
        intervals.index(settings.dashboard_default_interval)
        if settings.dashboard_default_interval in intervals
        else 0
    )

    left, middle = st.columns([3, 1])
    symbol = left.selectbox(
        "Instrument",
        symbols,
        format_func=lambda s: f"{instruments.describe(s).asset_class} — {instruments.label(s)}",
    )
    bar_interval = middle.selectbox("Interval", intervals, index=default_interval)

    first_ts, last_ts = series_span(coverage, symbol, bar_interval)
    window_start, window_end = charts.default_date_range(first_ts, last_ts)
    selected = st.date_input(
        "Date range",
        value=(window_start, window_end),
        min_value=charts.as_date(first_ts),
        max_value=window_end,
    )
    if not isinstance(selected, tuple | list) or len(selected) != 2:
        st.info("Pick an end date to show the range.")
        return
    start, end = selected

    prices = load_prices(
        symbol, bar_interval, start, end + timedelta(days=1), settings.dashboard_max_rows
    )
    if prices.empty:
        st.info(f"No {bar_interval} bars for {instruments.label(symbol)} in this range.")
        return

    if len(prices) >= settings.dashboard_max_rows:
        st.warning(
            f"Showing the first {settings.dashboard_max_rows} bars; "
            "narrow the date range to see the rest."
        )

    instrument = instruments.describe(symbol)
    last_close = charts.last_valid(prices["close"])
    first_close = charts.last_valid(prices["close"].iloc[::-1])
    change = (last_close - first_close) if last_close is not None and first_close else None

    metrics = st.columns(4)
    metrics[0].metric("Last close", f"{last_close:,.2f}" if last_close is not None else "n/a")
    metrics[1].metric(
        "Change over range",
        f"{change:+,.2f}" if change is not None else "n/a",
        f"{(change / first_close * 100):+.2f}%" if change and first_close else None,
    )
    metrics[2].metric("Bars shown", f"{len(prices):,}")
    metrics[3].metric(
        "Last bar (UTC)",
        pd.Timestamp(prices["ts"].iloc[-1]).strftime("%Y-%m-%d %H:%M"),
    )

    st.altair_chart(
        charts.price_chart(prices, title=f"{instrument.name} ({symbol}) · {bar_interval}"),
        use_container_width=True,
    )
    st.caption("Volume")
    st.altair_chart(charts.volume_chart(prices), use_container_width=True)

    with st.expander("Most recent bars"):
        st.dataframe(load_latest_bars(symbol, bar_interval), width="stretch")


def union_span(
    coverage: pd.DataFrame, symbols: list[str], bar_interval: str
) -> tuple[object, object]:
    """The widest span across several series, so the date range covers everything selected."""
    rows = coverage[coverage["symbol"].isin(symbols) & (coverage["bar_interval"] == bar_interval)]
    if rows.empty:
        return None, None
    return rows["first_ts"].min(), rows["last_ts"].max()


def default_compare_selection(symbols: list[str]) -> list[str]:
    """Open on gold vs silver when both are collected; otherwise just the first two instruments."""
    preset = [symbol for symbol in DEFAULT_COMPARE_SYMBOLS if symbol in symbols]
    if len(preset) == comparison.MIN_COMPARE_SYMBOLS:
        return preset
    return symbols[: comparison.MIN_COMPARE_SYMBOLS]


def limit_to_readable(symbols: list[str]) -> tuple[list[str], list[str]]:
    """Split a selection into the lines that fit on one axis and the extras that do not."""
    cap = comparison.MAX_COMPARE_SYMBOLS
    return symbols[:cap], symbols[cap:]


def render_ratio(frames: dict[str, pd.DataFrame], symbols: list[str]) -> None:
    """One instrument priced in the other: the direct answer to "which of the two to hold"."""
    numerator, denominator = symbols
    top = instruments.describe(numerator).name
    bottom = instruments.describe(denominator).name
    st.subheader(f"{top} / {bottom} ratio")

    ratio = comparison.ratio_series(frames[numerator], frames[denominator])
    if ratio.empty:
        st.info(
            f"{top} and {bottom} have no bars at the same timestamps in this range, so a ratio "
            "cannot be formed. Try the other interval, or two instruments with the same "
            "trading calendar."
        )
        return

    values = ratio["ratio"]
    first, current = float(values.iloc[0]), float(values.iloc[-1])
    tiles = st.columns(3)
    tiles[0].metric(
        "Current", f"{current:,.3f}", f"{(current - first):+,.3f} over the range", delta_color="off"
    )
    tiles[1].metric("Range low", f"{float(values.min()):,.3f}")
    tiles[2].metric("Range high", f"{float(values.max()):,.3f}")

    if current > first:
        st.caption(f"Rising: {top} has been gaining on {bottom}.")
    elif current < first:
        st.caption(f"Falling: {bottom} has been gaining on {top}.")
    else:
        st.caption(f"Flat: {top} and {bottom} ended the range where they started.")

    st.altair_chart(charts.ratio_chart(ratio), use_container_width=True)


def render_compare(coverage: pd.DataFrame, settings: Settings) -> None:
    """Several instruments on one percentage scale, rebased to their first common bar."""
    st.caption(
        "Percentage change from the first bar the selected instruments have in common. This is "
        "past movement of stored bars, each in its own quote currency: no FX adjustment is made."
    )

    symbols = instruments.ordered_symbols(coverage["symbol"].unique())
    intervals = sorted(coverage["bar_interval"].unique())
    default_interval = (
        intervals.index(settings.dashboard_default_interval)
        if settings.dashboard_default_interval in intervals
        else 0
    )

    left, middle = st.columns([3, 1])
    selected_symbols = left.multiselect(
        "Instruments",
        symbols,
        default=default_compare_selection(symbols),
        format_func=instruments.label,
        key="compare_symbols",
    )
    bar_interval = middle.selectbox(
        "Interval", intervals, index=default_interval, key="compare_interval"
    )

    if len(selected_symbols) < comparison.MIN_COMPARE_SYMBOLS:
        st.info(f"Pick at least {comparison.MIN_COMPARE_SYMBOLS} instruments to compare.")
        return
    selected_symbols, too_many = limit_to_readable(selected_symbols)
    if too_many:
        st.warning(
            f"Comparing the first {comparison.MAX_COMPARE_SYMBOLS}; more lines than that on one "
            "axis stop being readable. Left out "
            + ", ".join(instruments.label(symbol) for symbol in too_many)
            + "."
        )

    first_ts, last_ts = union_span(coverage, selected_symbols, bar_interval)
    window_start, window_end = charts.default_date_range(first_ts, last_ts)
    selected = st.date_input(
        "Date range",
        value=(window_start, window_end),
        min_value=charts.as_date(first_ts),
        max_value=window_end,
        key="compare_range",
    )
    if not isinstance(selected, tuple | list) or len(selected) != 2:
        st.info("Pick an end date to show the range.")
        return
    start, end = selected

    loaded = {
        symbol: load_prices(
            symbol, bar_interval, start, end + timedelta(days=1), settings.dashboard_max_rows
        )
        for symbol in selected_symbols
    }
    frames = {symbol: frame for symbol, frame in loaded.items() if not frame.empty}
    empty = [symbol for symbol in loaded if symbol not in frames]
    if empty:
        st.warning(
            f"No {bar_interval} bars in this range for "
            + ", ".join(instruments.label(symbol) for symbol in empty)
            + "."
        )
    if len(frames) < comparison.MIN_COMPARE_SYMBOLS:
        st.info("Not enough instruments with data in this range to compare.")
        return
    if any(len(frame) >= settings.dashboard_max_rows for frame in frames.values()):
        st.warning(
            f"At least one series hit the {settings.dashboard_max_rows}-bar limit; "
            "narrow the date range to compare the full window."
        )

    result = comparison.compare(frames)
    if result.dropped:
        st.warning(
            "Left out "
            + ", ".join(instruments.label(symbol) for symbol in result.dropped)
            + ": no usable close from the common baseline onwards."
        )
    leader = result.leader
    if result.frame.empty or leader is None or result.start is None:
        st.info("Nothing comparable in this range.")
        return

    tiles = st.columns(len(result.summaries))
    for tile, summary in zip(tiles, result.summaries, strict=True):
        tile.metric(
            instruments.describe(summary.symbol).name,
            f"{summary.last_close:,.2f}",
            f"{summary.percent_change:+.2f}%",
        )

    baseline_at = pd.Timestamp(result.start).strftime("%Y-%m-%d %H:%M")
    st.markdown(
        f"**{instruments.describe(leader.symbol).name}** leads this window at "
        f"{leader.percent_change:+.2f}%, measured from the first bar all "
        f"{len(result.summaries)} instruments share ({baseline_at} UTC)."
    )

    st.altair_chart(
        charts.percent_change_chart(
            result.frame,
            labels={symbol: instruments.label(symbol) for symbol in frames},
            title=f"% change · {bar_interval}",
        ),
        use_container_width=True,
    )

    compared = [summary.symbol for summary in result.summaries]
    if len(compared) == comparison.MIN_COMPARE_SYMBOLS:
        render_ratio(frames, compared)


def render_freshness_banner(coverage: pd.DataFrame) -> None:
    """Say it on the page when collection has stopped, instead of leaving it in the logs.

    Rendered above the tabs so a stall is visible whichever tab is open — the failure this guards
    against reported `success` on every run while the data silently aged.
    """
    now = pd.Timestamp.now(tz="UTC")
    state = freshness.overall(coverage, now=now)
    if state.age is None:
        return

    age = freshness.humanise(state.age)
    if state.is_stale:
        st.error(
            f"The newest bar anywhere is {age} old. Collection appears to have stopped — check "
            "the Ingestion health tab and the ingestor's logs."
        )
        return

    behind = freshness.behind_series(freshness.assess(coverage, now=now))
    if behind:
        st.warning(
            "Collection is running, but these series are behind their peers: "
            + ", ".join(instruments.label(symbol) for symbol in sorted(set(behind)))
            + "."
        )


def render_coverage(coverage: pd.DataFrame) -> None:
    st.caption(
        "What the database holds. `state` compares a series with the freshest series of the same "
        "interval, so it needs no trading calendar — but it is a heuristic, not a market clock."
    )
    table = freshness.assess(coverage, now=pd.Timestamp.now(tz="UTC"))
    table.insert(0, "instrument", [instruments.describe(s).name for s in table["symbol"]])
    table.insert(1, "class", [instruments.describe(s).asset_class for s in table["symbol"]])
    table["age"] = [freshness.humanise(value) for value in table["age"]]
    table["behind_by"] = [freshness.humanise(value) for value in table["behind_by"]]
    st.dataframe(table, width="stretch")


def render_health(coverage: pd.DataFrame | None = None) -> None:
    if coverage is not None and not coverage.empty:
        state = freshness.overall(coverage, now=pd.Timestamp.now(tz="UTC"))
        st.metric(
            "Newest bar anywhere",
            freshness.humanise(state.age),
            "collection stopped" if state.is_stale else None,
            delta_color="inverse",
        )

    counts = load_run_status_counts()
    if counts.empty:
        st.info("No ingestion runs recorded in the last 24 hours.")
    else:
        columns = st.columns(len(counts))
        for column, row in zip(columns, counts.to_dict(orient="records"), strict=True):
            column.metric(str(row["status"]), int(row["runs"]))

    runs = load_runs()
    if runs.empty:
        st.info("The Ingestor has not recorded any runs yet.")
        return
    failed = runs[runs["status"] == "failed"]
    if not failed.empty:
        st.error(f"{len(failed)} failed run(s) in the most recent {len(runs)}.")
    st.dataframe(runs, width="stretch")


def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon="📈", layout="wide")
    st.title("FinStream")
    st.caption("Raw market data collected by the FinStream Ingestor. This view is read-only.")

    settings = get_settings()
    coverage = load_coverage()

    if coverage.empty:
        st.warning(EMPTY_DATABASE_HINT)
        st.subheader("Ingestion health")
        render_health()
        return

    render_freshness_banner(coverage)

    prices_tab, compare_tab, coverage_tab, health_tab = st.tabs(
        ["Prices", "Compare", "Coverage", "Ingestion health"]
    )
    with prices_tab:
        render_prices(coverage, settings)
    with compare_tab:
        render_compare(coverage, settings)
    with coverage_tab:
        render_coverage(coverage)
    with health_tab:
        render_health(coverage)


if __name__ == "__main__":
    main()
