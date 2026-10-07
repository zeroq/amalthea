"""Fixtures shared across the conformance suite.

Only two things live here, and both are things several modules would otherwise copy verbatim:

* ``_reset_throttle_cache`` (autouse) — DRF's ``AnonRateThrottle`` keys into the process-wide
  LocMemCache, which survives between tests, so a long run trips 429s that have nothing to do
  with the assertion at hand. ``test_query_api`` and ``test_webhook_hardening`` each grew the
  same guard locally; making it suite-wide is the same fix applied once. It is safe for the
  tests that already clear the cache themselves (they clear it again inside the test body, and
  fixture setup runs first).
* ``analyst`` / ``api`` / ``anonymous_api`` — the plain authenticated and unauthenticated
  clients. A test module that defines its own of any of these shadows this one (pytest
  precedence: module beats conftest), so the suites that seed a different organisation are
  unaffected.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from identity.models import Organisation, User


@pytest.fixture(autouse=True)
def _reset_throttle_cache() -> None:
    """Drop DRF's per-IP/per-user request counters before every test (see the docstring)."""
    cache.clear()


@pytest.fixture
def analyst(db: None) -> User:
    org = Organisation.objects.create(name="Test Org")
    return User.objects.create(
        login="analyst",
        username="analyst",
        email="analyst@example.net",
        org=org,
        is_active=True,
    )


@pytest.fixture
def api(analyst: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=analyst)
    return client


@pytest.fixture
def anonymous_api() -> APIClient:
    return APIClient()
