# mcptest cloud backend — small production image.
#
# Build:
#   docker build -t mcptest-cloud .
#
# Run (with a local Postgres):
#   docker run --rm -p 8000:8000 \
#     -e MCPTEST_DATABASE_URL=postgresql+psycopg://user:pass@host/db \
#     mcptest-cloud

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system deps for psycopg2 / SSL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the source and install with the cloud extra.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[cloud]"

EXPOSE 8000

CMD ["uvicorn", "mcptest.cloud.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
