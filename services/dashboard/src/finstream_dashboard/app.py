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

from finstream_dashboard import charts, instruments, queries
from finstream_dashboard.config import Settings

#: Streamlit needs the cache TTL at decoration time. Reading the environment directly keeps
#: importing this module free of side effects; Settings still validates it for everything else.
CACHE_TTL_SECONDS = int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "60"))

PAGE_TITLE = "FinStream"
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


def render_coverage(coverage: pd.DataFrame) -> None:
    st.caption("What the database holds. A `last_ts` far behind now means ingestion is behind.")
    table = coverage.copy()
    table.insert(0, "instrument", [instruments.describe(s).name for s in table["symbol"]])
    table.insert(1, "class", [instruments.describe(s).asset_class for s in table["symbol"]])
    st.dataframe(table, width="stretch")


def render_health() -> None:
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

    prices_tab, coverage_tab, health_tab = st.tabs(["Prices", "Coverage", "Ingestion health"])
    with prices_tab:
        render_prices(coverage, settings)
    with coverage_tab:
        render_coverage(coverage)
    with health_tab:
        render_health()


if __name__ == "__main__":
    main()
