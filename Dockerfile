# syntax=docker/dockerfile:1
# HIVE agent image — Docker-in-Docker (fyp.txt Platform / L4).
#
# The agent spawns disposable sandbox containers at runtime (Layer 4), so this
# image runs its OWN Docker daemon (DinD) rather than using the host's. That
# keeps the sandbox lifecycle isolated from the host daemon, at the cost of
# requiring `--privileged` at run time (documented in README / threat model).
#
# Build:  docker build -t hive:latest .
# Run:    docker run --rm --privileged --env-file .env -p 9130:9130 hive:latest
#
# Caching: `docker/` is copied AFTER the dependency install and a BuildKit uv
# cache mount is used, so editing the entrypoint or source does NOT re-download
# the (large, torch-heavy) dependency set.
#
# NOTE: GLiNER pulls in torch, so the image is large. Set HIVE load_ner off
# (build_engine(..., load_ner=False)) or trim deps for a slim variant.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DOCKER_TLS_CERTDIR=""

# libs for pyzbar + opencv (slim, no recommends).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libzbar0 \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/*

# Docker daemon + CLI for DinD. Install WITH recommends: on Debian 13 the
# `docker` CLI is a recommended (not hard) dependency of docker.io, so
# --no-install-recommends would leave `dockerd` present but `docker` missing.
RUN apt-get update && apt-get install -y docker.io \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version && dockerd --version

RUN --mount=type=cache,target=/root/.cache/pip pip install uv

WORKDIR /app
# Dependency install layer: depends ONLY on the package metadata + source, and
# uses a persistent uv download cache. Editing docker/ or configs below will not
# invalidate this layer, so torch etc. are not re-downloaded on every build.
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --system .

# Runtime-only files, copied AFTER the heavy install so edits here are cheap.
COPY docker ./docker
COPY docker/app-entrypoint.sh /usr/local/bin/hive-entrypoint.sh
RUN chmod +x /usr/local/bin/hive-entrypoint.sh

# Web control panel (localhost inside the container; publish with -p at run).
EXPOSE 9130

ENTRYPOINT ["/usr/local/bin/hive-entrypoint.sh"]
