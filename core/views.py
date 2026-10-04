from __future__ import annotations

from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError
from django.http import HttpRequest, JsonResponse
from redis import Redis
from redis.exceptions import RedisError


def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    # Check DB
    try:
        conn = connections["default"]
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError:
        return JsonResponse({"status": "error", "detail": "db"}, status=503)

    # Check Redis
    try:
        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        r = Redis.from_url(redis_url)
        r.ping()
    except (RedisError, Exception):
        return JsonResponse({"status": "error", "detail": "redis"}, status=503)

    # Check migrations (simple check)
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connections["default"])
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            return JsonResponse({"status": "error", "detail": "migrations_pending"}, status=503)
    except Exception:
        return JsonResponse({"status": "error", "detail": "migration_check_failed"}, status=503)

    return JsonResponse({"status": "ok"})
