#!/usr/bin/env bash
# HIVE Docker-in-Docker entrypoint.
# Starts the inner Docker daemon, builds the sandbox image inside it (so the
# Layer 4 runner can spawn disposable containers), then launches the agent.
set -uo pipefail

# Pin the CLI to the inner daemon's socket, ignoring any inherited DOCKER_HOST
# or docker context that would point the client elsewhere.
export DOCKER_HOST=unix:///var/run/docker.sock
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH 2>/dev/null || true

# Force the vfs storage driver: overlay2/fuse-overlayfs are unavailable when
# running DinD on top of Docker Desktop's overlay filesystem. vfs is slower but
# works everywhere.
container_started=$(date +%s)
echo "[startup][container] INITIALIZING HIVE container bootstrap"
echo "[startup][docker] INITIALIZING inner dockerd storage_driver=vfs"
dockerd \
    --host=unix:///var/run/docker.sock \
    --storage-driver=vfs \
    > /var/log/dockerd.log 2>&1 &
DOCKERD_PID=$!

# Wait (up to ~60s) for the daemon to answer on the socket.
ready=0
for i in $(seq 1 60); do
    if [ -S /var/run/docker.sock ] && docker version > /dev/null 2>&1; then
        ready=1
        echo "[startup][docker] READY inner dockerd duration=${i}s"
        break
    fi
    if ! kill -0 "$DOCKERD_PID" 2>/dev/null; then
        echo "[startup][docker] ERROR dockerd process exited during startup" >&2
        break
    fi
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    echo "[startup][docker] ERROR inner dockerd did not become ready" >&2
    echo "----- docker version (client error) -----" >&2
    docker version >&2 2>&1 || true
    echo "----- socket -----" >&2
    ls -l /var/run/docker.sock >&2 2>&1 || echo "(no socket)" >&2
    echo "DOCKER_HOST=${DOCKER_HOST:-<unset>}" >&2
    echo "----- dockerd.log (tail) -----" >&2
    tail -n 20 /var/log/dockerd.log >&2 || true
    exit 1
fi

# Build the disposable sandbox image inside the inner daemon if absent.
if ! docker image inspect hive-sandbox:latest > /dev/null 2>&1; then
    sandbox_started=$(date +%s)
    echo "[startup][sandbox] INITIALIZING image=hive-sandbox:latest source=build"
    docker build -t hive-sandbox:latest /app/docker/sandbox
    echo "[startup][sandbox] READY image=hive-sandbox:latest duration=$(($(date +%s) - sandbox_started))s"
else
    echo "[startup][sandbox] READY image=hive-sandbox:latest source=cache"
fi

# Verify mode: prove the DinD plumbing works without needing real credentials.
if [ "${HIVE_VERIFY_ONLY:-}" = "1" ]; then
    echo "[startup][container] READY verification completed duration=$(($(date +%s) - container_started))s"
    exit 0
fi

echo "[startup][container] READY bootstrap completed duration=$(($(date +%s) - container_started))s"
echo "[startup][application] STARTING command='python -m hive'"
exec python -m hive
