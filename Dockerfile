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
# Caching: locked dependencies are installed before application source is
# copied. Source-only edits therefore create a small final layer instead of
# repacking the Torch environment.
#
# NOTE: GLiNER pulls in Torch. pyproject.toml pins Torch to PyTorch's official
# CPU wheel index because this deployment does not expose a GPU.

FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7

ENV PYTHONUNBUFFERED=1 \
    DOCKER_TLS_CERTDIR="" \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

# libs for pyzbar + opencv (slim, no recommends).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libzbar0 \
        libgl1 \
        libglib2.0-0 \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-chi-sim \
        fonts-wqy-zenhei \
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
# Heavy dependency layer: invalidated only by dependency metadata or uv.lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --link-mode=copy

# Application code remains a thin layer and runs directly from /app/src.
COPY src ./src
COPY evaluation/fixtures ./evaluation/fixtures

# Runtime-only files, copied AFTER the heavy install so edits here are cheap.
COPY docker ./docker
COPY docker/app-entrypoint.sh /usr/local/bin/hive-entrypoint.sh
RUN chmod +x /usr/local/bin/hive-entrypoint.sh

# Web control panel (localhost inside the container; publish with -p at run).
EXPOSE 9130

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9130/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/hive-entrypoint.sh"]
