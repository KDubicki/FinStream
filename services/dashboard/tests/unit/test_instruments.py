"""Display metadata: names for known symbols, graceful fallback for everything else."""

from __future__ import annotations

from finstream_dashboard import instruments


def test_known_symbols_get_a_name_and_class() -> None:
    gold = instruments.describe("GC=F")
    assert gold.name == "Gold futures"
    assert gold.asset_class == instruments.METALS
    assert instruments.describe("^GSPC").name == "S&P 500"


def test_unknown_symbols_still_render() -> None:
    """An operator can add any ticker to YAHOO_SYMBOLS; the page must not break."""
    unknown = instruments.describe("SOMETHING.NEW")
    assert unknown.name == "SOMETHING.NEW"
    assert unknown.asset_class == instruments.OTHER
    assert unknown.label == "SOMETHING.NEW · SOMETHING.NEW"


def test_label_shows_name_and_ticker() -> None:
    assert instruments.label("^GSPC") == "S&P 500 · ^GSPC"


def test_grouping_follows_the_declared_class_order() -> None:
    grouped = instruments.group_by_asset_class(["BTC-USD", "^GSPC", "GC=F", "EURUSD=X"])
    assert list(grouped) == [
        instruments.METALS,
        instruments.INDICES,
        instruments.FX,
        instruments.CRYPTO,
    ]


def test_symbols_are_sorted_by_name_inside_a_class() -> None:
    grouped = instruments.group_by_asset_class(["SLV", "GC=F", "GLD"])
    assert grouped[instruments.METALS] == ["GC=F", "GLD", "SLV"]  # Gold futures, SPDR, iShares


def test_unknown_symbols_sort_last() -> None:
    ordered = instruments.ordered_symbols(["ZZZ.TEST", "^GSPC", "GC=F"])
    assert ordered[-1] == "ZZZ.TEST"
    assert ordered[0] == "GC=F"


def test_every_declared_instrument_is_self_consistent() -> None:
    for symbol, instrument in instruments.INSTRUMENTS.items():
        assert instrument.symbol == symbol
        assert instrument.asset_class in instruments.ASSET_CLASS_ORDER
        assert instrument.name
