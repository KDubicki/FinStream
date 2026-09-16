"""The Streamlit page.

UI only: every SQL statement lives in `queries.py`. Results are cached with a TTL so moving a
widget does not re-query the database on every rerun.

Run locally:  streamlit run src/finstream_dashboard/app.py
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy.engine import Engine

from finstream_dashboard import queries
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


def render_prices(coverage: pd.DataFrame, settings: Settings) -> None:
    symbols = sorted(coverage["symbol"].unique())
    intervals = sorted(coverage["bar_interval"].unique())
    default_interval = (
        intervals.index(settings.dashboard_default_interval)
        if settings.dashboard_default_interval in intervals
        else 0
    )

    left, middle, right = st.columns([2, 1, 2])
    symbol = left.selectbox("Symbol", symbols)
    bar_interval = middle.selectbox("Interval", intervals, index=default_interval)
    today = datetime.now(UTC).date()
    selected = right.date_input(
        "Date range",
        value=(today - timedelta(days=90), today),
        max_value=today,
    )
    if not isinstance(selected, tuple | list) or len(selected) != 2:
        st.info("Pick an end date to show the range.")
        return
    start, end = selected

    prices = load_prices(
        symbol, bar_interval, start, end + timedelta(days=1), settings.dashboard_max_rows
    )
    if prices.empty:
        st.info(f"No {bar_interval} bars for {symbol} in this range.")
        return

    if len(prices) >= settings.dashboard_max_rows:
        st.warning(
            f"Showing the first {settings.dashboard_max_rows} bars; "
            "narrow the date range to see the rest."
        )

    indexed = prices.set_index("ts")
    st.line_chart(indexed[["close", "adj_close"]], height=320)
    st.bar_chart(indexed["volume"], height=180)

    latest = prices.iloc[-1]
    first_close = prices.iloc[0]["close"]
    change = (latest["close"] - first_close) if pd.notna(latest["close"]) else None
    metrics = st.columns(4)
    metrics[0].metric(
        "Last close", f"{latest['close']:.2f}" if pd.notna(latest["close"]) else "n/a"
    )
    metrics[1].metric(
        "Change over range",
        f"{change:+.2f}" if change is not None else "n/a",
        f"{(change / first_close * 100):+.2f}%" if change is not None and first_close else None,
    )
    metrics[2].metric("Bars shown", len(prices))
    metrics[3].metric("Last bar (UTC)", str(latest["ts"]))

    with st.expander("Most recent bars"):
        st.dataframe(load_latest_bars(symbol, bar_interval), width="stretch")


def render_coverage(coverage: pd.DataFrame) -> None:
    st.caption("What the database holds. Gaps between `last_ts` and now mean ingestion is behind.")
    st.dataframe(coverage, width="stretch")


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
