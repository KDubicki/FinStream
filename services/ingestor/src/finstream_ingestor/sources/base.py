"""Types shared by every price source.

A source turns an external API's response into `PriceBar` objects. It never touches the
database, and it never transforms data beyond the EL boundary (AGENTS.md GR-1, ADR-0001).

The error hierarchy is what makes GR-3 work: only transient failures are retried, permanent
ones are recorded and moved past, and "no data" is not an error at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One OHLCV bar exactly as the source delivered it.

    `ts` is the bar's start instant and must be timezone-aware; the database stores UTC.
    Missing values stay `None` rather than being filled in (GR-1).
    """

    source: str
    symbol: str
    bar_interval: str
    ts: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adj_close: float | None = None
    volume: int | None = None

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise ValueError(
                f"PriceBar.ts must be timezone-aware, got {self.ts!r} for {self.symbol}"
            )


class SourceError(Exception):
    """Base class for every failure a price source reports."""


class TransientSourceError(SourceError):
    """A failure worth retrying: a timeout, a dropped connection, a 5xx."""


class RateLimitedError(TransientSourceError):
    """The source asked us to slow down. Retried, with backoff."""


class PermanentSourceError(SourceError):
    """Retrying cannot help: an unknown or delisted symbol, an invalid request."""


@runtime_checkable
class PriceSource(Protocol):
    """What a job needs from any source, so jobs never depend on a specific API."""

    #: Stable identifier stored in `raw.market_prices.source`, e.g. "yahoo".
    name: str

    def fetch(self, symbol: str, *, interval: str, period: str) -> list[PriceBar]:
        """Return the bars for one symbol, or an empty list when the source has no data.

        Raises `RateLimitedError` or `TransientSourceError` for failures worth retrying,
        and `PermanentSourceError` when retrying cannot help.
        """
        ...
