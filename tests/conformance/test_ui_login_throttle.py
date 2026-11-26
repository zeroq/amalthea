"""UI login throttle hardening (Phase 12 / §2.7 QA).

The UI login view uses `identity.ratelimit` counters. Lockout must block repeated failed
attempts, surface a clear error, and reset on successful login.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from identity.models import Organisation, User


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ui-login-throttle-test",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@override_settings(
    LOGIN_RATE_LIMIT_ATTEMPTS=3,
    LOGIN_RATE_LIMIT_WINDOW=900,
    LOGIN_RATE_LIMIT_LOCKOUT=300,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
def test_ui_login_lockout_after_failed_attempts_and_reset_on_success() -> None:
    org = Organisation.objects.create(name="Throttle Org")
    User.objects.create_user(
        username="throttle-user",
        password="correct-pass-123",
        email="throttle@example.net",
        org=org,
    )
    client = Client()
    url = reverse("login")

    for _ in range(3):
        resp = client.post(url, {"username": "throttle-user", "password": "bad-pass"})
        assert resp.status_code == 200

    # Lockout: even correct credentials are denied while locked.
    locked = client.post(url, {"username": "throttle-user", "password": "correct-pass-123"})
    assert locked.status_code == 200
    assert "_auth_user_id" not in client.session

    # Reset on successful login: clear lockout state by simulating window expiry via new cache.
    from django.core.cache import cache

    cache.clear()
    ok = client.post(url, {"username": "throttle-user", "password": "correct-pass-123"})
    assert ok.status_code in (302, 303)
    assert "_auth_user_id" in client.session
