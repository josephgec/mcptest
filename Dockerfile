# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# mcptest cloud backend — production container image
# ---------------------------------------------------------------------------
# Multi-stage build:
#   builder  — installs Python deps into a venv
#   runtime  — copies only the venv + src, runs as non-root user
#
# Build:
#   docker build -t mcptest-cloud .
#
# Run (SQLite, local dev):
#   docker run --rm -p 8000:8000 mcptest-cloud
#
# Run (Postgres, production):
#   docker run --rm -p 8000:8000 \
#     -e MCPTEST_DATABASE_URL=postgresql+psycopg2://user:pass@host/db \
#     -e MCPTEST_API_KEYS=secret-key \
#     -e MCPTEST_AUTH_REQUIRED=true \
#     mcptest-cloud
# ---------------------------------------------------------------------------

# ---- builder stage --------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# System deps needed to compile psycopg2 (Postgres driver)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

# Copy manifests first — Docker layer cache: rebuilds from here only when
# pyproject.toml changes, not on every src edit.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir ".[cloud]"

# ---- runtime stage --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/src"

# Runtime system libs for psycopg2 (shared library, not headers)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --shell /bin/bash --uid 1001 mcptest

WORKDIR /app

# Copy venv and source from builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/src ./src

# Default configuration — override at deploy time via env vars
ENV MCPTEST_DATABASE_URL="sqlite:///./mcptest_cloud.db" \
    MCPTEST_CLOUD_TITLE="mcptest cloud" \
    MCPTEST_CLOUD_VERSION="0.1.0" \
    MCPTEST_CORS_ORIGINS="*" \
    MCPTEST_RATE_LIMIT="60"
# MCPTEST_API_KEYS and MCPTEST_AUTH_REQUIRED intentionally absent —
# configure these at deploy time.

USER mcptest

EXPOSE 8000

# Liveness health check (no DB dependency — fast)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
        || exit 1

# exec-form CMD for correct SIGTERM → graceful uvicorn shutdown
CMD ["uvicorn", "mcptest.cloud.app:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
