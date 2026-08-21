# syntax=docker/dockerfile:1.7
#
# PRIVIA backend image.
#
# Multi-stage so the runtime layer contains no build tools. Runs as a
# non-root user with no capabilities.

# --- builder -----------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services ./services
COPY apps/api ./apps/api

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install .

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="PRIVIA" \
      org.opencontainers.image.description="Private Personal AI - local-first assistant" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/privia-app/privia"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PRIVIA_DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1001 privia \
 && useradd --system --uid 1001 --gid privia --create-home --home-dir /home/privia privia \
 && mkdir -p /data /workspace \
 && chown -R privia:privia /data /workspace

COPY --from=builder /opt/venv /opt/venv

USER privia
WORKDIR /home/privia
VOLUME ["/data"]
EXPOSE 8756

# Migrations are idempotent, so applying them on every start is safe and means
# a fresh volume works with no extra step.
ENTRYPOINT ["/bin/sh", "-c", "privia-migrate up && exec privia-api \"$@\"", "--"]
CMD []
