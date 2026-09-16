# ADR-0005: The dashboard reads `raw` directly, for now

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** FinStream owner
- **Related:** [ADR-0001](0001-ingestor-is-extract-load-only.md), [plan 0003](../plans/0003-dashboard-service.md), [architecture](../architecture.md)

## Context

[ADR-0001](0001-ingestor-is-extract-load-only.md) splits the platform into ingest, process and serve, where each layer reads only from the one before it. The processing layer does not exist yet: there is no `staging` or `core` schema, and no service that would populate one.

The maintainer asked for a Streamlit dashboard to see the collected data. Waiting for a processing layer before anything can be looked at would leave the platform unobservable for as long as that takes.

## Decision

The dashboard service reads the `raw` schema **directly**, and only reads it.

- All of its SQL lives in `queries.py`, and a test asserts every statement is a `SELECT` against `raw.*`.
- It performs no transformation beyond what a chart needs to render: filtering by symbol, interval and date range, ordering, and limiting.
- Derived series, indicators, resampling and cross-source logic stay out of it. They belong to the processing layer when it arrives.
- When a processing layer exists, the dashboard switches to it and this ADR is superseded.

## Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Dashboard reads `raw` directly (chosen) | Data is visible now; no extra moving parts; read-only keeps the contract one-way | Couples a serving component to the raw contract, so a `raw` change can break the dashboard | **Accepted** |
| Build a processing layer first | Architecturally pure | Nothing is visible until a whole service exists; the shape of that service is still unknown | Rejected for now |
| Let the dashboard compute derived series itself | Quick to demo | Business logic in a UI, duplicated later by the processing service, exactly what ADR-0001 avoids | Rejected |

## Consequences

### Positive
- The data is observable as soon as the Ingestor writes it, including ingestion failures.
- The read-only rule keeps the raw schema owned solely by the Ingestor (GR-6).

### Negative / risks
- A change to `raw` can break the dashboard, so `docs/data-model.md` changes must consider both consumers.
- There is a standing temptation to add "just one" computed column to the dashboard's SQL. The SELECT-only test and review are what hold that line.

## Compliance

- A test asserts every dashboard statement is read-only and touches only `raw.*`.
- The golden-rules review checks GR-1 against the dashboard's queries.
- Superseding this ADR is part of introducing the processing layer.
