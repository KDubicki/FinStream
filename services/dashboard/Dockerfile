# syntax=docker/dockerfile:1
# Multi-stage: the build environment never reaches the runtime image.
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
RUN groupadd --system --gid 10002 app \
 && useradd --system --uid 10002 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
USER app
EXPOSE 8501
# Streamlit's own health endpoint, so an unhealthy container is visible in `docker compose ps`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3).status == 200 else 1)"
CMD ["streamlit", "run", "src/finstream_dashboard/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
