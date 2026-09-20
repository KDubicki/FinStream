# Research: additional market-data sources for FinStream

- **Date:** 2026-09-18
- **Author:** Claude Code (Opus 5), research agent; reviewed by: *pending*
- **Related plan / item:** none yet — input to the "multi-source" plan (**0006**, not 0002: see
  [note 01, F10](01-current-state-and-backlog.md))
- **Status:** Draft <!-- Draft | Final -->

> Research only. No production code, configuration or schema was changed. Every claim carries a URL
> and the date checked; all live probes ran **2026-09-18**. Anything not confirmed from a primary
> source is marked **UNVERIFIED**.
>
> **Independent re-verification.** The orchestrating session re-ran four load-bearing probes and
> seven PyPI lookups on 2026-09-18 and got identical results: the Stooq proof-of-work gate
> (`200 text/html`, `crypto.subtle.digest("SHA-256"…)`), Binance klines (keyless, 12-field array),
> NBP `/api/cenyzlota/last/2/` (`2026-09-17 → 523.97`), `api.exchangerate.host` returning
> `missing_access_key`, and the versions of `alpaca-py` 0.44.0, `ccxt` 4.5.78, `sdmx1` 2.27.0,
> `pandasdmx` 1.10.0 (`requires_python >=3.9.6,<3.12`), `fredapi` 0.5.2, `python-binance` 1.0.37 and
> `nasdaq-data-link` 1.0.4 (2022-08-29).
>
> Companion notes: [01 current state](01-current-state-and-backlog.md) ·
> [03 event-driven](03-event-driven-platform.md) · [04 roadmap](04-roadmap.md)

---

## Question

Which additional market-data sources should FinStream ingest alongside Yahoo Finance, given that
`raw.market_prices` is already keyed by `source` (so providers can coexist), the ingestor is
Extract & Load only (GR-1), and the project is a single-maintainer, self-hosted, cost-sensitive
Docker Compose stack?

## Context

Yahoo (via `yfinance`) already covers the whole current symbol set — metals futures + ETFs, energy,
indices, ETFs, FX, crypto and `^TNX` (see `docs/configuration.md`). So "more of the same" has low
marginal value. The real gaps are:

1. **No independent second opinion** on any price (single point of failure; yfinance is a scraper).
2. **No macro / interest-rate series** — Yahoo has `^TNX` as a price, but not CPI, unemployment,
   policy rates, or a yield curve.
3. **No official PLN data**, although the maintainer is Polish and `USDPLN=X` is already ingested.
4. **No exchange-native crypto** — Yahoo's `BTC-USD` is a composite with unreliable volume.
5. **No push/streaming path**, which the project wants for the event-driven phase.

---

## 1. Executive summary

### 1.1 Ranked: best marginal value first

| # | Source | One-line justification |
|---|---|---|
| **1** | **NBP Web API** (`api.nbp.pl`) | Keyless, official central-bank PLN FX (from 2002) **and** PLN gold (from 2013); zero cost, zero ToS risk, and Yahoo has no authoritative PLN reference rate. Cheapest possible win. |
| **2** | **Binance public REST + WS** | True exchange-native crypto OHLCV with real volume and full listing history (BTCUSDT back to 2017-08-17), 1m granularity, no API key, 6000 weight/min. Strictly better than Yahoo's `BTC-USD`, and the keyless WebSocket is the project's cheapest on-ramp to event-driven. |
| **3** | **FRED** (St. Louis Fed) | Unlocks the entire macro/rates axis Yahoo cannot serve (CPI, unemployment, Fed funds, full Treasury curve, DXY-style series). Free key, stable, decades of history — but needs a **new table**. |
| **4** | **Alpaca Market Data (free tier)** | 200 req/min, 7+ years, IEX feed + free WebSocket. The best free *independent* cross-check for the ETF/equity half of the symbol list (SPY, QQQ, IWM, TLT, GLD, SLV), from a regulated broker rather than a scrape. |

Honourable mention: **ECB Data Portal SDMX** — keyless, authoritative EUR FX and euro-area policy
rates. It is nearly free to add once the macro table from (3) exists, and it is the upstream of
Frankfurter.

### 1.2 Best cross-check / redundancy for Yahoo

**Alpaca free tier**, for the equity and ETF part of the symbol set. It is a fully independent
provider (broker-sourced IEX feed, not a scrape of a consumer website), 200 req/min free is far more
than a daily+hourly job needs, 7+ years of history covers any backfill, and every ETF in
`YAHOO_SYMBOLS` (SPY, QQQ, IWM, TLT, GLD, SLV) is in scope. Writing it as `source='alpaca'` next to
`source='yahoo'` gives a per-bar divergence check for free.

Caveats and runners-up:

- **Alpaca's IEX feed is a single-venue feed**, so its daily volume will legitimately differ from
  Yahoo's consolidated volume. Use OHLC for divergence checks, not volume.
- **Tiingo** is the better *EOD* cross-check (clean "composite" EOD prices) but its free tier is
  capped at **500 unique symbols/month, 50 req/hour, 1000 req/day, 1 GB/month**, and its licence is
  **"Internal Use Only… your own personal use and you may not display or share the data"**. Fine for
  a private self-hosted stack; it forbids putting the data on a public dashboard.
- **Binance** is the cross-check for the crypto rows.
- **Stooq would have been the ideal keyless cross-check** (it covers indices, FX, metals and GPW in
  one CSV endpoint) — but it is now bot-blocked. See §3.1.

### 1.3 Sources that need a NEW table rather than more `market_prices` rows

| Source | Why it does not fit `market_prices` | Target table |
|---|---|---|
| FRED, ECB SDMX, Eurostat, World Bank | Single observation value per period, no OHLC, no volume; also revision/vintage semantics | `raw.macro_series` |
| NBP gold (`/cenyzlota`) | One price per day (PLN per 1 g, fineness 1000) | `raw.macro_series` |
| LBMA benchmarks | One fixing per auction, three currencies per row, no volume | `raw.macro_series` (3 rows/day) |
| NBP table C | **bid/ask**, not OHLC | `raw.fx_rates` |
| Finnhub `/stock/symbol`, Binance `exchangeInfo`, Massive tickers | Reference/instrument metadata, not time series | `raw.instruments` |
| Tiingo/FMP/yfinance actions | Splits & dividends as discrete events | `raw.corporate_actions` |

Minimal shapes are sketched in §6.

### 1.4 Native push / WebSocket (verified live, 2026-09-18)

| Source | Keyless? | Verified |
|---|---|---|
| **Binance** `wss://stream.binance.com:9443/ws/<sym>@kline_1m` | yes | **Confirmed live** — received a `kline` frame (see §7) |
| **Kraken** `wss://ws.kraken.com/v2` (`ohlc` channel) | yes | **Confirmed live** — connected, got `status` frame `v2.0.10` |
| Bybit, Deribit, Coinbase Exchange | yes | Public WS documented; REST verified, WS **UNVERIFIED** |
| Alpaca | key required, free tier | Free, but **limited to 30 symbols** |
| Finnhub, Twelve Data | key required | Free/trial tiers exist; **UNVERIFIED** |
| Massive (ex-Polygon.io) | key required | **Not on the free tier** |

**Recommendation:** Binance keyless kline WS is the single best first streaming integration — no key,
no account, no symbol cap, and the frames already carry a complete OHLCV bar plus an `x`
(bar-closed) flag, so an EL-only consumer can upsert closed bars without deriving anything.

---

## 2. Master comparison table

Free-tier limits as verified on the dates shown. "PyPI" = latest version on 2026-09-18.

| Source | Key? | Free-tier limit (exact) | Asset classes | OHLCV fit | PyPI client (latest) | Verdict |
|---|---|---|---|---|---|---|
| **Stooq** | no | n/a — **bot-blocked** | indices, FX, metals, GPW, ETFs | would be perfect | `pandas-datareader` 0.11.1 | **Reject** |
| **Alpha Vantage** | yes | **25 req/day** | equities, FX, crypto, some commodities | good | `alpha_vantage` 3.0.0 (2024-07-18) | **Reject** |
| **Twelve Data** | yes | **800 credits/day, 8 credits/min**; `time_series` = 1 credit/symbol | equities, ETF, FX, crypto, indices | good (but local-exchange tz by default) | `twelvedata` 1.4.0 (2026-04-27) | Conditional |
| **Finnhub** | yes | **60 calls/min** (secondary source) | equities, FX, crypto, fundamentals | good | `finnhub-python` 2.4.29 (2026-06-24) | Conditional |
| **Tiingo** | yes | **50/h, 1000/day, 500 symbols/mo, 1 GB/mo** | EOD equities/ETF, IEX, crypto | very good (EOD) | `tiingo` 0.16.1 (2025-04-05) | Conditional |
| **Massive** (ex-Polygon.io) | yes | **5 calls/min, 2 years history, EOD only, no WS** | stocks, options, indices, FX, crypto, futures | very good | `polygon-api-client` 1.16.3 (2025-10-30) | Conditional |
| **EODHD** | yes | **20 calls/day, past year only, personal use** | global EOD, fundamentals | good | `eodhd` 1.0.32 (2024-12-18) | **Reject** |
| **FMP** | yes | **250 calls/day, EOD, 500 MB/30 d** | equities, ETF, FX, crypto, commodities | good | `fmpsdk` 20260824.0 (2026-08-24) | Conditional |
| **Marketstack** | yes | **100 requests/month**, 1 yr history | equities/ETF EOD | good | `marketstack` 0.6.2 (2022) — unofficial | **Reject** |
| **Databento** | yes | none — **$125 signup credit**, then $/GB, plans from $199/mo | futures, options, US equities (L1–L3) | excellent | `databento` 0.86.0 (2026-09-01) | **Reject (cost)**, note as upgrade path |
| **Nasdaq Data Link** | yes | **UNVERIFIED** (site behind Imperva) | mixed datasets | varies | `nasdaq-data-link` 1.0.4 (**2022-08-29**) | **Reject** |
| **Binance** | **no** | **6000 request-weight/min**; klines weight 2, max 1000 rows/call | crypto spot | **perfect** | `python-binance` 1.0.37 (2026-06-08) | **Recommend** |
| **Coinbase Exchange** | **no** | documented; observed **max 350 candles/call** | crypto spot | good (field order differs) | plain `httpx` | Conditional |
| **Kraken** | **no** | public; **only ~720 most recent candles, `since` ignored** | crypto spot | poor for history | `krakenex` 2.2.2 (2024-07-01) | Conditional |
| **CCXT** | n/a | per-exchange | 100+ exchanges | perfect (unified) | `ccxt` 4.5.78 (2026-09-07) | Recommend *as adapter* |
| **CoinGecko** | optional | Demo **100 calls/min, 10 000 calls/month**; attribution required | crypto aggregate | **poor** — OHLC without volume, auto-chosen granularity | `pycoingecko` 3.2.0 (2024-11-13) | **Reject for bars** |
| **ECB Data Portal** | **no** | no documented quota | EUR FX, policy rates, macro | macro table | `sdmx1` 2.27.0 / plain `httpx` | **Recommend** |
| **Frankfurter** | **no** | **"no quotas"**, self-hostable | FX, 205 currencies, back to 1948 | macro/FX table | `frankfurter` 2.0.0 (2025-01-08) | Recommend (or use ECB directly) |
| **exchangerate.host** | **yes (now)** | n/a — **now requires `access_key`** | FX | — | — | **Reject** |
| **NBP** | **no** | no documented rate limit; docs say 93-day range (not enforced today) | PLN FX (A/B/C), PLN gold | macro + fx tables | plain `httpx` | **Recommend** |
| **FRED** | yes (free) | **120 req/min — UNVERIFIED** (no primary page found) | macro, rates, some commodity/FX | macro table | `fredapi` 0.5.2 (2024-05-05) | **Recommend** |
| **Eurostat** | **no** | no documented quota | EU macro (HICP etc.) | macro table (JSON-stat) | `eurostat` 1.1.1 (2024-06-13) | Conditional |
| **World Bank** | **no** | no documented quota | annual macro | macro table | `wbdata` 1.1.0 (2025-10-05) | Conditional (low value) |
| **LBMA** | **no** | no quota, but **IBA licence required** | gold/silver/Pt/Pd fixings from 1968 | macro table (3 ccy/row) | plain `httpx` | Conditional (licence) |
| **GoldAPI.io** | yes | **100 req/month — UNVERIFIED primary** | metals spot (no OHLC) | poor | — | **Reject** |
| **metals-api** | yes | **no free plan**; from $19.99/mo | metals OHLC | good | — | **Reject** |
| **Alpaca** | yes (free) | **200 calls/min, 7+ yrs, IEX feed, WS ≤ 30 symbols** | US equities/ETF, crypto, options (indicative) | very good | `alpaca-py` 0.44.0 (2026-08-11) | **Recommend** |
| **Interactive Brokers** | account | needs running TWS/Gateway + daily re-auth + paid subs | everything | good | `ib-async` 2.1.0 (2025-12-08) | **Reject (ops)** |
| **Deribit** | **no** | public | crypto derivatives | good (parallel arrays) | plain `httpx` | Conditional |
| **Bybit** | **no** | public | crypto derivatives | good | `pybit` 5.17.0 (2026-07-14) | Conditional |

---

## 3. Detail — general market-data APIs

### 3.1 Stooq — **REJECT (new, material finding)**

- **URL:** `https://stooq.com/q/d/l/?s=xauusd&i=d` — **checked 2026-09-18**
- **What changed:** Stooq's CSV download endpoints no longer return CSV to a plain HTTP client.
  Both `stooq.com` and `stooq.pl` respond **HTTP 200 with `Content-Type: text/html`** containing a
  JavaScript proof-of-work challenge:

  ```html
  <noscript>This site requires JavaScript to verify your browser.</noscript>
  <script>… crypto.subtle.digest("SHA-256", c+n) … until hash starts with "0000" …
      fetch("/__verify", {method:"POST", body:"c=…&n="+n, credentials:"same-origin"}) …</script>
  ```

  It is a hashcash gate (SHA-256, difficulty 4 hex zeros) whose solution is POSTed to `/__verify` to
  obtain a session cookie.
- **Verified it is not just user-agent filtering:** identical HTML returned with a current Chrome
  UA string, on both `stooq.com` and `stooq.pl`, for `xauusd`, `xagusd`, `cb.f`, `hg.f`, `eurusd`
  and `wig20`, at `i=d` and `i=5`.
- **Assessment:** the challenge is mechanically solvable from Python without a browser, but doing so
  is deliberately circumventing an anti-automation measure the operator just installed. That is a
  clear ToS risk and a permanently fragile dependency for an unattended daemon. **This research does
  not recommend implementing a bypass.**
- **Knock-on:** `pandas-datareader` 0.11.1 (2026-06-24, `requires_python >=3.11`) ships
  `StooqDailyReader`, which hits the same wall. Whether it has since gained a workaround is
  **UNVERIFIED**.
- **Verdict: reject.** This is the single biggest change versus the conventional wisdom — Stooq used
  to be *the* obvious keyless, license-friendly cross-check for exactly FinStream's symbol set.

### 3.2 Alpha Vantage — **REJECT**

- **Source:** <https://www.alphavantage.co/premium/> — checked 2026-09-18.
- **Free tier: "25 API requests per day."** Paid from $49.99/mo (75 req/min).
- 25 requests/day cannot cover even one daily pass over FinStream's ~22 symbols with retries.
- **Client:** `alpha_vantage` 3.0.0, released **2024-07-18**, `requires_python` unset (no declared
  3.12 guarantee), MIT. <https://pypi.org/pypi/alpha_vantage/json>
- **Verdict: reject** — free tier is not viable for a scheduled daemon.

### 3.3 Twelve Data — **CONDITIONAL**

- **Source:** <https://twelvedata.com/pricing> — checked 2026-09-18. Basic (free):
  **800 API credits/day, 8 credits/min**, 3 markets, "8 trial WS" credits, *"internal non-display
  usage"*, described as *"for personal, internal, and non-commercial purposes."*
- **Credit cost:** <https://twelvedata.com/docs> — `time_series` costs **1 API credit per symbol**
  (checked 2026-09-18).
- **Timezone gotcha:** the docs define `datetime` as *"Datetime at local exchange time referring to
  when the bar with specified interval was opened"*, with an optional `timezone` parameter accepting
  `UTC` or an IANA name. FinStream must pass `timezone=UTC` explicitly, or normalise on ingest
  (allowed under GR-1).
- **Client:** `twelvedata` 1.4.0, released **2026-04-27**, `requires_python !=3.0.*…,>=2.7`, MIT —
  actively maintained. <https://pypi.org/pypi/twelvedata/json>
- **Verdict: conditional.** 800/day is genuinely workable (22 symbols × 2 jobs ≈ 44 credits/day) and
  it covers metals, FX, indices and crypto in one API — the broadest single free alternative.
  Blocker is the non-commercial / non-display licence wording; fine for a private stack.

### 3.4 Finnhub — **CONDITIONAL**

- **Free tier: 60 API calls/min** — this number comes from secondary sources only
  (<https://apicostcalc.com/finnhub.html>, GitHub issues). The official
  <https://finnhub.io/docs/api/rate-limit> page is a JavaScript SPA and its rate-limit text could not
  be extracted. **UNVERIFIED from primary source.**
- **Endpoint schema (verified 2026-09-18** by parsing `window.docSchema` from
  <https://finnhub.io/docs/api>, 116 paths**):** `/stock/candle`, `/crypto/candle` and
  `/forex/candle` all exist. `/stock/candle` states *"Daily data will be adjusted for Splits.
  Intraday data will remain unadjusted. Only 1 month of intraday will be returned at a time."*
- **Critical unknown:** the schema does **not** mark `/stock/candle` as premium, but multiple
  community reports say free accounts get `403` on US stock candles. Every probe without a valid key
  returns `401 {"error":"Please use an API key."}` (and `token=demo` is rejected), so this **cannot
  be settled without creating an account**. **UNVERIFIED — must be tested with a real free key
  before any plan commits to Finnhub for bars.**
- **Client:** `finnhub-python` 2.4.29, released **2026-06-24**, `requires_python` unset, Apache-2.0.
  <https://pypi.org/pypi/finnhub-python/json>
- **Verdict: conditional**, gated entirely on that one signup test. Its `/stock/symbol` and
  `/index/constituents` endpoints are separately attractive for a reference-data table.

### 3.5 Tiingo — **CONDITIONAL (good EOD cross-check)**

- **Source:** <https://www.tiingo.com/pricing> — checked 2026-09-18.
  Free "Starter": **50 req/hour, 1000 req/day, 500 unique symbols/month, 1 GB bandwidth/month.**
  Includes Tiingo EOD "Composite Prices", Tiingo Crypto, and the IEX feed. Fundamentals are a paid
  add-on.
- **Licence (important):** both free and paid carry an *"Internal Use Only"* licence — users
  *"may only use the data for your own personal use and you may not display or share the data with
  another person or organization."* A public FinStream dashboard fed by Tiingo would breach this.
- **Client:** `tiingo` 0.16.1, released **2025-04-05**, `requires_python` unset, MIT.
  <https://pypi.org/pypi/tiingo/json>
- **Verdict: conditional.** Excellent quality EOD cross-check for a private stack; the 500-unique-
  symbols/month cap is generous relative to FinStream's ~22 symbols. Licence must be recorded.

### 3.6 Polygon.io → **Massive** — **CONDITIONAL (rebrand finding)**

- **Rebrand confirmed:** `https://polygon.io/pricing` returns **HTTP 301 → `https://massive.com/pricing`**
  (observed 2026-09-18). Polygon.io announced it became **Massive effective 2025-10-30**
  (<https://massive.com/blog/polygon-is-now-massive>). Existing APIs, accounts and SDKs continue to
  work; both domains run in parallel.
- **Free "Stocks Basic":** **5 API calls/minute, 2 years historical data, End-of-Day only, no
  WebSocket** (<https://massive.com/pricing>, checked 2026-09-18).
  Paid: Starter $29/mo (unlimited calls, 5 yr), Developer $79/mo (10 yr), Advanced $199/mo
  (real-time, 20+ yr).
- **Client:** `polygon-api-client` 1.16.3, released **2025-10-30**, `requires_python >=3.9,<4.0`,
  MIT — official. <https://pypi.org/pypi/polygon-api-client/json>
- **Verdict: conditional.** 5 calls/min is tight but *sufficient* for a once-a-day EOD job over ~22
  symbols (≈5 minutes of wall clock). The 2-year history cap limits backfill. Aggregates map cleanly
  to `market_prices` (`t` is a ms UTC epoch). Reasonable second-choice cross-check after Alpaca.

### 3.7 EOD Historical Data (EODHD) — **REJECT**

- **Source:** <https://eodhd.com/pricing> — checked 2026-09-18. Free: **20 API calls/day**, data
  range **"Past year"**, **personal use only**, 500-call welcome bonus.
- Paid from $19.99/mo (100 000 calls/day, 30+ yrs).
- **Client:** `eodhd` 1.0.32, released **2024-12-18**, `requires_python` unset, MIT.
  <https://pypi.org/pypi/eodhd/json>
- **Verdict: reject** on the free tier (20/day). Worth revisiting only if the project ever buys a
  plan — its global exchange coverage is the best value at $19.99.

### 3.8 Financial Modeling Prep — **CONDITIONAL**

- **Source:** <https://site.financialmodelingprep.com/developer/docs/pricing> — checked 2026-09-18
  (page is Cloudflare-protected; text extracted via a browser-UA fetch).
  Free "Basic": **250 Calls/Day**, End-of-Day Historical Data, Profile and Reference Data, 150+
  endpoints.
- **Bandwidth:** *"API usage has a trailing 30 days bandwidth limit of: Free plan - 500MB…"*
- **Licence:** *"Displaying or redistributing data sourced from FMP requires a specific Data Display
  and Licensing Agreement with FMP."*
- Paid: Starter $19/mo (300 calls/min, 5 yr, US only), Premium $49/mo (750/min, 30 yr, + UK/Canada),
  Ultimate $99/mo (3000/min, global).
- **Client:** `fmpsdk` 20260824.0, released **2026-08-24**, `requires_python >=3.9`, BSD-3-Clause —
  actively maintained, typed responses, automatic retries.
  <https://pypi.org/pypi/fmpsdk/json>
- **Verdict: conditional.** 250 calls/day is enough for a daily job; breadth (commodities, FX,
  crypto, ETF, macro) is strong. The display/redistribution clause and the fact that the free tier's
  symbol coverage is **UNVERIFIED** (could not confirm whether free is US-only) hold it back.

### 3.9 Marketstack — **REJECT**

- **Source:** <https://marketstack.com/product> — checked 2026-09-18. Free: **100 Requests/month**,
  1 Year History, End-of-Day only, no intraday.
- Paid: Basic $9.99/mo (10 000 req/mo, 10 yr), Professional $49.99/mo, Business $149.99/mo.
- Only an **unofficial** client exists: `marketstack` 0.6.2, released **2022-11-09**
  (<https://pypi.org/pypi/marketstack/json>) — "Inofficial Marketstack OpenAPI Python client".
- **Verdict: reject.** 100 requests/month is roughly four days of a single daily job.

### 3.10 Databento — **REJECT on cost, note as upgrade path**

- **Source:** <https://databento.com/pricing> — checked 2026-09-18. **No free tier**; *"Sign up today
  and get $125 in free credits"* (historical only, **expire after 6 months**, one set per team).
  Usage-based $/GB plus subscriptions **$199/mo (Standard) to $4,500/mo (Unlimited)**. Live data
  requires additional licensing fees. 650 000+ symbols across CME, CBOT, NYMEX, COMEX, CFE, Eurex,
  ICE, EEX and Databento US Equities; 16+ years of history. Pricing is by *uncompressed binary size*.
- **Client:** `databento` 0.86.0, released **2026-09-01**, `requires_python >=3.10` — official, very
  actively maintained. <https://pypi.org/pypi/databento/json>
- **Verdict: reject** for a cost-sensitive hobby stack. But it is the *only* candidate here that
  provides genuine COMEX gold (`GC`) and NYMEX crude (`CL`) futures data with real exchange volume
  and proper contract metadata — i.e. the correct answer if FinStream ever gets serious about
  commodity futures. Its data would also motivate a `raw.instruments` table (contract expiries,
  roll dates).

### 3.11 Nasdaq Data Link / Quandl — **REJECT**

- `https://data.nasdaq.com/api/v3/datasets/LBMA/GOLD.json` returns an **Imperva/Incapsula
  interstitial** to a plain HTTP client (observed 2026-09-18), so free-tier limits and current
  dataset availability could not be confirmed. **UNVERIFIED.**
- **Client:** `nasdaq-data-link` 1.0.4, released **2022-08-29** — **four years without a release**
  (<https://pypi.org/pypi/nasdaq-data-link/json>).
- Historically the free `LBMA/GOLD` and `LBMA/SILVER` datasets were the standard free metals source;
  most of the genuinely free Quandl catalogue has been retired or moved behind Nasdaq accounts
  (**UNVERIFIED**).
- **Verdict: reject.** Unmaintained client + bot-protected API + unverifiable free catalogue.
  **Use LBMA's own JSON endpoints instead (§5.1)** — same data, direct from the publisher.

---

## 4. Detail — crypto-native

### 4.1 Binance public API — **RECOMMEND**

- **REST verified 2026-09-18:**
  `https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=2` → no key required,
  returns `[[openTime_ms, "open","high","low","close","volume", closeTime_ms, quoteVolume, trades,
  takerBuyBase, takerBuyQuote, "0"], …]`.
- **Rate limits (read live from `/api/v3/exchangeInfo`, 2026-09-18):**
  `REQUEST_WEIGHT` **6000 per minute**, `RAW_REQUESTS` **300 000 per 5 minutes**. Response headers
  return `X-MBX-USED-WEIGHT-1M` for self-throttling (observed: weight 4 for a 2-row klines call).
- **Page size:** `limit=1500` returned **1000 rows** — the current hard cap is **1000 candles per
  call** (observed 2026-09-18; older docs said 1500).
- **History:** `startTime=0` on `BTCUSDT` 1d returns the first bar at **2017-08-17T00:00:00Z** — full
  listing history, no cap.
- **Mapping to `raw.market_prices`:** essentially perfect. `openTime_ms / 1000` → `ts` (UTC, bar
  *start* — matches FinStream's semantics exactly), open/high/low/close/volume map 1:1,
  `adj_close = NULL` (crypto has no corporate actions). `volume` is base-asset volume and is
  fractional, so note that `market_prices.volume BIGINT` will **truncate** crypto volumes — either
  store quote volume, round, or accept the loss. **This is the one schema friction point.**
- **WebSocket verified live 2026-09-18:** `wss://stream.binance.com:9443/ws/btcusdt@kline_1m`
  delivered a frame within seconds, keyless (see §7).
- **Client:** `python-binance` 1.0.37, released **2026-06-08**, `requires_python` unset, MIT
  (<https://pypi.org/pypi/python-binance/json>). Community-maintained but long-lived. Given
  EL-only requirements, **plain `httpx` is arguably the better dependency** — the endpoint is two
  query parameters and an array.
- **Risks:** Binance geo-restricts some jurisdictions (notably the US, which redirect to
  `binance.us`); from Poland `api.binance.com` is reachable (verified). Terms permit public market
  data use; **redistribution terms UNVERIFIED**.
- **Verdict: recommend.** Best-in-class free crypto OHLCV, zero key management (no GR-5 surface),
  full history, and the cheapest route to event-driven.

### 4.2 Coinbase Exchange — **CONDITIONAL**

- **Verified 2026-09-18:** `https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400`
  — keyless, returned **350 rows** (oldest 2025-10-03), newest-first.
- **Field order trap:** the array is `[time, low, high, open, close, volume]` — **low and high come
  before open/close**, unlike every other provider here. A naive positional mapping silently corrupts
  the table.
- Backfilling deep history requires many paginated `start`/`end` calls at ≤300–350 candles each.
- **Client:** `coinbase-advanced-py` 1.8.4, released **2026-06-19**, `requires_python >=3.8`
  (<https://pypi.org/pypi/coinbase-advanced-py/json>) — but that SDK targets *Advanced Trade*, not
  the public Exchange candles endpoint. **Plain `httpx` is the right choice here.**
- **Verdict: conditional.** Useful as a second crypto source (USD-quoted, US-regulated venue, good
  BTC-USD cross-check against Binance's BTCUSDT which is stablecoin-quoted). Low priority.

### 4.3 Kraken — **CONDITIONAL (history-limited)**

- **Verified 2026-09-18:** `https://api.kraken.com/0/public/OHLC?pair=XXBTZUSD&interval=1440`
  returned **721 rows**, oldest 2024-09-27. Re-requesting with `since=1420070400` (2015-01-01)
  returned **the same 721 rows** — the `since` parameter cannot reach further back. Kraken's public
  OHLC endpoint only serves the **most recent ~720 candles per interval**.
- Row shape `[time, open, high, low, close, vwap, volume, count]` — clean mapping, `vwap` and `count`
  have no home in `market_prices`.
- **Client:** `krakenex` 2.2.2, released **2024-07-01**, `requires_python >=3.3`
  (<https://pypi.org/pypi/krakenex/json>).
- **WebSocket:** `wss://ws.kraken.com/v2` connects keyless (verified 2026-09-18, `api_version v2`,
  `system: online`) and offers an `ohlc` channel.
- **Verdict: conditional.** Fine for keeping recent bars fresh, useless for backfill. Binance
  dominates it on every axis except venue diversity.

### 4.4 CCXT — **RECOMMEND as the adapter, if more than one exchange**

- `ccxt` **4.5.78**, released **2026-09-07**, `requires_python >=3.10`
  (<https://pypi.org/pypi/ccxt/json>). Release cadence is essentially daily.
- Provides a unified `fetch_ohlcv(symbol, timeframe, since, limit)` returning
  `[timestamp_ms, open, high, low, close, volume]` across 100+ exchanges — which is *exactly* the
  `market_prices` row.
- **Trade-off:** CCXT is a large package (it bundles every exchange's implementation), and the
  frequent releases fight GR-8's exact pinning in the sense that pins go stale quickly. For **one**
  exchange, direct `httpx` is leaner; for **three or more**, CCXT pays for itself and removes the
  Coinbase field-order trap entirely.
- **Verdict: recommend conditionally** — adopt only when the plan targets multiple exchanges.

### 4.5 CoinGecko — **REJECT for bars, conditional for reference data**

- **Verified 2026-09-18:** `/api/v3/ping` works keyless; `/api/v3/coins/bitcoin/ohlc?vs_currency=usd&days=7`
  returns `[[ts_ms, open, high, low, close], …]` — **no volume at all**.
- **Worse for GR-1:** CoinGecko *chooses* the candle granularity from the `days` parameter (the 7-day
  request returned 4-hourly candles). The bar interval is therefore not something FinStream requests;
  it is derived server-side. Storing that in `bar_interval` means recording a granularity the caller
  did not choose and that can change.
- **Limits:** <https://www.coingecko.com/en/api/pricing> and <https://docs.coingecko.com/docs/common-errors-rate-limit>
  (checked 2026-09-18): Demo plan **100 calls/min, 10 000 calls/month**; the keyless public API is
  *"IP-based rate limiting — shared across all users on the same IP"* with **no published number**.
  **Attribution required**: must display *"Data provided by CoinGecko"* with a hyperlink.
- **Client:** `pycoingecko` 3.2.0, released **2024-11-13** (<https://pypi.org/pypi/pycoingecko/json>).
- **Verdict: reject** as an OHLCV source — it is aggregated/derived data, which is the exact shape
  GR-1 says is a poor fit. **Conditional** as a future reference-data source (market cap, circulating
  supply, coin metadata) into a separate table.

### 4.6 Deribit / Bybit — **CONDITIONAL (derivatives niche)**

- **Deribit verified 2026-09-18:** `https://www.deribit.com/api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&resolution=1D`
  — keyless. Returns **parallel arrays**: `{ticks:[…], open:[…], high:[…], low:[…], close:[…],
  volume:[…], cost:[…], status:"ok"}`. Needs zipping into rows; ticks are ms UTC.
- **Bybit verified 2026-09-18:** `https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=D`
  — keyless. Returns `list: [[start_ms, open, high, low, close, volume, turnover], …]`,
  **newest-first**. Client `pybit` 5.17.0, released **2026-07-14**, `requires_python >=3.10`, official
  (<https://pypi.org/pypi/pybit/json>).
- **Verdict: conditional.** Both map fine, both keyless, both have WS. Only worth adding if FinStream
  decides it cares about perpetuals/funding rates — which would itself want a new table
  (`raw.funding_rates`). Not a priority.

---

## 5. Detail — metals, FX, central banks, macro

### 5.1 LBMA precious-metal benchmarks — **CONDITIONAL (best free metals, licence caveat)**

- **Endpoints verified 2026-09-18** (`https://prices.lbma.org.uk/json/<name>.json`, no key):

| Endpoint | Rows | First | Last |
|---|---|---|---|
| `gold_am.json` | 14 837 | 1968-01-02 | 2026-09-17 |
| `gold_pm.json` | 14 685 | 1968-04-01 | 2026-09-17 |
| `silver.json` | 14 848 | 1968-01-02 | 2026-09-17 |
| `platinum_am.json` / `platinum_pm.json` | 9 213 / 9 144 | 1990-04-02 | 2026-09-17 |
| `palladium_am.json` / `palladium_pm.json` | 9 213 / 9 147 | 1990-04-02 | 2026-09-17 |

- **Shape:** `{"is_cms_locked":0, "d":"2026-09-17", "v":[4368.1, 3265.15, 3800.25]}` — the `v` array
  is `[USD, GBP, EUR]`. Per <https://www.lbma.org.uk/prices-and-data/precious-metal-prices>
  (checked 2026-09-18), *"All are quoted in USD, with GBP and EUR available as indicative prices for
  settlement only."*
- **Auction times (London):** gold 10:30 & 15:00, silver 12:00, platinum 09:45 & 14:00, palladium
  after platinum. So `am`/`pm` are distinct fixings, not OHLC — they need distinct series ids and a
  timezone-aware timestamp (Europe/London, DST-shifting).
- **Licensing — the blocker:** the LBMA page states *"A licence from IBA is required in order to
  obtain, use or redistribute real-time or historical benchmark data"*, and historical tabular data
  has moved to the MyLBMA Portal behind IBA licensing. The `prices.lbma.org.uk/json/` endpoints are
  the *de facto* public feed behind the LBMA's own website chart; they are not documented as a public
  API.
- **Verdict: conditional.** Technically the best free metals data anywhere — 58 years of the actual
  benchmark that the gold market settles on, three currencies, no key, no quota. But it is (a) an
  undocumented site endpoint, i.e. the same category of risk as yfinance, and (b) explicitly covered
  by an IBA licence requirement. **If adopted, the licence position must be recorded in the plan's
  Exceptions/Risks section and the data must not be republished.** For a private, single-maintainer
  stack it is defensible; for anything public-facing it is not.

### 5.2 GoldAPI.io / metals-api — **REJECT both**

- **GoldAPI.io:** the site is a JavaScript SPA; `https://www.goldapi.io/` and `/pricing` return only
  *"You need to enable JavaScript to run this app."* to a plain fetch (checked 2026-09-18). Secondary
  sources report a **free plan of 100 requests/month**, paid from $4.99 to $129/mo
  (<https://goldprice.dev/comparison/goldapi-io-vs-gold-api>) — **UNVERIFIED from primary source.**
  It returns **spot prices, not OHLCV bars**, which is a poor fit regardless.
- **metals-api:** <https://metals-api.com/pricing> (checked 2026-09-18) — **no free plan exists.**
  Lowest tier "Copper" is **$19.99/mo, 2 500 API calls/mo then $0.032435 each**; it does include
  Historical Rates, Time-Series (≤30 days/request) and an Open/High/Low/Close endpoint.
- **Verdict: reject both.** LBMA gives better data for free; Yahoo's `GC=F`/`SI=F` already give
  tradeable OHLCV.

### 5.3 NBP (Narodowy Bank Polski) — **RECOMMEND (top pick)**

- **Source:** <https://api.nbp.pl/> — checked 2026-09-18. No API key. JSON or XML
  (`?format=json` or `Accept: application/json`). HTTPS mandatory since 2025-08-01.
- **Verified live 2026-09-18:**
  - `/api/cenyzlota/last/3/?format=json` → `[{"data":"2026-09-17","cena":523.97}, …]` — PLN price of
    1 g of gold, fineness 1000. History from **2013-01-02** (confirmed: a 2013-01-02 query returns
    `165.83`).
  - `/api/exchangerates/rates/a/eur/last/2/?format=json` → `{"table":"A","code":"EUR",
    "rates":[{"no":"181/A/NBP/2026","effectiveDate":"2026-09-17","mid":4.3632}]}`. FX history from
    **2002-01-02**.
  - `/api/exchangerates/rates/c/usd/last/1/?format=json` → `{"bid":3.7377,"ask":3.8133}` — table C is
    **bid/ask**, not a mid.
- **Documented range limit vs. observed:** the docs state *"pojedyncze zapytanie nie może obejmować
  przedziału dłuższego, niż 93 dni"* (max 93-day span). **Observed 2026-09-18: a full-year request**
  `/api/exchangerates/rates/a/usd/2025-01-01/2025-12-31/` **succeeded, returning 251 rows**, and the
  same for `/api/cenyzlota/2025-01-01/2025-12-31/`. Do **not** rely on the relaxation — implement
  ≤93-day chunking to match the documented contract.
- **Rate limits:** none documented (**UNVERIFIED** whether throttling exists in practice).
- **Licence:** the page carries only *"Copyright © 2024 Narodowy Bank Polski. Wszystkie prawa
  zastrzeżone"* with no explicit API terms — **UNVERIFIED** redistribution position.
- **Client:** none needed; plain `httpx` + `pydantic`.
- **Mapping:** a single value per day → **`raw.macro_series`**, not `market_prices`. Table C (bid/ask)
  wants a small `raw.fx_rates` table.
- **Verdict: recommend.** Highest value-per-unit-of-effort of any source here: official, keyless,
  stable, directly relevant to a Polish maintainer, and it supplies a *PLN-denominated* gold series
  that Yahoo simply does not have.

### 5.4 ECB Data Portal (SDMX) — **RECOMMEND**

- **Verified live 2026-09-18:**
  `https://data-api.ecb.europa.eu/service/data/EXR/D.PLN.EUR.SP00.A?startPeriod=2026-09-10&format=csvdata`
  → CSV with `KEY,FREQ,CURRENCY,…,TIME_PERIOD,OBS_VALUE,OBS_STATUS,…`; e.g.
  `EXR.D.PLN.EUR.SP00.A,D,PLN,EUR,SP00,A,2026-09-10,4.322,A,…` titled
  *"Polish zloty/Euro ECB reference exchange rate"*, *"2.15 pm (C.E.T.)"*.
- Also verified the policy-rate flow:
  `https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.MRR_FR.LEV?startPeriod=2026-09-01&format=csvdata`
  → `2026-09-01, 2.4` — *"Main refinancing operations – fixed rate tenders"*.
- **No API key, no documented quota.** `format=csvdata` avoids XML/SDMX parsing entirely — a plain
  `httpx` GET plus `csv.DictReader` is the whole integration.
- **Timezone semantics:** `TIME_PERIOD` is a **date**, not an instant, and the reference rate is
  fixed at 14:15 CET. Storing it as a UTC timestamp requires choosing a convention (midnight UTC of
  the reference date is the honest, non-derived choice).
- **Clients:**
  - `sdmx1` **2.27.0**, released **2026-08-07**, `requires_python >=3.10`
    (<https://pypi.org/pypi/sdmx1/json>) — the maintained SDMX library.
  - ⚠️ `pandasdmx` **1.10.0** (2023-02-25) declares `requires_python >=3.9.6,<3.12` — **incompatible
    with FinStream's Python 3.12.** Do not pick it.
  - `ecbdata` 0.1.1, released **2025-03-23** — tiny and not actively maintained.
- **Verdict: recommend**, implemented with plain `httpx` + `csvdata` (no new dependency at all).

### 5.5 Frankfurter — **RECOMMEND (or skip in favour of ECB)**

- **Source:** <https://frankfurter.dev/> — checked 2026-09-18. *"It requires no API key."*
  *"There are no quotas. Requests are rate-limited to prevent abuse, but there are no monthly or
  daily caps."* Free for commercial use. **205 currencies**, history *"back to 1948"*, tracking
  *"daily exchange rates from 98 central banks and official sources"*. **Self-hostable with Docker.**
- **Verified live 2026-09-18:**
  `https://api.frankfurter.dev/v1/2026-09-01..2026-09-05?base=EUR&symbols=PLN,USD` →
  `{"base":"EUR","start_date":"2026-09-01","end_date":"2026-09-04","rates":{"2026-09-01":{"PLN":4.3313,"USD":1.159}, …}}`
  — note weekends are simply absent and `end_date` is clamped to the last business day.
- **Client:** `frankfurter` 2.0.0, released **2025-01-08**, `requires_python >=3.8`
  (<https://pypi.org/pypi/frankfurter/json>) — a thin wrapper; `httpx` is equally easy.
- **Verdict: recommend**, with a caveat: Frankfurter's headline FX data is **ECB-derived**, so
  adopting both ECB and Frankfurter buys little. Pick Frankfurter for its friendlier JSON and
  self-hosting story (a local Docker instance removes the external dependency entirely, which fits
  the self-hosted ethos), or pick ECB for directness. **Do not count them as two independent
  sources.**

### 5.6 exchangerate.host — **REJECT (changed)**

- **Verified 2026-09-18:** `https://api.exchangerate.host/latest?base=EUR` now returns
  `{"success":false,"error":{"code":101,"type":"missing_access_key","info":"You have not supplied an
  API Access Key."}}`.
- It is no longer the keyless free FX API it was widely known as (it now sits behind apilayer-style
  key management). **Free-tier limits UNVERIFIED.**
- **Verdict: reject.** Frankfurter and ECB do the same job with no key.

### 5.7 FRED — **RECOMMEND**

- **Source:** <https://fred.stlouisfed.org/docs/api/api_key.html> — checked 2026-09-18.
  *"All web service requests require an API key to identify requests"* and *"All users of an
  application shall use their own API key."* The key is free (registration required). Whether the
  page states it is free was not extractable — **UNVERIFIED**, but FRED keys are free in practice.
- **Rate limit: 120 requests/minute per key** is the number cited across many third-party wrappers
  (e.g. `fredr`, `fedfred` docs) — **UNVERIFIED: no primary St. Louis Fed page publishing this was
  found.** `/fred/series/observations` documents no rate limit at all.
- **Response shape** (<https://fred.stlouisfed.org/docs/api/fred/series_observations.html>, checked
  2026-09-18): each observation is `{"date":"1929-01-01","value":"1065.9","realtime_start":…,
  "realtime_end":…}`. `file_type` accepts `xml` (default), `json`, `xlsx`, `csv`.
  **Values arrive as strings** and missing observations are the literal `"."`.
- **Vintages:** the `realtime_start`/`realtime_end` fields are FRED's revision model. Storing them is
  what makes the series reproducible — this is a genuine reason the macro table should carry a
  vintage column.
- **Client:** `fredapi` 0.5.2, released **2024-05-05**, `requires_python` unset
  (<https://pypi.org/pypi/fredapi/json>) — thin, pandas-oriented, two years stale. Given GR-8 and the
  EL-only philosophy, **plain `httpx` against `file_type=json` is the better choice** (and avoids
  taking a pandas-shaped dependency for what is a flat list of dicts).
- **Coverage relevant here:** Fed funds, full Treasury constant-maturity curve (DGS1M…DGS30),
  CPI/PCE, unemployment, DXY-like broad dollar indices, WTI/Brent spot (EIA-sourced), and — note —
  the old LBMA gold series (`GOLDAMGBD228NLBM`) was **discontinued**; whether a replacement exists is
  **UNVERIFIED**. Use LBMA direct (§5.1) for gold.
- **Verdict: recommend.** Single biggest expansion of what FinStream can answer, for one free key.

### 5.8 Eurostat — **CONDITIONAL**

- **Verified live 2026-09-18:**
  `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx?format=JSON&geo=PL&coicop=CP00&unit=I15&lastTimePeriod=2`
  → JSON-stat 2.0: `{"version":"2.0","class":"dataset","label":"HICP - monthly data (index)",
  "source":"ESTAT","updated":"2026-02-06T23:00:00+0100","value":{"0":154.7,"1":154.7},
  "id":["freq","unit","coicop","geo","time"],"size":[1,1,1,1,2],"dimension":{…}}`.
  No key, no documented quota.
- **Cost:** JSON-stat is a *sparse, index-addressed* format — you must decode `dimension`/`size` to
  turn `value` keys into (date, value) pairs. That is real parsing work, and getting it wrong
  silently mislabels observations.
- **Client:** `eurostat` 1.1.1, released **2024-06-13**, `requires_python >=3.5`
  (<https://pypi.org/pypi/eurostat/json>) — handles the decoding but is pandas-based and two years
  stale. `sdmx1` 2.27.0 also speaks Eurostat.
- **Verdict: conditional.** Right answer for EU-specific macro (HICP for Poland/euro area), but it
  should wait until `raw.macro_series` exists and FRED/ECB have proven the pattern.

### 5.9 World Bank — **CONDITIONAL (low priority)**

- **Verified live 2026-09-18:**
  `https://api.worldbank.org/v2/country/PL/indicator/NY.GDP.MKTP.CD?format=json&per_page=2` →
  `[{"page":1,"pages":33,"per_page":2,"total":66,"lastupdated":"2026-07-13"},
  [{"indicator":{"id":"NY.GDP.MKTP.CD"},"country":{"id":"PL"},"date":"2025","value":1035491784197.44,…}]]`.
  No key, no documented quota, clean pagination envelope.
- **Client:** `wbdata` 1.1.0, released **2025-10-05**, `requires_python >=3.10,<4`
  (<https://pypi.org/pypi/wbdata/json>) — maintained. Alternative `world-bank-data` 0.1.4 (2024).
- **Verdict: conditional / low priority.** The data is **annual**, which is far too coarse to be
  interesting next to hourly bars. Add only if FinStream grows a genuinely macro-analytical service.

---

## 6. Detail — broker / account-gated

### 6.1 Alpaca Market Data — **RECOMMEND (best cross-check)**

- **Source:** <https://alpaca.markets/data> — checked 2026-09-18. Free plan:
  **200 API calls/min**, **7+ years** of historical data, **100% market coverage** for US Stocks via
  the **IEX** feed (all-exchange SIP is the $99/mo "Algo Trader Plus" tier), US Options
  *"Yes, indicative"*, crypto included, and **WebSocket streaming limited to 30 symbols** (unlimited
  on the paid tier). Real-time via REST is **15-minute delayed** on free; the WebSocket delivers
  real-time for those 30 symbols.
- **Account:** free signup; a paper-trading account is enough for market data (**UNVERIFIED** whether
  funding is required for the free data plan).
- **Client:** `alpaca-py` **0.44.0**, released **2026-08-11**, `requires_python >=3.10,<4.0`,
  Apache-2.0 — **official and actively maintained** (<https://pypi.org/pypi/alpaca-py/json>). This is
  the healthiest official client of any candidate in this report.
- **Mapping:** bars come back as `{t (RFC-3339 UTC), o, h, l, c, v, n, vw}` — `t` is already UTC and
  is the **bar start**, matching `market_prices.ts` exactly. `n` (trade count) and `vw` (VWAP) have
  no column; `adj_close` is NULL unless the adjustment parameter is used.
- **Caveat for cross-checking:** IEX is one venue with a few percent of consolidated volume, so
  **volume will differ materially from Yahoo** and prices can differ at the margins in thin names.
  Compare OHLC on liquid ETFs, not volume.
- **Verdict: recommend.** Best free tier in the report by a wide margin (200/min vs. Alpha Vantage's
  25/day), real official SDK, and it doubles as the WebSocket path for US equities.

### 6.2 Interactive Brokers — **REJECT (operational fit)**

- **Client:** `ib-async` **2.1.0**, released **2025-12-08**, `requires_python >=3.10`
  (<https://pypi.org/pypi/ib-async/json>) — the maintained fork/successor. The original `ib-insync`
  0.9.86 has not been released since **2023-07-02** (<https://pypi.org/pypi/ib-insync/json>) and is
  abandoned.
- **Structural blockers** (the IBKR docs pages return HTTP 403 to automated fetches, so the specifics
  below are **UNVERIFIED from primary source**, but they are the well-known operating model): the API
  requires a **running TWS or IB Gateway process** alongside the daemon, with a **daily
  re-authentication / session restart**, a funded account, and **paid market-data subscriptions** per
  exchange. Historical data is additionally pacing-limited.
- **Verdict: reject.** A GUI-adjacent process needing daily human re-auth is the opposite of "a
  long-running daemon in Docker Compose that never dies" (GR-3).

---

## 7. Evidence — live probes (all 2026-09-18)

```text
# Stooq is bot-gated (identical with and without a browser UA, .com and .pl)
$ curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "https://stooq.com/q/d/l/?s=xauusd&i=d"
200 text/html; charset=utf-8
  body: <noscript>This site requires JavaScript to verify your browser.</noscript>
        ... crypto.subtle.digest("SHA-256", c+n) until 4 leading zeros ... POST /__verify ...

# Binance: full history, generous limits, perfect shape
$ curl -s ".../api/v3/klines?symbol=BTCUSDT&interval=1d&startTime=0&limit=1"   -> oldest 2017-08-17T00:00:00
$ curl -s ".../api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1500" | len     -> 1000   (hard cap)
$ curl -s ".../api/v3/exchangeInfo?symbol=BTCUSDT" .rateLimits
   REQUEST_WEIGHT  MINUTE 1  -> 6000
   RAW_REQUESTS    MINUTE 5  -> 300000
   response header: x-mbx-used-weight-1m: 4

# Kraken REST cannot backfill
$ .../0/public/OHLC?pair=XXBTZUSD&interval=1440                  -> 721 rows, oldest 2024-09-27
$ .../0/public/OHLC?pair=XXBTZUSD&interval=1440&since=1420070400 -> 721 rows, oldest 2024-09-27  (since ignored)

# Coinbase page size
$ .../products/BTC-USD/candles?granularity=86400 -> 350 rows, oldest 2025-10-03
  row shape: [time, LOW, HIGH, open, close, volume]   <-- non-standard field order

# Keyless WebSockets (python websockets)
OK wss://stream.binance.com:9443/ws/btcusdt@kline_1m
   {"e":"kline","E":1789689218030,"s":"BTCUSDT","k":{"t":1789689180000,"T":1789689239999,"i":"1m",
    "o":"76421.07000000","c":"76408.36000000","h":"76421.08000000","l":"76408.35000000",
    "v":"2.62202000","n":423,"x":false,...}}
OK wss://ws.kraken.com/v2
   {"channel":"status","data":[{"version":"2.0.10","system":"online","api_version":"v2"}]}

# NBP: keyless, and the documented 93-day cap is not currently enforced
$ .../api/cenyzlota/last/3/?format=json
   [{"data":"2026-09-15","cena":520.67},{"data":"2026-09-16","cena":516.15},{"data":"2026-09-17","cena":523.97}]
$ .../api/exchangerates/rates/c/usd/last/1/?format=json
   {"table":"C",...,"rates":[{"effectiveDate":"2026-09-17","bid":3.7377,"ask":3.8133}]}
$ .../api/exchangerates/rates/a/usd/2025-01-01/2025-12-31/?format=json
   251 rows, 2025-01-02 .. 2025-12-31        (docs say max 93 days)

# LBMA: 58 years of the actual benchmark, keyless
$ https://prices.lbma.org.uk/json/gold_am.json -> 14837 rows, 1968-01-02 .. 2026-09-17
   last: {"is_cms_locked":0,"d":"2026-09-17","v":[4307.25, 3215.79, 3752.91]}   # [USD, GBP, EUR]

# ECB SDMX csvdata: no key, no parsing library needed
$ .../service/data/EXR/D.PLN.EUR.SP00.A?startPeriod=2026-09-10&format=csvdata
   EXR.D.PLN.EUR.SP00.A,D,PLN,EUR,SP00,A,2026-09-10,4.322,A,...  "Polish zloty/Euro ECB reference exchange rate"

# exchangerate.host now needs a key
$ https://api.exchangerate.host/latest?base=EUR
   {"success":false,"error":{"code":101,"type":"missing_access_key",...}}

# Nasdaq Data Link is behind Imperva
$ https://data.nasdaq.com/api/v3/datasets/LBMA/GOLD.json?rows=2
   <html ...>_Incapsula_Resource...
```

---

## 8. Proposed new tables

These are **sketches for a future plan**, not a schema change. Any real version needs GR-6 treatment
(idempotent DDL, `docs/data-model.md` updated in the same change).

### 8.1 `raw.macro_series` — FRED, ECB, Eurostat, World Bank, NBP gold, LBMA

The dominant new shape: one scalar observation per (source, series, period), with revisions.

```sql
CREATE TABLE IF NOT EXISTS raw.macro_series (
    source        TEXT         NOT NULL,   -- 'fred' | 'ecb' | 'nbp' | 'lbma' | 'eurostat' | 'worldbank'
    series_id     TEXT         NOT NULL,   -- source's own id: 'DGS10', 'EXR.D.PLN.EUR.SP00.A', 'gold_pm.USD'
    ts            TIMESTAMPTZ  NOT NULL,   -- observation period start, UTC (see note)
    value         DOUBLE PRECISION,        -- NULL for FRED's "." missing marker
    vintage       TIMESTAMPTZ  NOT NULL DEFAULT now(),  -- FRED realtime_start; now() elsewhere
    ingested_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT macro_series_pk PRIMARY KEY (source, series_id, ts)
);
```

Design notes:

- **Mirrors `market_prices` deliberately:** same `source`-first key, same upsert-on-conflict story,
  same `ingested_at`/`updated_at` pair, so the existing writer and its idempotency tests port over
  with minimal new logic (GR-2).
- **`ts` is a period *start*, not an instant.** A monthly CPI print for 2026-08 is `2026-08-01T00:00Z`.
  Storing the reporting frequency is worth a `freq TEXT` column if multiple frequencies share a
  source; otherwise it is implied by the series id.
- **Multi-currency sources fan out into series ids**, not columns: LBMA gold PM becomes three rows
  (`gold_pm.USD`, `gold_pm.GBP`, `gold_pm.EUR`). That keeps the table narrow and avoids a currency
  column that is NULL for 90% of rows.
- **`vintage` is the honest way to record revisions** without deriving anything. If FinStream does
  not care about vintages initially, drop the column — but adding it later to a hypertable is
  cheaper than reconstructing lost history.

### 8.2 `raw.fx_rates` — NBP table C, and any bid/ask source

```sql
CREATE TABLE IF NOT EXISTS raw.fx_rates (
    source        TEXT         NOT NULL,   -- 'nbp'
    base_ccy      CHAR(3)      NOT NULL,   -- 'USD'
    quote_ccy     CHAR(3)      NOT NULL,   -- 'PLN'
    ts            TIMESTAMPTZ  NOT NULL,   -- effectiveDate at 00:00 UTC
    mid           DOUBLE PRECISION,        -- NBP table A/B
    bid           DOUBLE PRECISION,        -- NBP table C
    ask           DOUBLE PRECISION,
    table_code    TEXT,                    -- 'A' | 'B' | 'C'  (source-native provenance)
    ingested_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT fx_rates_pk PRIMARY KEY (source, base_ccy, quote_ccy, table_code, ts)
);
```

Alternative: skip this table and push NBP mid rates into `macro_series` as
`nbp.A.USDPLN`. That is simpler and probably right for a first pass — **only build `fx_rates` if
bid/ask actually matters**, because a two-column spread does not fit `macro_series` without
inventing derived series ids like `…USDPLN.bid`.

### 8.3 `raw.instruments` — reference / metadata

```sql
CREATE TABLE IF NOT EXISTS raw.instruments (
    source        TEXT         NOT NULL,   -- 'binance' | 'finnhub' | 'alpaca'
    symbol        TEXT         NOT NULL,
    name          TEXT,
    asset_class   TEXT,                    -- source's own classification, unmapped
    currency      TEXT,
    exchange      TEXT,
    mic           TEXT,
    isin          TEXT,
    status        TEXT,                    -- 'active' | 'delisted' | source-native
    first_seen_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    raw_payload   JSONB,                   -- the source record verbatim (EL-only friendly)
    CONSTRAINT instruments_pk PRIMARY KEY (source, symbol)
);
```

Notes: this is slowly-changing reference data, not a time series — no hypertable. The `raw_payload`
JSONB column is the most EL-faithful option: it stores what the source said without FinStream
choosing a canonical schema (that mapping belongs downstream, per GR-1's spirit). This also finally
gives the dashboard proper instrument names without hardcoding them.

Feeders: Binance `/api/v3/exchangeInfo` (keyless), Finnhub `/stock/symbol` and `/index/constituents`,
Alpaca assets, Massive tickers.

### 8.4 `raw.corporate_actions` — splits & dividends

```sql
CREATE TABLE IF NOT EXISTS raw.corporate_actions (
    source        TEXT         NOT NULL,
    symbol        TEXT         NOT NULL,
    action_type   TEXT         NOT NULL,   -- 'split' | 'dividend'
    ex_date       DATE         NOT NULL,
    value         DOUBLE PRECISION NOT NULL, -- split ratio, or dividend amount
    currency      TEXT,
    ingested_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT corporate_actions_pk PRIMARY KEY (source, symbol, action_type, ex_date)
);
```

This is the table that would finally *explain* `market_prices.adj_close` revisions, which the data
model currently notes but cannot account for. Feeders: `yfinance` `.actions` (already a dependency —
so this is the cheapest of the four new tables), Tiingo, FMP.

---

## 9. Options summary

| Option | Pros | Cons |
|---|---|---|
| **A. Do nothing; stay single-source** | Zero work, zero new secrets, zero new licence questions | Yahoo is an unofficial scrape with no fallback; no macro axis; no PLN; no streaming path |
| **B. Add NBP only** | Keyless, official, small, Polish-relevant; forces `macro_series` into existence cheaply | Doesn't address the "Yahoo could break" risk at all |
| **C. NBP + Binance (recommended first slice)** | Both keyless → no GR-5 secret surface; one new table + one new `market_prices` source; Binance also unlocks WS later | Still no equity/ETF redundancy |
| **D. C + FRED + Alpaca (recommended target)** | Full coverage of the four identified gaps: PLN, crypto-native, macro, and independent equity cross-check; two free API keys total | Two secrets to manage; `macro_series` + a second bar source is a real plan, not a small change |
| **E. Add a broad commercial free tier (Twelve Data / FMP)** | One integration covers metals, FX, indices, crypto at once | Non-commercial/display licences; daily caps; yet another key; duplicates Yahoo rather than complementing it |
| **F. Buy Databento / EODHD** | Genuine institutional-grade futures & global EOD | $19.99–$199+/mo against an explicitly cost-sensitive project |

## 10. Recommendation

**Option D, staged.** Sequence it as two plans so each lands as a verified slice:

1. **Plan A — "keyless sources":** NBP + Binance. Introduces `raw.macro_series` (NBP gold, NBP mid
   rates) and adds `source='binance'` rows to `raw.market_prices`. **No API keys at all**, so nothing
   new touches GR-5, and the whole slice is testable offline with recorded fixtures (GR-7).
2. **Plan B — "keyed sources":** FRED (macro/rates into `macro_series`) + Alpaca (ETF/equity
   cross-check into `market_prices`). Adds exactly two secrets, both documented in
   `docs/configuration.md` and `.env.example` (GR-4).

Deliberately **not** recommended for now: Stooq (bot-gated), CoinGecko as a bar source (derived
granularity conflicts with GR-1), Nasdaq Data Link (dead client), IBKR (needs a GUI session),
exchangerate.host (now keyed), and everything with a sub-1000/day free cap.

**LBMA is the one judgement call.** It is the best free metals data in existence and it is one
`httpx` call, but the IBA licence language is explicit. Recommend treating it as a separate, later
decision with the licence risk written into the plan — not folded silently into Plan A.

## 11. Risks & open questions

1. **Finnhub free-tier candle access is unresolved.** Must be tested with a real free key before any
   plan depends on it.
2. **FRED's 120 req/min is unconfirmed** from a primary source. Implement conservative tenacity
   backoff on 429 regardless (GR-3 makes this cheap).
3. **`market_prices.volume` is `BIGINT`** but crypto volumes are fractional (Binance returned
   `"2.62202000"` BTC). Ingesting Binance forces a decision: truncate, store quote volume, or widen
   the column. Widening is a GR-6 schema change and needs its own plan item.
4. **Daily-bar `ts` convention across sources.** `market_prices.ts` is documented as "bar start, UTC",
   and Yahoo's daily bars already land at odd UTC offsets. Binance daily bars open at exactly
   00:00 UTC; NBP/ECB/LBMA are *dates*, not instants. Two sources for the "same day" will not share a
   `ts`. That is correct and unavoidable for an EL-only ingestor, but any cross-check query must join
   on a downstream-derived trading date, **not** on `ts`. Worth stating in `docs/data-model.md`.
5. **Twelve Data returns local exchange time by default** — must pass `timezone=UTC` explicitly.
6. **Coinbase's `[time, low, high, open, close, volume]` ordering** is a silent-corruption trap.
7. **Licence obligations to record if adopted:** Tiingo (internal/personal use only), CoinGecko
   (attribution), FMP (display agreement), LBMA/IBA (licence required), Twelve Data (non-commercial).
8. **Stooq could come back.** If the proof-of-work gate is removed, re-evaluate immediately — it was
   the single best licence-and-cost fit for FinStream's exact symbol set.
9. **`pandasdmx` must not be used** (`requires_python <3.12`). Use `sdmx1` or plain HTTP.

## 12. Sources

All checked **2026-09-18**.

1. <https://stooq.com/q/d/l/?s=xauusd&i=d> — live probe (JS proof-of-work challenge)
2. <https://www.alphavantage.co/premium/> — free tier 25 req/day
3. <https://twelvedata.com/pricing>, <https://twelvedata.com/docs> — 800 credits/day, 8/min
4. <https://finnhub.io/docs/api> — `window.docSchema`, 116 paths; <https://finnhub.io/pricing>
5. <https://www.tiingo.com/pricing> — 50/h, 1000/day, 500 symbols/mo, Internal Use Only
6. <https://massive.com/pricing>, <https://massive.com/blog/polygon-is-now-massive> — rebrand 2025-10-30
7. <https://eodhd.com/pricing> — 20 calls/day, past year
8. <https://site.financialmodelingprep.com/developer/docs/pricing> — 250 calls/day, 500 MB/30 d
9. <https://marketstack.com/product> — 100 requests/month
10. <https://databento.com/pricing> — $125 credits, $199–$4500/mo
11. <https://data.nasdaq.com/api/v3/datasets/LBMA/GOLD.json> — Imperva block
12. <https://api.binance.com/api/v3/exchangeInfo>, `/api/v3/klines`, `wss://stream.binance.com:9443`
13. <https://api.exchange.coinbase.com/products/BTC-USD/candles>
14. <https://api.kraken.com/0/public/OHLC>, `wss://ws.kraken.com/v2`
15. <https://www.coingecko.com/en/api/pricing>, <https://docs.coingecko.com/docs/common-errors-rate-limit>
16. <https://data-api.ecb.europa.eu/service/data/EXR/...>, `/service/data/FM/...`
17. <https://frankfurter.dev/> and <https://api.frankfurter.dev/v1/...>
18. <https://api.exchangerate.host/latest> — now returns `missing_access_key`
19. <https://api.nbp.pl/> and live `/api/cenyzlota/`, `/api/exchangerates/rates/{a,c}/...`
20. <https://fred.stlouisfed.org/docs/api/api_key.html>, `/docs/api/fred/series_observations.html`
21. <https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx>
22. <https://api.worldbank.org/v2/country/PL/indicator/NY.GDP.MKTP.CD>
23. <https://prices.lbma.org.uk/json/{gold_am,gold_pm,silver,platinum_am,platinum_pm,palladium_am,palladium_pm}.json>
24. <https://www.lbma.org.uk/prices-and-data/precious-metal-prices> — IBA licence requirement
25. <https://metals-api.com/pricing> — no free plan
26. <https://goldprice.dev/comparison/goldapi-io-vs-gold-api> — GoldAPI free tier (secondary, UNVERIFIED)
27. <https://alpaca.markets/data> — 200 calls/min, IEX, WS ≤ 30 symbols
28. <https://www.deribit.com/api/v2/public/get_tradingview_chart_data>, <https://api.bybit.com/v5/market/kline>
29. PyPI JSON API `https://pypi.org/pypi/<pkg>/json` for every version and release date above
