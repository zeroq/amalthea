"""Ephemeral presence store for case WebSocket rooms.

Presence is deliberately not a relational model.  The store keeps only opaque,
server-side HMAC members and returns integer counts; no identity is ever broadcast.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from typing import Any

from django.conf import settings
from django.db import connection


def _presence_member(user: Any) -> str:
    """Stable, opaque member identifier computed on the server only."""
    user_id = str(getattr(user, "pk", None) or "anonymous")
    secret = str(getattr(settings, "SECRET_KEY", "amalthea-dev-only"))
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:16]


try:
    import redis

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    redis = None  # type: ignore[assignment]
    _REDIS_AVAILABLE = False


class RedisPresenceStore:
    """Redis sorted-set presence with expiring members."""

    TTL_SECONDS = 60

    def __init__(self, redis_client: Any = None) -> None:
        if redis_client is not None:
            self._client = redis_client
        elif _REDIS_AVAILABLE:
            layers = getattr(settings, "CHANNEL_LAYERS", {}).get("default", {})
            config = layers.get("CONFIG", {}) or {}
            hosts = config.get("hosts") or ["redis://127.0.0.1:6379/0"]
            host = hosts[0] if isinstance(hosts, (list, tuple)) else hosts
            urls = config.get("hosts") or [host]
            url = urls[0] if isinstance(urls, (list, tuple)) else urls
            self._client = redis.Redis.from_url(str(url))
        else:  # pragma: no cover - guarded by deployment guardrail
            self._client = None

    @staticmethod
    def _key(case_id: str) -> str:
        return f"presence:case:{case_id}"

    def _count(self, key: str) -> int:
        if self._client is None:  # pragma: no cover - guarded by deployment guardrail
            return 0
        now = time.time()
        self._client.zremrangebyscore(key, "-inf", now)
        self._client.expire(key, self.TTL_SECONDS + 10)
        return int(self._client.zcard(key))

    def join(self, case_id: str, user: Any) -> int:
        if self._client is None:  # pragma: no cover - guarded by deployment guardrail
            return 0
        self._client.zadd(
            self._key(case_id), {_presence_member(user): time.time() + self.TTL_SECONDS}
        )
        return self._count(self._key(case_id))

    def touch(self, case_id: str, user: Any) -> int:
        return self.join(case_id, user)

    def leave(self, case_id: str, user: Any) -> int:
        if self._client is None:  # pragma: no cover - guarded by deployment guardrail
            return 0
        self._client.zrem(self._key(case_id), _presence_member(user))
        return self._count(self._key(case_id))


class InMemoryPresenceStore:
    """Process-local presence for dev/test with the same contract."""

    TTL_SECONDS = 60

    def __init__(self) -> None:
        self._store: dict[str, dict[str, float]] = {}
        self._lock = threading.RLock()

    def _prune(self, case_id: str) -> None:
        now = time.time()
        members = self._store.get(case_id, {})
        for member, expiry in list(members.items()):
            if expiry <= now:
                members.pop(member, None)
        if not members:
            self._store.pop(case_id, None)

    def join(self, case_id: str, user: Any) -> int:
        with self._lock:
            self._store.setdefault(case_id, {})[_presence_member(user)] = (
                time.time() + self.TTL_SECONDS
            )
            self._prune(case_id)
            return len(self._store.get(case_id, {}))

    def touch(self, case_id: str, user: Any) -> int:
        return self.join(case_id, user)

    def leave(self, case_id: str, user: Any) -> int:
        with self._lock:
            self._store.get(case_id, {}).pop(_presence_member(user), None)
            self._prune(case_id)
            return len(self._store.get(case_id, {}))


_IN_MEMORY_STORE = InMemoryPresenceStore()


def get_presence_store() -> RedisPresenceStore | InMemoryPresenceStore:
    """Redis in production; one shared in-memory store for dev/test processes."""
    if connection.vendor != "postgresql" or not _REDIS_AVAILABLE:
        return _IN_MEMORY_STORE
    return RedisPresenceStore()
