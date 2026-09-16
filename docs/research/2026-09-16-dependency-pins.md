# Research: dependency pins (plan 0001, R4)

- **Date:** 2026-09-16
- **Author:** Claude Opus 5 (Claude Code), for review by the FinStream owner
- **Related plan / item:** [plan 0001](../plans/0001-ingestor-implementation.md), R4
- **Status:** Final

## Question

Which exact, mutually compatible versions should `requirements.txt` and `requirements-dev.txt` pin for Python 3.12 (GR-8), and do the pre-commit revs need refreshing?

## Findings

All versions come from the PyPI JSON API, checked 2026-09-16.

### Runtime (`requirements.txt`)
| Package | Pin | Released | `requires_python` | Note |
|---|---|---|---|---|
| yfinance | `==1.7.0` | 2026-08-26 | not declared | Requires `pandas>=1.3.0`, `curl_cffi>=0.15` |
| pandas | `==3.0.5` | 2026-07-22 | `>=3.11` | **Flagged:** yfinance allows 3.x, but it isn't proven at runtime |
| SQLAlchemy | `==2.0.54` | 2026-09-15 | `>=3.7` | Stay on 2.0.x; 2.1 is still in release candidates |
| psycopg[binary] | `==3.3.5` | 2026-08-31 | `>=3.10` | Binary wheels, so no build toolchain in the image |
| APScheduler | `==3.11.3` | 2026-06-28 | `>=3.8` | 4.0 is still alpha (ADR-0003) |
| tenacity | `==9.1.4` | 2026-02-07 | `>=3.10` | |
| pydantic-settings | `==2.15.0` | 2026-08-07 | `>=3.10` | Pulls `pydantic>=2.7.0`, `python-dotenv` |
| python-json-logger | `==4.2.0` | 2026-08-15 | `>=3.10` | No dependencies |

### Dev / test (`requirements-dev.txt`, starts with `-r requirements.txt`)
| Package | Pin | Released | Note |
|---|---|---|---|
| pytest | `==9.1.1` | 2026-06-19 | |
| pytest-cov | `==7.1.0` | 2026-03-21 | |
| testcontainers[postgres] | `==4.15.0` | 2026-07-24 | Needs a running Docker daemon |
| ruff | `==0.16.8` | 2026-09-16 | |
| mypy | `==2.3.1` | 2026-08-15 | |
| pandas-stubs | `==3.0.5.260914` | 2026-09-14 | Matches pandas 3.0.5 |

### Python version
- `python3.12` (3.12.13) is available locally, and `python3.11` (3.11.15) as well. The system `python3` is 3.14.
- pandas 3.x needs ≥ 3.11 and several packages need ≥ 3.10, so the target of **3.12** works and stays 3.11-compatible.

### Database image
- `timescale/timescaledb` publishes `2.30.0` for pg16, pg17 and pg18 (updated 2026-09-10), each also as an `-oss` variant.
- **Recommendation: `timescale/timescaledb:2.30.0-pg16`.** [ADR-0002](../adr/0002-postgresql-timescaledb-raw-storage.md) is Accepted and specifies PostgreSQL 16, so the pg16 build is the compliant choice; pg17/pg18 images exist and moving to one would need a new ADR (added to the plan's follow-ups). The non-OSS (community, Timescale License) build keeps compression and continuous aggregates available for later processing services, which ADR-0002 already anticipated. `-oss` is Apache-licensed but lacks those features.
- Source: [Docker Hub tags API](https://hub.docker.com/v2/repositories/timescale/timescaledb/tags), checked 2026-09-16.

### pre-commit
- `.pre-commit-config.yaml` pins ruff-pre-commit at `v0.16.7`, but `0.16.8` was released on 2026-09-16. **Action (M1):** bump the rev so it matches the `ruff` pin in `requirements-dev.txt`.
- The other revs remain current: `pre-commit-hooks v6.0.0`, `mirrors-mypy v2.3.1`, `gitleaks v8.30.1`, `conventional-pre-commit v4.4.0`.
- The mypy hook needs `additional_dependencies` so it can see third-party types: `pydantic`, `pydantic-settings`, `sqlalchemy`, `pandas-stubs`, `types-APScheduler` if it exists. **Open:** the exact pydantic version gets pinned in M1 from the installed environment.

## Recommendation

1. Pin exactly the versions above with `==`.
2. **Verify pandas 3.x against yfinance first.** M1 installs the environment and runs a smoke test (import yfinance, normalise a fixture). If it fails, fall back to `pandas==2.*` plus a matching `pandas-stubs`, and record that in this note.
3. Pin the database image at `timescale/timescaledb:2.30.0-pg16` in both `docker-compose.yml` and the integration-test fixture (ADR-0002 specifies PostgreSQL 16).
4. In M1, bump ruff-pre-commit to `v0.16.8` and fill in the mypy `additional_dependencies`.

## Risks & open questions

- **pandas 3.x with yfinance 1.7** is the main unknown, and step 2 resolves it before any code depends on it.
- Pinning exactly means updates are deliberate. A dependency-update policy (e.g. `pip-audit`, Renovate) is already on the plan's follow-up list.

## Sources

1. <https://pypi.org/pypi/{package}/json> for every package listed (checked 2026-09-16)
2. <https://hub.docker.com/v2/repositories/timescale/timescaledb/tags> (checked 2026-09-16)
