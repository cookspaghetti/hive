#!/usr/bin/env bash
# HIVE Docker-in-Docker entrypoint.
# Starts the inner Docker daemon, builds the sandbox image inside it (so the
# Layer 4 runner can spawn disposable containers), then launches the agent.
set -uo pipefail

# Pin the CLI to the inner daemon's socket, ignoring any inherited DOCKER_HOST
# or docker context that would point the client elsewhere.
export DOCKER_HOST=unix:///var/run/docker.sock
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH 2>/dev/null || true

DOCKERD_PID=""
APP_PID=""
docker_runtime_files=(
    /var/run/docker.pid
    /var/run/docker.sock
    /var/run/docker/containerd/containerd.pid
    /var/run/docker/containerd/containerd.sock
    /var/run/docker/containerd/containerd.sock.ttrpc
    /var/run/docker/containerd/containerd-debug.sock
)

reset_docker_runtime_files() {
    local found=0
    local path
    for path in "${docker_runtime_files[@]}"; do
        if [ -e "$path" ] || [ -S "$path" ]; then
            found=1
            break
        fi
    done
    if [ "$found" -eq 1 ] && [ "${1:-}" = "announce" ]; then
        echo "[startup][docker] RECOVERING stale runtime files from previous container stop"
    fi
    rm -f -- "${docker_runtime_files[@]}"
}

cleanup_children() {
    local status=$?
    local i
    trap - EXIT
    if [ -n "$APP_PID" ] && kill -0 "$APP_PID" 2>/dev/null; then
        kill -TERM "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    if [ -n "$DOCKERD_PID" ] && kill -0 "$DOCKERD_PID" 2>/dev/null; then
        kill -TERM "$DOCKERD_PID" 2>/dev/null || true
        for i in $(seq 1 10); do
            kill -0 "$DOCKERD_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$DOCKERD_PID" 2>/dev/null || true
        wait "$DOCKERD_PID" 2>/dev/null || true
    fi
    reset_docker_runtime_files
    exit "$status"
}

handle_shutdown() {
    [ -z "$APP_PID" ] || kill -TERM "$APP_PID" 2>/dev/null || true
    exit 143
}

trap cleanup_children EXIT
trap handle_shutdown TERM INT
reset_docker_runtime_files announce

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

# Build the disposable sandbox image inside the inner daemon if absent or when
# a persisted DinD volume contains an older, incompatible image with the same
# tag. The contract label changes whenever the runner/image interface changes.
sandbox_image="${HIVE_SANDBOX_IMAGE:-hive-sandbox:latest}"
sandbox_contract="hive-scrapling-v1"
installed_contract=$(docker image inspect \
    --format '{{ index .Config.Labels "io.hive.sandbox.contract" }}' \
    "$sandbox_image" 2>/dev/null || true)
if [ "$installed_contract" != "$sandbox_contract" ]; then
    sandbox_started=$(date +%s)
    echo "[startup][sandbox] INITIALIZING image=$sandbox_image source=build installed_contract=${installed_contract:-missing} expected_contract=$sandbox_contract"
    docker build -t "$sandbox_image" /app/docker/sandbox
    echo "[startup][sandbox] READY image=$sandbox_image duration=$(($(date +%s) - sandbox_started))s"
else
    echo "[startup][sandbox] READY image=$sandbox_image source=cache contract=$installed_contract"
fi

# A correct-looking label is not enough: prove the cached/built image can run
# the exact Python and Scrapling imports required by the forensic runner. This
# probe is offline and uses the same read-only/non-privileged boundary.
if ! sandbox_probe=$(docker run --rm --network none --read-only \
    --tmpfs /tmp:rw,size=16m -e HOME=/tmp --cap-drop ALL \
    --security-opt no-new-privileges "$sandbox_image" python -c \
    'import json, platform; from importlib.metadata import version; from scrapling.fetchers import StealthyFetcher; print(json.dumps({"python": platform.python_version(), "scrapling": version("scrapling")}))' \
    2>&1); then
    echo "[startup][sandbox] ERROR image capability probe failed: $sandbox_probe" >&2
    echo "[startup][sandbox] ERROR rebuild with: docker build -t $sandbox_image /app/docker/sandbox" >&2
    exit 1
fi
echo "[startup][sandbox] VERIFIED image=$sandbox_image contract=$sandbox_contract capabilities=$sandbox_probe"

# Verify mode: prove the DinD plumbing works without needing real credentials.
if [ "${HIVE_VERIFY_ONLY:-}" = "1" ]; then
    echo "[startup][container] READY verification completed duration=$(($(date +%s) - container_started))s"
    exit 0
fi

echo "[startup][container] READY bootstrap completed duration=$(($(date +%s) - container_started))s"
echo "[startup][application] STARTING command='python -m hive'"
python -m hive &
APP_PID=$!
wait "$APP_PID"
app_status=$?
APP_PID=""
exit "$app_status"
