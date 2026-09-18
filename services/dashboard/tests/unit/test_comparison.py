"""Comparison maths: a fair baseline, honest percentages, and no infinities on the page."""

from __future__ import annotations

import pandas as pd
import pytest

from finstream_dashboard import comparison

DAY = pd.Timedelta(days=1)
START = pd.Timestamp("2026-09-01", tz="UTC")


def bars(closes: list[float | None], *, offset: int = 0) -> pd.DataFrame:
    """Daily bars starting `offset` days after START, shaped like `queries.price_history`."""
    return pd.DataFrame(
        {
            "ts": [START + (offset + i) * DAY for i in range(len(closes))],
            "open": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1_000] * len(closes),
        }
    )


def percents(result: comparison.Comparison, symbol: str) -> list[float]:
    rows = result.frame[result.frame["symbol"] == symbol]
    return [round(value, 6) for value in rows["percent"]]


def test_common_start_is_the_later_of_the_two_first_bars() -> None:
    """Gold from 09-01 and silver from 09-02 have to be anchored at 09-02."""
    frames = {"GC=F": bars([100.0, 110.0, 120.0]), "SI=F": bars([50.0, 55.0], offset=1)}
    assert comparison.common_start(frames) == START + DAY


def test_an_earlier_series_keeps_its_bars_but_gets_no_head_start() -> None:
    """The bar before the common start stays visible; 0% still sits on the common start."""
    frames = {"GC=F": bars([100.0, 200.0, 300.0]), "SI=F": bars([50.0, 55.0], offset=1)}

    result = comparison.compare(frames)

    assert result.start == START + DAY
    # Gold's baseline is its 09-02 close (200), so 09-01 reads -50% and 09-02 reads exactly 0%.
    assert percents(result, "GC=F") == [-50.0, 0.0, 50.0]
    assert percents(result, "SI=F") == [0.0, 10.0]
    assert len(result.frame) == 5, "no bar is dropped, it is only rebased"


def test_percent_change_is_rebased_against_the_baseline() -> None:
    result = comparison.compare({"GC=F": bars([80.0, 88.0, 76.0])})
    assert percents(result, "GC=F") == [0.0, 10.0, -5.0]
    assert result.summaries[0].baseline == 80.0
    assert result.summaries[0].last_close == 76.0
    assert result.summaries[0].percent_change == pytest.approx(-5.0)


def test_baseline_skips_a_nan_at_the_common_start() -> None:
    """Several indices report a NaN close; the baseline moves to the next real print."""
    result = comparison.compare({"^GSPC": bars([None, 50.0, 75.0])})
    assert result.summaries[0].baseline == 50.0
    assert percents(result, "^GSPC") == [0.0, 50.0]


def test_an_all_nan_series_is_dropped_and_named() -> None:
    frames = {"GC=F": bars([100.0, 110.0]), "DEAD": bars([None, None])}

    result = comparison.compare(frames)

    assert result.dropped == ("DEAD",)
    assert set(result.frame["symbol"]) == {"GC=F"}
    assert [summary.symbol for summary in result.summaries] == ["GC=F"]


def test_an_all_nan_series_does_not_move_the_common_start() -> None:
    """A useless series is dropped anyway, so it must not shrink everyone else's window."""
    frames = {"GC=F": bars([100.0, 110.0]), "DEAD": bars([None], offset=5)}
    assert comparison.common_start(frames) == START


def test_a_zero_baseline_is_dropped_rather_than_producing_infinity() -> None:
    result = comparison.compare({"ZERO": bars([0.0, 5.0])})
    assert result.dropped == ("ZERO",)
    assert result.frame.empty
    assert result.leader is None


def test_bars_out_of_order_are_sorted_before_the_baseline_is_taken() -> None:
    frame = bars([100.0, 110.0]).iloc[::-1]
    result = comparison.compare({"GC=F": frame})
    assert result.summaries[0].baseline == 100.0
    assert result.summaries[0].last_close == 110.0


def test_leader_is_the_strongest_performer() -> None:
    frames = {
        "GC=F": bars([100.0, 102.0]),  # +2%
        "SI=F": bars([50.0, 55.0]),  # +10%
        "CL=F": bars([70.0, 63.0]),  # -10%
    }

    leader = comparison.compare(frames).leader

    assert leader is not None
    assert leader.symbol == "SI=F"
    assert leader.percent_change == pytest.approx(10.0)


def test_comparing_nothing_is_an_empty_comparison_not_a_crash() -> None:
    result = comparison.compare({})
    assert result.start is None
    assert result.frame.empty
    assert result.summaries == ()
    assert result.leader is None


def test_an_empty_frame_is_dropped() -> None:
    result = comparison.compare({"GC=F": bars([100.0]), "NEW": pd.DataFrame()})
    assert result.dropped == ("NEW",)


def test_ratio_uses_only_timestamps_both_series_have() -> None:
    """Gold/silver: only the days both instruments traded can produce a ratio."""
    gold = bars([100.0, 200.0, 300.0])
    silver = bars([50.0, 50.0], offset=1)

    ratio = comparison.ratio_series(gold, silver)

    assert list(ratio["ts"]) == [START + DAY, START + 2 * DAY]
    assert list(ratio["ratio"]) == [4.0, 6.0]


def test_ratio_of_disjoint_series_is_empty_rather_than_an_error() -> None:
    ratio = comparison.ratio_series(bars([100.0, 110.0]), bars([50.0, 55.0], offset=10))
    assert ratio.empty
    assert list(ratio.columns) == ["ts", "ratio"]


def test_ratio_skips_a_zero_or_missing_denominator() -> None:
    gold = bars([100.0, 110.0, 120.0])
    silver = bars([0.0, None, 60.0])

    ratio = comparison.ratio_series(gold, silver)

    assert list(ratio["ratio"]) == [2.0]
    assert ratio["ratio"].abs().max() < float("inf"), "a zero denominator must not reach the chart"


def test_ratio_of_an_empty_frame_is_empty() -> None:
    assert comparison.ratio_series(bars([100.0]), pd.DataFrame()).empty


def test_baseline_without_a_common_start_is_the_series_own_first_close() -> None:
    """A single series has nothing to align to, so it is its own baseline."""
    assert comparison.baseline_close(bars([None, 42.0, 50.0]), None) == 42.0
