# Plan 0006: percentage comparison of instruments on one chart

- **Status:** In progress <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-19
- **Branch:** `main`
- **Related:** [plan 0003](0003-dashboard-service.md), [plan 0004](0004-dashboard-readability-and-coverage.md), [ADR-0001](../adr/0001-ingestor-is-extract-load-only.md), [ADR-0005](../adr/0005-serving-reads-raw-directly.md)

## Context

The dashboard can only show one instrument at a time. To answer "is gold or silver the better
thing to hold right now?" you have to switch the picker back and forth and compare two absolute
price axes — 4,400 USD against 52 USD — which says nothing about relative performance.

Plan 0004 already parked this as a follow-up: *"Symbol comparison on one chart, and candlesticks."*
This plan does the comparison half. The maintainer asked for it directly on 2026-09-19, with
gold vs silver as the motivating case.

The fix is to normalise: plot each instrument as its **percentage change from a shared baseline**,
so the lines start together at 0% and the one that ends higher is the one that performed better
over the selected window.

## Goals

- A **Compare** tab where 2–5 instruments are drawn on one chart as % change from a common baseline.
- Both lines start at 0% at the **first bar the selected instruments have in common**, so the
  comparison is fair even when one series starts earlier than the other.
- Per-instrument metric tiles (change over range, last close) and an explicit statement of which
  instrument led over the window.
- For exactly two instruments, a **ratio panel** (A / B over time, e.g. the gold/silver ratio) with
  the current value and its range, since that is the direct answer to "which one to hold".
- Gold and silver preselected when both are present, because that is the asked-for case.

## Non-goals

- **No transformation of stored data.** The percentages and the ratio are computed in the page from
  bars read out of `raw`, and nothing is written back (GR-1, ADR-0005). The Ingestor is untouched.
- No new instruments, no schema change, no new environment variables (see *Design → Configuration*).
- No changes to the existing Prices tab: it keeps its single-instrument price chart.
- Candlesticks, indicators, correlation matrices, alerting, saved comparison sets. Later work if wanted.
- No currency normalisation. Comparing instruments quoted in different currencies is permitted and
  the percentages are computed in each instrument's own quote currency; FX effects are not removed.

## Design

### Where the logic lives

The service's existing split is kept: SQL in `queries.py`, Altair specs in `charts.py`, display
names in `instruments.py`, widgets in `app.py`. The new frame maths gets its own module so it is
testable without either Streamlit or Altair.

| File | Change |
|---|---|
| `src/finstream_dashboard/comparison.py` | **new** — alignment, baseline choice, rebasing, ratio |
| `src/finstream_dashboard/charts.py` | **+** `percent_change_chart()`, `ratio_chart()` |
| `src/finstream_dashboard/app.py` | **+** `render_compare()`, a fourth tab, a multi-symbol date range |
| `src/finstream_dashboard/queries.py` | unchanged |

### Reading the data

`render_compare()` calls the **existing** cached `load_prices()` once per selected symbol. No new
SQL: `price_history` already takes a symbol, and the `@st.cache_data` TTL means switching a widget
does not re-query. Up to five small queries instead of one multi-symbol query is the cheaper trade
here — it reuses a function that is already tested against a real TimescaleDB container.

The date range follows the union of the selected series: `min(first_ts)` … `max(last_ts)` across
them, via the existing `series_span()` and `charts.default_date_range()`.

### Baseline: the first common bar

```python
def common_start(frames: dict[str, pd.DataFrame]) -> pd.Timestamp | None:
    """The earliest moment every selected series has data for."""
    # max of each frame's first timestamp with a non-null close
```

Each series is then rebased on its own first non-null close **at or after** `common_start`:

```
percent = (close / baseline - 1) * 100
```

Bars *before* `common_start` are still drawn — they simply sit below or above 0%. So if gold has a
bar on 09-01 and silver's history starts on 09-02, both are anchored at 09-02 and gold's 09-01 bar
stays visible. Nothing is dropped, and neither line gets a head start.

A series with no usable close at or after `common_start` is dropped from the chart with a named
warning rather than silently producing NaNs.

### Ratio panel (exactly two instruments)

A ratio is only meaningful where both instruments have a bar at the *same* timestamp, so this panel
— and only this panel — inner-joins on `ts`:

```
ratio = close_A / close_B   on the intersection of both series' timestamps
```

Shown with the current value and the range minimum/maximum over the window, plus a one-line
reading ("falling → B is the stronger of the two"). If the intersection is empty (for example a
24/7 crypto series against an index with disjoint hourly stamps), the panel is replaced by an
explanatory note instead of an empty chart.

### Widgets and guards

- `st.multiselect`, grouped and labelled through the existing `instruments.describe()`/`label()`,
  defaulting to `GC=F` and `SI=F` when both appear in coverage, otherwise the first two symbols.
- Fewer than 2 selected → an `st.info` prompt, no chart. More than `MAX_COMPARE_SYMBOLS` (5) →
  an `st.warning` and the extras are ignored; five lines is the readable limit on one axis.
- A symbol with no bars in the chosen range is named in a warning and excluded.
- The `dashboard_max_rows` cap applies per symbol, and the existing "narrow the range" warning is
  raised if any series hits it.

### Chart

Altair, consistent with the existing charts: `zero=False` is irrelevant here because 0% *is* the
meaningful baseline, so the percentage chart keeps a **zero rule** (a dashed horizontal line at 0%)
and a domain padded around the data. Colour encodes the instrument by its display label, tooltip
gives time, instrument, % change and the underlying close.

### Configuration

**No new environment variables.** `MAX_COMPARE_SYMBOLS` is a UI readability cap, not deployment
configuration, and it sits beside the existing `DEFAULT_WINDOW_DAYS` constant. GR-4 governs
tickers, URLs, schedules, intervals and secrets; none of those change. Consequently `.env.example`
and `docs/configuration.md` need no edit — this is recorded here so the Definition of Done is not
ticked on a change that never happened.

`README.md` and `docs/architecture.md` get a line about the new tab (GR-11).

## Research items

None. No new dependency, no unverified API: Altair, pandas and Streamlit are already in use in
this service, and every function used here (`mark_rule`, `multiselect`, `DataFrame.merge`) is
already exercised by existing code or standard.

## Tasks

Each milestone is one coherent, testable slice, committed as a single Conventional Commit together
with its tests and doc updates.

- [x] **M1: Comparison maths** — `comparison.py` (`common_start`, `baseline_close`, `compare`,
      `ratio_series`), with unit tests for the baseline choice, rebasing, NaN handling, disjoint
      timestamps and the empty cases. No UI yet.
- [x] **M2: Charts and tab** — `percent_change_chart()` and `ratio_chart()` in `charts.py`,
      `render_compare()` and the fourth tab in `app.py`, with chart-spec and render-guard tests.
- [ ] **M3: Verify and document** — full gates, `docker compose up` smoke test against live data
      with a real gold-vs-silver reading, README and `docs/architecture.md` updated, plan closed.

## Test plan

Unit tests only for the new code; the integration suite is unchanged because no new SQL is added.

| Type | Applies |
|---|---|
| Baseline | `common_start` picks the later of two first timestamps; a series starting earlier keeps its earlier bars and is anchored at the common start |
| Rebasing | A series rebased to its baseline reads exactly 0% at the baseline bar, and `(close/baseline - 1) * 100` elsewhere |
| NaN handling | A NaN baseline bar falls through to the next valid close; an all-NaN series is dropped, not charted as NaN |
| Ratio | Computed only on the timestamp intersection; disjoint series produce an empty result rather than a raise; a zero/NaN denominator does not produce `inf` on the page |
| Guards | <2 selected → prompt and no chart; >5 → warning and truncation; a symbol with no bars in range is named and excluded |
| Chart specs | Both charts build for 1, 5 and 50 rows and for 2 and 5 instruments; the zero rule is present |
| Regression | The existing Prices tab tests still pass unchanged |
| Smoke | `docker compose up -d --build`, open the Compare tab on live data, record the real gold vs silver numbers in the Verification log |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Five symbols × the `dashboard_max_rows` cap pulls a lot of rows into one page | Medium | Low | Cap at 5 instruments, reuse the existing per-query `LIMIT` and the cache TTL; the "narrow the range" warning already exists |
| A percentage chart invites reading it as investment advice | Medium | Low | The tab states it is past performance from stored bars, in each instrument's own quote currency, with no FX adjustment |
| Instruments with different trading calendars (crypto vs an index) look misaligned | Medium | Low | The common baseline makes the start fair; the ratio panel, where alignment truly matters, inner-joins and says so when the intersection is empty |
| Scope creep into indicators and candlesticks | Low | Medium | Explicit non-goals; anything found goes to Follow-ups (GR-10) |

## Exceptions

None.

The percentage and ratio computations are **not** a GR-1 exception: GR-1 constrains the Ingestor's
write path, and this is display-time arithmetic in a read-only service (ADR-0005), consistent with
the "change over range" metric the Prices tab already computes.

## Follow-ups

<Out-of-scope findings discovered during the work (GR-10).>

- `charts.py` imports `altair` directly, but Altair is only a transitive dependency of Streamlit and
  is not pinned in `requirements.txt`. That is a pre-existing GR-8 gap from plan 0004, not created
  here; it deserves its own small change rather than a silent fix.

## Definition of Done

- [ ] `ruff check`, `ruff format --check`, `mypy src` clean
- [ ] `pytest` green, coverage ≥ 85% on `src/`
- [ ] Idempotency and resilience tests exist for new or changed writers/jobs — *n/a: this service
      never writes (`ALL_STATEMENTS` is SELECT-only and tested as such)*
- [ ] `docker compose up` smoke test passed, with the live gold-vs-silver reading recorded
- [ ] `docs/` and README updated; `.env.example` and `docs/configuration.md` unchanged **by design**
      (no new variables — see *Design → Configuration*)
- [ ] All tasks ticked, Verification log filled in, Status `Done`

## Verification log

<Command → trimmed real output → PASS / FAIL / SKIPPED (reason). Newest entries last.>

### 2026-09-19 · M1 comparison maths

`comparison.py` exposes `first_valid_ts`, `common_start`, `baseline_close`, `compare` and
`ratio_series`, plus the `SeriesSummary`/`Comparison` records the page will render. The public
surface differs slightly from the plan's sketch: `compare()` replaces the separate
`rebase_to_percent` + `summarise` pair, because rebasing and summarising walk the same frames once
and the page needs both results together with the list of dropped symbols.

Gates, run in `services/dashboard/.venv`:

```
$ ruff check .
All checks passed!
$ ruff format --check .
16 files already formatted
$ mypy src
Success: no issues found in 7 source files
$ pytest -m "not integration" --cov=finstream_dashboard --cov-report=term-missing -q
62 passed, 6 deselected in 2.82s
src/finstream_dashboard/comparison.py       74      0     14      0   100%
TOTAL                                      309     26     44      6    91%
```

PASS. 18 new unit tests, `comparison.py` at 100% statement and branch coverage, service total 91%.

Two findings from writing the tests, both now covered:

- **A percentage change off a zero baseline is infinity.** A series whose baseline close is `0.0`
  is dropped and named, rather than sent to the chart as `inf`. The same guard drops a zero
  denominator from the ratio.
- **An all-NaN series must not move the common start.** It is dropped from the comparison anyway,
  so letting its first timestamp win the `max()` would have shortened everyone else's window for
  nothing. `common_start` now ignores series with no usable close.

One test initially failed on `(76 / 80 - 1) * 100 == -5.000000000000004`; the assertion was
tightened to `pytest.approx`, the arithmetic was not changed.

### 2026-09-19 · M2 charts and the Compare tab

`charts.percent_change_chart` / `charts.ratio_chart` and `app.render_compare` / `app.render_ratio`,
behind a fourth tab. Every Compare widget carries an explicit `key=`: the tab reuses the "Interval"
and "Date range" labels the Prices tab already has, and without distinct keys Streamlit would raise
a duplicate-widget error the moment both tabs render in one run.

Gates:

```
$ ruff check .
All checks passed!
$ mypy src
Success: no issues found in 7 source files
$ pytest -m "not integration" --cov=finstream_dashboard --cov-report=term-missing -q
91 passed, 6 deselected in 3.85s
src/finstream_dashboard/app.py         197     15     50      6    91%
src/finstream_dashboard/charts.py       74      4     10      2    93%
src/finstream_dashboard/comparison.py   74      0     14      0   100%
TOTAL                                  419     28     76      8    93%
```

PASS. 29 new tests across `test_charts.py` and `test_app.py`.

End-to-end shape check on gold vs silver, silver starting a day later with a trailing NaN bar:

```
common baseline: 2026-09-02 00:00:00+00:00
  GC=F: baseline  4420.00  last  4493.00  +1.65%
  SI=F: baseline    52.00  last    55.10  +5.96%
leader: SI=F
ratio rows: 4 | current: 81.31
y domain: [-1.31, 6.31]
```

Gold's 09-01 bar is kept and sits at -0.45%, which is why the domain starts below zero — the
baseline moved to the first shared bar without throwing the earlier bar away. The NaN bar keeps
silver out of the last ratio point (4 rows, not 5).

Three corrections made while testing, all in tests or structure rather than in the maths:

- **A chart test asserted one Vega param; there are two.** `.interactive()` contributes its own
  interval param for pan and zoom alongside the hover selection, so the assertion now counts point
  selections only.
- **The `>5 instruments` render test could not work.** The picker is `left.multiselect` on a column
  object, not `st.multiselect`, so patching the module attribute changed nothing. The cap moved
  into a pure `limit_to_readable()` that is tested directly and also reports which instruments were
  left out, instead of dropping them silently.
- **An unreachable branch in `render_compare`.** After the "nothing comparable" guard, the leader
  and the baseline can never be `None`, so the two checks were folded into that one guard.

Known uncovered line: `app.py:250`, the warning for a selection above five. In bare mode the picker
always returns its default, so the branch cannot be reached from `render_compare`; the logic behind
it is covered through `limit_to_readable`.

## Change log

- 2026-09-19: created
- 2026-09-19: approved by the maintainer; started M1
- 2026-09-19: M1 done — comparison maths and 18 unit tests
- 2026-09-19: M2 done — Compare tab, percentage and ratio charts
