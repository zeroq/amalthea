import pytest
from django.test import override_settings
from rest_framework.test import APIClient


@pytest.mark.django_db
@override_settings(REDIS_URL="redis://127.0.0.1:1/0")  # port 1: nothing listens here
def test_readyz_fails_when_redis_is_unreachable() -> None:
    """AC1.4 — `/readyz` must 503 when Redis is down.

    Deterministic regardless of whether a developer has docker/compose up: the suite must not
    depend on a live local Redis (the docker-compose gate has been up since 2026-10-06, so the
    old "Redis not running in test env" premise silently rotted). A dead port is a stable stand-in;
    the real "/readyz 200 with Redis up" is verified by the live gate in TODO 3.1.
    """
    client = APIClient()
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "redis"


@pytest.mark.django_db
def test_healthz_needs_no_services() -> None:
    """AC1.4 — `/healthz` returns 200 without touching Redis or the DB."""
    client = APIClient()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
