"""Display metadata for the instruments we collect.

Names and asset classes exist so the page can say "S&P 500" instead of "^GSPC". This is
presentation only: nothing here is written to the database, and the stored data keeps the
source's own symbols (GR-1, ADR-0005). An unknown symbol still renders, using the bare ticker.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

METALS = "Metals"
ENERGY = "Energy"
INDICES = "Indices"
ETFS = "ETFs"
FX = "FX"
CRYPTO = "Crypto"
RATES = "Rates"
OTHER = "Other"

#: Order the asset classes appear in on the page.
ASSET_CLASS_ORDER = (METALS, ENERGY, INDICES, ETFS, FX, CRYPTO, RATES, OTHER)


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    asset_class: str

    @property
    def label(self) -> str:
        """What the picker shows, e.g. 'S&P 500 · ^GSPC'."""
        return f"{self.name} · {self.symbol}"


#: Every entry was checked against Yahoo on 2026-09-17 (plan 0004).
INSTRUMENTS: dict[str, Instrument] = {
    instrument.symbol: instrument
    for instrument in (
        Instrument("GC=F", "Gold futures", METALS),
        Instrument("SI=F", "Silver futures", METALS),
        Instrument("HG=F", "Copper futures", METALS),
        Instrument("GLD", "SPDR Gold Shares", METALS),
        Instrument("IAU", "iShares Gold Trust", METALS),
        Instrument("SLV", "iShares Silver Trust", METALS),
        Instrument("CL=F", "WTI crude oil", ENERGY),
        Instrument("BZ=F", "Brent crude oil", ENERGY),
        Instrument("NG=F", "Natural gas", ENERGY),
        Instrument("^GSPC", "S&P 500", INDICES),
        Instrument("^IXIC", "Nasdaq Composite", INDICES),
        Instrument("^DJI", "Dow Jones Industrial Average", INDICES),
        Instrument("^RUT", "Russell 2000", INDICES),
        Instrument("^VIX", "VIX volatility index", INDICES),
        Instrument("^FTSE", "FTSE 100", INDICES),
        Instrument("^STOXX50E", "Euro Stoxx 50", INDICES),
        Instrument("^GDAXI", "DAX", INDICES),
        Instrument("^N225", "Nikkei 225", INDICES),
        Instrument("WIG20.WA", "WIG20", INDICES),
        Instrument("SPY", "S&P 500 ETF", ETFS),
        Instrument("VOO", "Vanguard S&P 500 ETF", ETFS),
        Instrument("QQQ", "Nasdaq 100 ETF", ETFS),
        Instrument("IWM", "Russell 2000 ETF", ETFS),
        Instrument("DIA", "Dow Jones ETF", ETFS),
        Instrument("TLT", "20+ year Treasury ETF", ETFS),
        Instrument("EURUSD=X", "EUR/USD", FX),
        Instrument("USDPLN=X", "USD/PLN", FX),
        Instrument("EURPLN=X", "EUR/PLN", FX),
        Instrument("DX-Y.NYB", "US dollar index", FX),
        Instrument("BTC-USD", "Bitcoin", CRYPTO),
        Instrument("ETH-USD", "Ethereum", CRYPTO),
        Instrument("^TNX", "US 10-year Treasury yield", RATES),
    )
}


def describe(symbol: str) -> Instrument:
    """Metadata for a symbol, falling back to the bare ticker for anything unknown."""
    return INSTRUMENTS.get(symbol, Instrument(symbol, symbol, OTHER))


def label(symbol: str) -> str:
    return describe(symbol).label


def group_by_asset_class(symbols: Iterable[str]) -> dict[str, list[str]]:
    """Symbols grouped for the picker, in a stable order with names sorted inside each class."""
    grouped: dict[str, list[str]] = {}
    for symbol in symbols:
        grouped.setdefault(describe(symbol).asset_class, []).append(symbol)
    return {
        asset_class: sorted(grouped[asset_class], key=lambda s: describe(s).name)
        for asset_class in ASSET_CLASS_ORDER
        if asset_class in grouped
    }


def ordered_symbols(symbols: Iterable[str]) -> list[str]:
    """All symbols, ordered by asset class and then by name."""
    return [symbol for group in group_by_asset_class(symbols).values() for symbol in group]
