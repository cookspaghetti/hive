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
    assert "hive_fastembed:/root/.cache/fastembed" in compose


def test_frontend_image_serves_assets_and_proxies_backend_apis():
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    nginx = (ROOT / "frontend" / "nginx.conf.template").read_text(encoding="utf-8")

    assert "FROM nginx:" in dockerfile
    assert "panel.js" in dockerfile and "panel.css" in dockerfile
    assert "location /api/" in nginx
    assert "proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT}" in nginx


def test_task_run_migrates_away_from_the_legacy_single_container():
    taskfile = (ROOT / "Taskfile.yml").read_text(encoding="utf-8")

    assert "docker compose up --build --remove-orphans" in taskfile
