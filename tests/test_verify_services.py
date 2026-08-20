from __future__ import annotations

import os

import pytest

from hive.verify_services import verify_services


def test_service_verifier_requires_both_endpoints() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        verify_services("", "http://qdrant")
    with pytest.raises(ValueError, match="Qdrant"):
        verify_services("postgresql://postgres", "")


@pytest.mark.live_services
def test_postgres_and_qdrant_round_trip_when_requested() -> None:
    database_url = os.getenv("HIVE_TEST_DATABASE_URL", "")
    qdrant_url = os.getenv("HIVE_TEST_QDRANT_URL", "")
    if not database_url or not qdrant_url:
        pytest.skip("set HIVE_TEST_DATABASE_URL and HIVE_TEST_QDRANT_URL to run")

    result = verify_services(database_url, qdrant_url)

    assert result["postgres"]["ok"] is True
    assert result["qdrant"]["ok"] is True
