# ==============================================================================
# Production Dockerfile for Real Estate Hunter & Scraper Pipeline
# Multi-stage build: uv (locked deps) in builder, slim runtime with venv copy.
# ==============================================================================

# Stage 1: Builder
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Stage 2: Runtime
FROM python:3.12-slim

# Install system dependencies (tzdata & ca-certificates for Polish timezone and TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    && ln -snf /usr/share/zoneinfo/Europe/Warsaw /etc/localtime && echo Europe/Warsaw > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user and runtime directories setup prior to file copy (avoids layer duplication)
RUN groupadd -r appuser && useradd -r -m -d /home/appuser -g appuser appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app /home/appuser \
    && chmod -R 755 /app/data /app/logs

# Copy virtualenv with locked dependencies from builder, then application source
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser . /app

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Warsaw \
    HOME=/home/appuser \
    PATH="/app/.venv/bin:$PATH"

# Expose web dashboard port
EXPOSE 8080

# Default command starts the live interactive web dashboard
CMD ["python", "main.py", "dashboard", "--host", "0.0.0.0", "--port", "8080", "--no-open"]
