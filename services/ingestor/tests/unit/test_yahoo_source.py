"""The Yahoo source: classification, retries and the EL boundary.

yfinance is always replaced here: automated tests never touch the network (AGENTS.md GR-7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest
import yfinance as yf
from yfinance.exceptions import (
    YFInvalidPeriodError,
    YFPricesMissingError,
    YFRateLimitError,
    YFTzMissingError,
)

from finstream_ingestor.sources import yahoo
from finstream_ingestor.sources.base import (
    PermanentSourceError,
    PriceSource,
    RateLimitedError,
    SourceError,
    TransientSourceError,
)
from finstream_ingestor.sources.yahoo import YahooSource, build_retryer

NEW_YORK = "America/New_York"


def make_frame(rows: int = 2, *, tz: str | None = NEW_YORK, **overrides: Any) -> pd.DataFrame:
    """A frame shaped like yfinance's output with auto_adjust=False."""
    index = pd.date_range("2026-09-15 09:30", periods=rows, freq="h", tz=tz)
    data = {
        "Open": [100.0 + i for i in range(rows)],
        "High": [101.0 + i for i in range(rows)],
        "Low": [99.0 + i for i in range(rows)],
        "Close": [100.5 + i for i in range(rows)],
        "Adj Close": [100.4 + i for i in range(rows)],
        "Volume": [1000 + i for i in range(rows)],
    }
    data.update(overrides)
    return pd.DataFrame(data, index=index)


class FakeTicker:
    """Stands in for yf.Ticker: returns a frame, or raises, and counts calls."""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def __call__(self, symbol: str) -> FakeTicker:
        self.symbol = symbol
        return self

    def history(self, **kwargs: Any) -> pd.DataFrame:
        self.calls += 1
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture
def install_ticker(monkeypatch: pytest.MonkeyPatch):
    def install(result: Any = None, error: BaseException | None = None) -> FakeTicker:
        fake = FakeTicker(result=result, error=error)
        monkeypatch.setattr(yahoo.yf, "Ticker", fake)
        return fake

    return install


def test_source_satisfies_the_protocol() -> None:
    assert isinstance(YahooSource(), PriceSource)


def test_configure_yfinance_turns_exceptions_back_on() -> None:
    yf.config.debug.hide_exceptions = True
    YahooSource()
    assert yf.config.debug.hide_exceptions is False, (
        "hidden exceptions would make failures look like empty results"
    )


def test_fetch_requests_unadjusted_ohlcv(install_ticker) -> None:
    fake = install_ticker(result=make_frame())
    YahooSource().fetch("SPY", interval="1h", period="5d")
    assert fake.kwargs == {
        "period": "5d",
        "interval": "1h",
        "auto_adjust": False,
        "actions": False,
    }


def test_normalisation_converts_to_utc_and_our_column_names(install_ticker) -> None:
    install_ticker(result=make_frame(rows=2))
    bars = YahooSource().fetch("SPY", interval="1h", period="5d")

    assert len(bars) == 2
    first = bars[0]
    assert first.source == "yahoo"
    assert first.symbol == "SPY"
    assert first.bar_interval == "1h"
    # 09:30 in New York is 13:30 UTC on that date.
    assert first.ts == datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    assert first.ts.tzinfo is not None
    assert (first.open, first.high, first.low, first.close, first.adj_close) == (
        100.0,
        101.0,
        99.0,
        100.5,
        100.4,
    )
    assert first.volume == 1000


def test_row_count_is_preserved_no_resampling(install_ticker) -> None:
    install_ticker(result=make_frame(rows=7))
    assert len(YahooSource().fetch("SPY", interval="1h", period="5d")) == 7


def test_missing_values_become_none(install_ticker) -> None:
    frame = make_frame(rows=1)
    frame.loc[frame.index[0], "Volume"] = float("nan")
    frame.loc[frame.index[0], "Adj Close"] = float("nan")
    install_ticker(result=frame)

    bar = YahooSource().fetch("SPY", interval="1h", period="5d")[0]
    assert bar.volume is None
    assert bar.adj_close is None
    assert bar.close == 100.5, "other values must survive untouched"


def test_rows_that_are_entirely_empty_are_dropped(install_ticker) -> None:
    frame = make_frame(rows=2)
    frame.iloc[1] = float("nan")
    install_ticker(result=frame)
    assert len(YahooSource().fetch("SPY", interval="1h", period="5d")) == 1


def test_empty_frame_returns_no_bars(install_ticker) -> None:
    install_ticker(result=pd.DataFrame())
    assert YahooSource().fetch("SPY", interval="1h", period="5d") == []


def test_naive_timestamps_are_refused(install_ticker) -> None:
    """Localising a naive index would invent information, which GR-1 forbids."""
    install_ticker(result=make_frame(tz=None))
    with pytest.raises(SourceError, match="without a timezone"):
        YahooSource().fetch("SPY", interval="1h", period="5d")


def test_missing_prices_are_empty_not_a_failure(install_ticker) -> None:
    """A holiday or a closed market legitimately has no rows."""
    install_ticker(error=YFPricesMissingError("SPY", "no price data found"))
    assert YahooSource().fetch("SPY", interval="1h", period="5d") == []


def test_rate_limit_is_classified_as_retryable(install_ticker) -> None:
    install_ticker(error=YFRateLimitError())
    with pytest.raises(RateLimitedError):
        YahooSource().fetch("SPY", interval="1h", period="5d")


@pytest.mark.parametrize(
    "error",
    [
        YFTzMissingError("NOPE"),
        YFInvalidPeriodError("SPY", "99y", ["1d", "5d"]),
    ],
)
def test_bad_input_is_permanent(install_ticker, error: BaseException) -> None:
    install_ticker(error=error)
    with pytest.raises(PermanentSourceError):
        YahooSource().fetch("SPY", interval="1h", period="5d")


@pytest.mark.parametrize("error", [ConnectionError("reset"), TimeoutError("timed out")])
def test_network_failures_are_transient(install_ticker, error: BaseException) -> None:
    install_ticker(error=error)
    with pytest.raises(TransientSourceError):
        YahooSource().fetch("SPY", interval="1h", period="5d")


def test_transient_failures_are_retried_until_they_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class Flaky(FakeTicker):
        def history(self, **kwargs: Any) -> pd.DataFrame:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("reset")
            return make_frame(rows=1)

    monkeypatch.setattr(yahoo.yf, "Ticker", Flaky())
    source = YahooSource(retryer=build_retryer(max_attempts=5, max_wait_seconds=0))

    assert len(source.fetch("SPY", interval="1h", period="5d")) == 1
    assert attempts == 3


def test_retries_are_exhausted_and_the_error_reaches_the_caller(install_ticker) -> None:
    fake = install_ticker(error=ConnectionError("reset"))
    source = YahooSource(retryer=build_retryer(max_attempts=3, max_wait_seconds=0))

    with pytest.raises(TransientSourceError):
        source.fetch("SPY", interval="1h", period="5d")
    assert fake.calls == 3


def test_permanent_failures_are_not_retried(install_ticker) -> None:
    fake = install_ticker(error=YFTzMissingError("NOPE"))
    source = YahooSource(retryer=build_retryer(max_attempts=5, max_wait_seconds=0))

    with pytest.raises(PermanentSourceError):
        source.fetch("NOPE", interval="1d", period="5d")
    assert fake.calls == 1, "a wrong symbol must fail fast, not burn the retry budget"
