"""Static contracts for the four-service Docker Compose deployment."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_declares_the_four_service_boundaries():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    for service in ("frontend", "backend", "postgres", "qdrant"):
        assert f"  {service}:" in compose
    assert '"127.0.0.1:9130:80"' in compose
    assert '"127.0.0.1:6333:6333"' not in compose
    assert "HIVE_DATABASE_URL:" in compose
    assert "HIVE_QDRANT_URL: http://qdrant:6333" in compose
    assert "HIVE_SANDBOX_PIDS_LIMIT:" in compose
    assert "hive_fastembed:/root/.cache/fastembed" in compose


def test_stateful_service_images_are_immutable_and_health_gated():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "postgres:17-alpine@sha256:" in compose
    assert "qdrant/qdrant:v1.18.0@sha256:" in compose
    assert "qdrant/qdrant:latest" not in compose
    assert "qdrant:\n        condition: service_healthy" in compose
    assert "GET /readyz HTTP/1.0" in compose


def test_frontend_image_serves_assets_and_proxies_backend_apis():
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    nginx = (ROOT / "frontend" / "nginx.conf.template").read_text(encoding="utf-8")

    assert "FROM node:22-alpine@sha256:" in dockerfile
    assert "FROM nginx:" in dockerfile
    assert "npm ci --prefix frontend" in dockerfile
    assert "npm run build --prefix frontend" in dockerfile
    assert "COPY --from=build" in dockerfile
    assert "panel.js" in dockerfile and "panel.css" in dockerfile
    assert "sha256sum" in dockerfile
    assert "docker-20260823" not in dockerfile
    assert "location /api/" in nginx
    assert "proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT}" in nginx


def test_application_and_sandbox_base_images_are_immutable():
    backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    sandbox = (ROOT / "docker" / "sandbox" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim@sha256:" in backend
    assert "FROM nginx:1.27-alpine@sha256:" in frontend
    assert "FROM python:3.12-slim-trixie@sha256:" in sandbox
    assert 'io.hive.sandbox.contract="hive-scrapling-v1"' in sandbox


def test_dind_rebuilds_stale_sandbox_and_probes_runtime_capabilities():
    entrypoint = (ROOT / "docker" / "app-entrypoint.sh").read_text(encoding="utf-8")

    assert 'installed_contract' in entrypoint
    assert 'io.hive.sandbox.contract' in entrypoint
    assert 'from scrapling.fetchers import StealthyFetcher' in entrypoint
    assert 'docker build -t "$sandbox_image" /app/docker/sandbox' in entrypoint


def test_dind_recovers_stale_runtime_files_and_cleans_up_children():
    entrypoint = (ROOT / "docker" / "app-entrypoint.sh").read_text(encoding="utf-8")

    assert "/var/run/docker.pid" in entrypoint
    assert "/var/run/docker/containerd/containerd.pid" in entrypoint
    assert "reset_docker_runtime_files" in entrypoint
    assert "trap cleanup_children EXIT" in entrypoint
    assert "trap handle_shutdown TERM INT" in entrypoint
    assert "python -m hive &" in entrypoint
    assert 'wait "$APP_PID"' in entrypoint


def test_backend_healthcheck_allows_for_cold_model_startup():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "--start-period=90s" in dockerfile


def test_task_run_migrates_away_from_the_legacy_single_container():
    taskfile = (ROOT / "Taskfile.yml").read_text(encoding="utf-8")

    assert "docker compose up --build --remove-orphans" in taskfile
