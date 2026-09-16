"""Types shared by every price source.

A source turns an external API's response into `PriceBar` objects. It never touches the
database, and it never transforms data beyond the EL boundary (AGENTS.md GR-1, ADR-0001).
The `PriceSource` protocol and the error hierarchy arrive with the Yahoo source (plan 0001, M3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
