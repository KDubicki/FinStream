# ADR-0001: The FinStream Ingestor is Extract & Load only

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** FinStream owner
- **Related:** [architecture](../architecture.md), [data model](../data-model.md), [plan 0001](../plans/0001-ingestor-implementation.md), AGENTS.md GR-1

## Context

FinStream is planned as several microservices. The first one collects financial market data (gold, ETFs, stock indices) from free, unofficial and unreliable APIs, starting with Yahoo Finance via `yfinance`. Some of that history is hard or impossible to fetch again later. Yahoo, for instance, limits how far back intraday bars go.

Putting transformations into the ingestion service would:
- couple two concerns with different change rates. Fetching is about reliability, processing is about business logic.
- lose raw fidelity. A bug in a transformation would corrupt the only copy of the data.
- mean that reprocessing requires re-fetching, which may no longer be possible.

## Decision

We will keep the FinStream Ingestor strictly **Extract & Load**:

- **Allowed:** renaming columns; casting types; converting timestamps to UTC; `NaN` → `NULL`; dropping rows where every value is `NaN`; adding bookkeeping columns (`source`, `ingested_at`, `updated_at`); storing both unadjusted `close` and source-provided `adj_close`.
- **Forbidden:** resampling, aggregations, gap filling/interpolation, returns, indicators, currency or unit conversion, cross-source deduplication, outlier correction.
- **Where the data lands:** the `raw` schema, which is owned and written only by the Ingestor. Downstream services read from `raw` and write to their own schemas.

## Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| ETL inside the Ingestor | One service to deploy | Couples concerns; raw data lost on transformation bugs; no replay | Rejected |
| EL + downstream processing services (chosen) | Replayable raw data; small, stable Ingestor; clear ownership | Downstream must handle timezones/trading days and choose adjusted vs unadjusted | **Accepted** |
| Store raw API payloads (JSON blobs) | Maximum fidelity | yfinance returns DataFrames, not raw payloads; poor queryability; typed columns lose nothing material here | Rejected for Yahoo; may be revisited per source |

## Consequences

### Positive
- Raw history is preserved as delivered and can be reprocessed indefinitely.
- The Ingestor stays small, which makes its reliability requirements (idempotency, retries) easy to test.
- Downstream services can evolve independently.

### Negative / risks
- Downstream services must implement trading-calendar and timezone logic (e.g. deriving trading dates from daily bars stored in UTC).
- Values revised by the source (latest bar, adjusted closes) change in place in `raw`. The history of revisions isn't kept. If audit history is ever needed, that's a separate decision.

## Compliance

- AGENTS.md **GR-1**.
- The EL-boundary test is mandatory for normalisation code (test skill).
- The golden-rules review happens during the Review & Document phase (development skill).
