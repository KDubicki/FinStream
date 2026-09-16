# syntax=docker/dockerfile:1
# Multi-stage: build tooling and wheels never reach the runtime image.
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
# Exactly pinned (GR-8); psycopg[binary] and pandas ship wheels, so no compiler is needed.
RUN pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src
# Non-root: the service only needs to read its code and write a heartbeat file.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
USER app
# The scheduler touches a heartbeat file; a stale one means the process is wedged even though the
# container still looks up. start-period covers waiting for the database and the first job.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD ["python", "-m", "finstream_ingestor.healthcheck"]
# No secrets in the image: configuration arrives through the environment at runtime (GR-4, GR-5).
CMD ["python", "-m", "finstream_ingestor.main"]
