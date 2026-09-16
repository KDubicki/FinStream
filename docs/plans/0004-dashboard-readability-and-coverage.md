# Plan 0004: readable charts and broader instrument coverage

- **Status:** Done <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-17
- **Branch:** `main`
- **Related:** [plan 0003](0003-dashboard-service.md), [ADR-0001](../adr/0001-ingestor-is-extract-load-only.md), [ADR-0005](../adr/0005-serving-reads-raw-directly.md), [configuration](../configuration.md)

## Context

The dashboard works but is hard to read, and the maintainer asked for the well-known instruments (gold, S&P 500, and so on) to be collected.

Looking at the current page, three concrete problems:

1. **The price chart is flat.** The y-axis starts at zero, so an instrument trading near 80 draws a straight line across the top. Real movement is invisible.
2. **The x-axis is unreadable**, with every hour labelled, and the default 90-day window is mostly empty because only about five days of hourly bars exist.
3. **Symbols are raw tickers.** `IAU` and `^GSPC` mean nothing without a name, and `close` and `adj_close` are drawn as two identical lines.

Coverage is also narrow: nine symbols, all US equity/gold.

## Goals

- A price chart that shows the actual movement: no forced zero baseline, a readable time axis, and a tooltip with the values.
- A date range that defaults to the data that exists for the chosen series, not a fixed 90 days.
- Symbols presented by name and grouped by asset class, e.g. "S&P 500 · ^GSPC" under *Indices*.
- A broader default instrument set covering gold and other metals, energy, the major indices, large ETFs, FX, crypto and a rates benchmark.
- Correct handling of a NaN latest close, which several indices return intraday.

## Non-goals

- Any transformation of stored data. This is a presentation change (GR-1, ADR-0005); instrument names live in the dashboard as display metadata, not in `raw`.
- Candlestick charts, indicators, comparisons between symbols, alerting. All later work if wanted.
- A database table of instrument metadata; that stays a follow-up (`raw.instruments`).

## Design

### Instrument metadata (`services/dashboard/src/finstream_dashboard/instruments.py`)
A frozen dataclass `Instrument(symbol, name, asset_class)` plus a lookup keyed by symbol, with `describe()` falling back to the bare symbol so an unknown ticker still renders. Purely for display.

### Chart rewrite (`app.py`)
- Altair (already a Streamlit dependency) instead of `st.line_chart`, with `alt.Scale(zero=False)`, `axis=alt.Axis(format='~s')`, an interactive tooltip, and an automatic time axis that thins its own labels.
- `adj_close` is drawn only when it actually differs from `close`, which is rarely.
- Volume moves to a compact bar chart sharing the x axis.
- Metrics use the last *valid* close, since several indices return NaN for the most recent intraday bar.

### Symbols
`YAHOO_SYMBOLS` grows to a curated set, **each verified against Yahoo before inclusion** on 2026-09-17:

| Class | Symbols |
|---|---|
| Metals | `GC=F`, `SI=F`, `GLD`, `SLV` |
| Energy | `CL=F`, `NG=F` |
| Indices | `^GSPC`, `^IXIC`, `^DJI`, `^RUT`, `^VIX`, `^FTSE`, `^STOXX50E` |
| ETFs | `SPY`, `QQQ`, `IWM`, `TLT` |
| FX | `EURUSD=X`, `USDPLN=X`, `DX-Y.NYB` |
| Crypto | `BTC-USD`, `ETH-USD` |
| Rates | `^TNX` |

`^WIG20` is excluded: Yahoo reports it delisted. `WIG20.WA` does work and can be added if wanted.

## Tasks

- [x] **M1: Instruments and chart** — `instruments.py`, the Altair rewrite, NaN-safe metrics, data-driven default date range, with tests
- [x] **M2: Coverage** — expand `YAHOO_SYMBOLS` in `config.py`, `.env.example` and `docs/configuration.md`; restart the ingestor and confirm the new symbols land
- [x] **M3: Verify** — both services' gates green, stack running, CI green

## Test plan

| Type | Applies |
|---|---|
| Fail fast | Existing config tests still pass with the larger default list |
| Display logic | `describe()` falls back for unknown symbols; grouping is stable and ordered |
| NaN handling | Last valid close is used when the most recent bar has none; a fully empty series shows "n/a" rather than crashing |
| Default range | Derived from the series' own first and last timestamps |
| Integration | Existing dashboard query tests continue to pass |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| More symbols means more Yahoo calls per cycle, raising rate-limit risk | Medium | Medium | Sequential fetches, existing backoff, and `RateLimitedError` already recorded per symbol; the set can be trimmed in `.env` without a code change |
| Some indices return NaN closes intraday | High | Low | Explicitly handled and tested |
| Longer job runs | Medium | Low | `max_instances=1` prevents overlap; runs are recorded per symbol |

## Exceptions

- **Plan written and approved in one step**, as with plans 0002 and 0003: the maintainer asked for this directly on 2026-09-17.

## Follow-ups

- `raw.instruments` table so names and exchange timezones live with the data.
- Symbol comparison on one chart, and candlesticks.

## Definition of Done

- [x] `ruff check`, `ruff format --check`, `mypy src` clean in both services
- [x] `pytest` green, coverage ≥ 85% on `src/`
- [x] Stack restarted, new symbols collected, page readable
- [x] `.env.example`, `docs/configuration.md` updated
- [x] Tasks ticked, Verification log filled, Status `Done`

## Verification log

### 2026-09-17 · readable charts and 23 instruments

**Symbols were verified against Yahoo before being added**, not assumed: every candidate was
fetched first. `^WIG20` failed (`YFPricesMissingError`, delisted) and was left out; `WIG20.WA`
works and is documented as an opt-in. Four indices (`^GDAXI`, `^FCHI`, `^N225`, `^HSI`) return a
**NaN close on the newest intraday bar**, which is what prompted the last-valid-close handling.

Gates, both services:

- ingestor: `ruff`, `ruff format --check`, `mypy src` clean; `88 unit tests` pass; the default list parses to exactly 23 symbols.
- dashboard: `ruff`, `ruff format --check`, `mypy src` clean; **`52 passed`, coverage 90.94%**.

Live stack after `docker compose up -d --build`:

- All three services healthy; dashboard `GET /_stcore/health` → 200.
- **50 series stored (25 symbols × `1d` and `1h`), 1,768 rows, 64 ingestion runs, all `success`, zero failed and zero empty.** Every newly added instrument returned data, including `DX-Y.NYB`, `^STOXX50E`, `^TNX`, `BTC-USD` and `USDPLN=X`.
- 25 rather than 23 symbols because `IAU` and `VOO` remain from the previous list: the Ingestor never deletes, so dropping a symbol from `YAHOO_SYMBOLS` stops collection but keeps the history.

**The readability fix, measured on live data** (run inside the dashboard container):

| Series | Y-axis domain | Zero baseline? |
|---|---|---|
| Gold futures (`GC=F`, 1d) | 4,325.61 … 4,483.79 | no |
| S&P 500 (`^GSPC`, 1d) | 7,577.63 … 7,755.81 | no |

That is the whole bug: with a zero baseline an 80-dollar series drew a flat line across the top.
The opening window now follows the data (e.g. 2026-09-04 … 2026-09-16 for gold) instead of a
fixed 90 days, and `adj_close` is suppressed when identical to `close`, which it was for both
series above.

## Change log

- 2026-09-17: created and approved (maintainer request).
- 2026-09-17: implemented and closed; 23 instruments collected and the charts made readable.
