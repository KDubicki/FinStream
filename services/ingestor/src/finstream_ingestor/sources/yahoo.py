"""Yahoo Finance source: fetch, classify, retry, normalise.

Behaviour verified in docs/research/2026-09-16-yfinance-behaviour.md (R1). The two facts that
shape this module:

* yfinance hides exceptions by default and returns an empty DataFrame instead, so failures
  would be indistinguishable from a closed market. `configure_yfinance()` turns that off.
* `YFRateLimitError` always propagates, while the missing-data errors depend on that setting.

Normalisation stays inside the EL boundary (GR-1): rename, cast, convert to UTC, NaN to None.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf
from curl_cffi import CurlError
from curl_cffi.requests.exceptions import RequestException as CurlRequestException
from requests.exceptions import RequestException
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from yfinance.exceptions import (
    YFInvalidPeriodError,
    YFPricesMissingError,
    YFRateLimitError,
    YFTickerMissingError,
    YFTzMissingError,
)

from finstream_ingestor.sources.base import (
    PermanentSourceError,
    PriceBar,
    RateLimitedError,
    SourceError,
    TransientSourceError,
)

logger = logging.getLogger(__name__)

#: Value stored in `raw.market_prices.source`.
SOURCE_NAME = "yahoo"

#: yfinance column names (with auto-adjust disabled) mapped to our column names.
COLUMN_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}
FLOAT_FIELDS = ("open", "high", "low", "close", "adj_close")

#: Network-level failures. Worth another attempt.
TRANSIENT_EXCEPTIONS = (CurlRequestException, CurlError, RequestException, OSError)
#: Wrong input rather than bad luck: retrying would just repeat the same answer.
PERMANENT_EXCEPTIONS = (YFTzMissingError, YFTickerMissingError, YFInvalidPeriodError)


def configure_yfinance() -> None:
    """Make yfinance raise instead of logging and returning an empty frame (research R1)."""
    yf.config.debug.hide_exceptions = False


def build_retryer(max_attempts: int, max_wait_seconds: int) -> Retrying:
    """Retry policy for source calls: transient failures only (GR-3).

    Built from settings rather than hardcoded in a decorator, so it stays configurable (GR-4)
    and tests can run it with zero wait.
    """
    return Retrying(
        retry=retry_if_exception_type((TransientSourceError, RateLimitedError)),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=1, max=max_wait_seconds),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _to_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


class YahooSource:
    """Fetches OHLCV bars for one symbol at a time."""

    name = SOURCE_NAME

    def __init__(self, retryer: Retrying | None = None) -> None:
        configure_yfinance()
        self._retryer = retryer

    def fetch(self, symbol: str, *, interval: str, period: str) -> list[PriceBar]:
        """Return the symbol's bars, retrying transient failures if a retryer was supplied."""

        def call() -> pd.DataFrame:
            return self._download(symbol, interval=interval, period=period)

        frame = self._retryer(call) if self._retryer is not None else call()
        bars = self._normalise(frame, symbol=symbol, interval=interval)
        logger.info(
            "fetched bars",
            extra={"source": self.name, "symbol": symbol, "interval": interval, "bars": len(bars)},
        )
        return bars

    def _download(self, symbol: str, *, interval: str, period: str) -> pd.DataFrame:
        try:
            # auto_adjust=False keeps both `close` and `adj_close`; actions=False keeps the
            # frame to OHLCV, since corporate actions are not part of this plan.
            # yfinance is untyped, so bind the result before returning it.
            frame: pd.DataFrame = yf.Ticker(symbol).history(
                period=period, interval=interval, auto_adjust=False, actions=False
            )
            return frame
        except YFRateLimitError as exc:
            raise RateLimitedError(f"{symbol}: rate limited by Yahoo") from exc
        except YFPricesMissingError:
            # Not an error: a closed market, a holiday, or a window with no trading.
            logger.warning(
                "no price data returned",
                extra={"source": self.name, "symbol": symbol, "interval": interval},
            )
            return pd.DataFrame()
        except PERMANENT_EXCEPTIONS as exc:
            raise PermanentSourceError(f"{symbol}: {type(exc).__name__}: {exc}") from exc
        except TRANSIENT_EXCEPTIONS as exc:
            raise TransientSourceError(f"{symbol}: {type(exc).__name__}: {exc}") from exc

    def _normalise(self, frame: pd.DataFrame, *, symbol: str, interval: str) -> list[PriceBar]:
        """Turn the DataFrame into PriceBars. Only EL-allowed operations (GR-1)."""
        if frame is None or frame.empty:
            return []

        index = frame.index
        if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
            # Localising here would invent information, so refuse instead (research R1 says the
            # index is always exchange-local and timezone-aware).
            raise SourceError(f"{symbol}: yfinance returned timestamps without a timezone")

        renamed = frame.rename(columns=COLUMN_MAP)
        timestamps = index.tz_convert("UTC")

        bars: list[PriceBar] = []
        for timestamp, record in zip(timestamps, renamed.to_dict(orient="records"), strict=True):
            values: dict[str, Any] = {name: _to_float(record.get(name)) for name in FLOAT_FIELDS}
            values["volume"] = _to_int(record.get("volume"))
            if all(value is None for value in values.values()):
                continue  # a row with nothing in it carries no information
            bars.append(
                PriceBar(
                    source=self.name,
                    symbol=symbol,
                    bar_interval=interval,
                    ts=timestamp.to_pydatetime(),
                    **values,
                )
            )
        return bars
