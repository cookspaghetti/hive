# Disposable forensic sandbox image (fyp.txt L4).
# One short-lived, network-locked, non-root container is launched PER URL by
# hive.sandbox.runner.PlaywrightDockerRunner, then destroyed (--rm).
#
# Build:  docker build -t hive-sandbox:latest docker/sandbox
#
# The container only ever runs the Playwright script passed via `node -e`, writes
# a screenshot to the mounted /out, and prints JSON findings to stdout.

FROM mcr.microsoft.com/playwright:v1.44.0-jammy

# Playwright browsers are preinstalled in this base image. Ensure the chromium
# build is present and the runtime user cannot write outside /out.
WORKDIR /sandbox
RUN npm init -y >/dev/null 2>&1 && npm install playwright@1.44.0 >/dev/null 2>&1

# Drop privileges: the base image ships a non-root 'pwuser'.
USER pwuser

# No ENTRYPOINT: the runner invokes `node -e "<script>" <url>` explicitly and
# relies on `docker run --rm --network ... --read-only` for isolation.
