"""Fixtures shared across the conformance suite.

The autouse ``_reset_throttle_cache`` fixture clears DRF's process-wide throttle counters before
every test. The remaining fixtures are the plain authenticated/unauthenticated clients plus an
optional superAdmin user.

A test module that defines its own fixture of the same name shadows this one (pytest precedence:
module beats conftest), so suites that seed a different organisation are unaffected.
"""

from __future__ import annotations

import asyncio
import contextlib

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


@pytest.fixture
def super_admin(db: None) -> User:
    """SuperAdmin user for cross-org tests.

    Uses its own organisation so a test can create data in a *different* org and verify the
    break-glass read path. Set ``role="superAdmin"`` explicitly; ``is_superuser`` alone is Django
    admin state and is not what ``identity.scoping`` consults.
    """
    org = Organisation.objects.create(name="Test SuperAdmin Org")
    return User.objects.create(
        login="superadmin",
        username="superadmin",
        email="superadmin@example.net",
        org=org,
        role="superAdmin",
        is_active=True,
    )


@contextlib.asynccontextmanager
async def _drive_lifespan(app):
    """Drive ASGI lifespan for lifespan-capable servers (Daphne does not send it)."""
    incoming: asyncio.Queue = asyncio.Queue()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def receive():
        return await incoming.get()

    async def send(message):
        if message["type"] == "lifespan.startup.complete":
            started.set()
        elif message["type"] == "lifespan.shutdown.complete":
            stopped.set()

    scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
    task = asyncio.create_task(app(scope, receive, send))
    await incoming.put({"type": "lifespan.startup"})
    await asyncio.wait_for(started.wait(), 5)
    try:
        yield
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(stopped.wait(), 5)
        await task
