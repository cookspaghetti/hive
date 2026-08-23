# Disposable forensic sandbox image (fyp.txt L4).
# One short-lived, network-locked, non-root container is launched PER URL by
# hive.sandbox.runner.ScraplingDockerRunner, then destroyed (--rm).
#
# Build:  docker build -t hive-sandbox:latest docker/sandbox
#
# The container only ever runs the Scrapling script passed via `python -c`,
# writes a screenshot to the mounted /out, and prints JSON findings to stdout.

FROM python:3.12-slim-trixie@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ARG SCRAPLING_VERSION=0.4.14
ARG PLAYWRIGHT_VERSION=1.62.0
ARG PATCHRIGHT_VERSION=1.62.1
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# Pin Scrapling so the forensic runner remains reproducible. Scrapling's
# fetchers use Playwright/Patchright; installing Chromium with Playwright also
# installs the browser's required system libraries.
RUN pip install --no-cache-dir \
        "scrapling[fetchers]==${SCRAPLING_VERSION}" \
        "playwright==${PLAYWRIGHT_VERSION}" \
        "patchright==${PATCHRIGHT_VERSION}" \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

RUN groupadd --system sandbox \
    && useradd --system --gid sandbox --create-home --home-dir /home/sandbox sandbox \
    && mkdir -p /sandbox /out \
    && chown -R sandbox:sandbox /sandbox /out /home/sandbox

WORKDIR /sandbox
USER sandbox

# No ENTRYPOINT: the runner invokes `python -c "<script>" <url>` explicitly and
# relies on `docker run --rm --network ... --read-only` for isolation.
