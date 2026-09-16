# Research: yfinance behaviour (plan 0001, R1)

- **Date:** 2026-09-16
- **Author:** Claude Opus 5 (Claude Code), for review by the FinStream owner
- **Related plan / item:** [plan 0001](../plans/0001-ingestor-implementation.md), R1
- **Status:** Final

## Question

How does the current yfinance release signal failure (exception vs empty DataFrame), which exception means rate limiting, what does a returned DataFrame look like (columns, index timezone), and what are the history limits? This unblocks the error classification, the retry policy and the normalisation step in M3.

## Context

GR-3 requires transient failures to be retried and permanent ones to be recorded without retrying, and GR-1 restricts normalisation to renaming, casting, UTC conversion and NaN handling. Both need exact knowledge of what yfinance returns and raises.

## Findings

### Package
- **yfinance 1.7.0**, released 2026-08-26. Declares `pandas>=1.3.0` (no upper bound), `curl_cffi>=0.15`, `numpy>=1.16.5`, `requests>=2.31`, `peewee>=3.16.2`, `websockets>=13.0`; no `requires_python`. Source: [PyPI JSON](https://pypi.org/pypi/yfinance/json), checked 2026-09-16.
- `curl_cffi` is a hard runtime dependency, so no custom session is needed for browser impersonation.

### Exceptions (`yfinance/exceptions.py`, main branch, checked 2026-09-16)
| Class | Base | Meaning |
|---|---|---|
| `YFException` | `Exception` | Base for all yfinance errors |
| `YFRateLimitError` | `YFException` | "Too Many Requests. Rate limited. Try after a while." |
| `YFTickerMissingError` | `YFException` | Expected ticker data absent, possibly delisted |
| `YFTzMissingError` | `YFTickerMissingError` | No timezone for the ticker (typical for delisted/invalid symbols) |
| `YFPricesMissingError` | `YFTickerMissingError` | No price data for the requested range/interval |
| `YFInvalidPeriodError` | `YFException` | Invalid `period` value, message lists the valid ones |
| `YFDataException`, `YFNotImplementedError` | — | Other data/API problems |

Source: <https://github.com/ranaroussi/yfinance/blob/main/yfinance/exceptions.py>

### `Ticker.history()` error behaviour (`yfinance/scrapers/history.py`, checked 2026-09-16)
- A `raise_errors` parameter still exists but is **deprecated**: the code emits `"'raise_errors' deprecated, do: yf.config.debug.hide_exceptions = False"`.
- **`YFRateLimitError` always propagates**, regardless of that setting. It's explicitly re-raised.
- `YFTzMissingError`, `YFPricesMissingError` and `YFInvalidPeriodError` are raised only when exceptions aren't hidden. Otherwise they're logged and the method returns `utils.empty_df()`.

### Configuration API (`yfinance/config.py`, checked 2026-09-16)
```python
YfConfig.debug.hide_exceptions = True  # default
YfConfig.debug.logging = False  # default
YfConfig.network.proxy = None
YfConfig.network.retries = 0
YfConfig.locale.lang = "en-US"
YfConfig.locale.region = "US"
```
Options are set attribute-style, e.g. `yf.config.debug.hide_exceptions = False`.

### Returned data
- The index is localised to the **exchange timezone** (`utils.set_df_tz(quotes, interval, tz_exchange)`), and it's named `Date` for daily bars and `Datetime` for intraday ones.
- With `auto_adjust=False` the columns are `Open`, `High`, `Low`, `Close`, `Adj Close`, `Volume`, plus `Dividends`, `Stock Splits` and `Capital Gains` when `actions=True`.
- Multi-level columns are a `yf.download()` feature for multiple tickers. We fetch per symbol with `Ticker.history()`, which gives single-level columns. **UNVERIFIED** at runtime, so a unit test asserts the exact column set.

### History limits
- **UNVERIFIED (secondary sources only):** hourly data reaches back about 730 days, and 1-minute data only about 7 days. These limits are enforced by Yahoo, not by yfinance, and no primary documentation was found. Sources: [aroussi.com](https://aroussi.com/post/python-yahoo-finance), [AlgoTrading101](https://algotrading101.com/learn/yfinance-guide/).
- Consequence: backfill (`BACKFILL_PERIOD`, up to `max`) uses **daily bars only**, so we never depend on this limit.

## Recommendation

1. **Turn exceptions on at startup:** `yf.config.debug.hide_exceptions = False`. Don't use the deprecated `raise_errors` parameter. Without this, failures arrive as empty DataFrames and can't be classified.
2. **Fetch:** `yf.Ticker(symbol).history(period=…, interval=…, auto_adjust=False, actions=False)`, one call per symbol.
3. **Error classification** (`sources/yahoo.py`):

   | yfinance raises | Our type | Retried? | Run status |
   |---|---|---|---|
   | `YFRateLimitError` | `RateLimitedError` | yes | `failed` after exhaustion |
   | network / timeout / connection errors | `TransientSourceError` | yes | `failed` after exhaustion |
   | `YFPricesMissingError` | *(not an error)* returns `[]` | no | `empty` |
   | `YFTzMissingError`, `YFTickerMissingError` | `PermanentSourceError` | no | `failed` |
   | `YFInvalidPeriodError` | `PermanentSourceError` | no | `failed` (configuration bug) |
   | empty DataFrame without an exception | `[]` | no | `empty` |

   `YFPricesMissingError` counts as "empty" because a closed market or holiday triggers it legitimately, and `YFTzMissingError` means the symbol itself is wrong.
4. **Normalisation** (GR-1 allowed operations only): rename `Adj Close` → `adj_close` and lowercase the rest; `tz_convert("UTC")` on the index (it's already tz-aware); `NaN` → `None`; drop all-NaN rows; cast volume to `int`.
5. **pandas 3.x:** allowed by yfinance's metadata but unproven at runtime. M1 adds a smoke test that imports yfinance and normalises a fixture. If it breaks, pin `pandas==2.*`.

## Risks & open questions

- The intraday history limit is unverified. It doesn't block us, since backfill is daily-only.
- Deprecated `raise_errors` may be removed in a future release. We use `yf.config` instead, so we aren't affected.
- Corporate actions (dividends, splits) aren't stored. That's a follow-up (`raw.corporate_actions`), not part of plan 0001.

## Sources

1. <https://pypi.org/pypi/yfinance/json> (1.7.0, checked 2026-09-16)
2. <https://github.com/ranaroussi/yfinance/blob/main/yfinance/exceptions.py> (main, 2026-09-16)
3. <https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py> (main, 2026-09-16)
4. <https://github.com/ranaroussi/yfinance/blob/main/yfinance/config.py> (main, 2026-09-16)
5. <https://aroussi.com/post/python-yahoo-finance>, <https://algotrading101.com/learn/yfinance-guide/> (secondary, for the history limits)
