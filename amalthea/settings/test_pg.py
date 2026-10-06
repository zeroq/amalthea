"""
Postgres test settings (TODO 3.1 verification gate).

`amalthea.settings.test` pins SQLite in-memory; this module is the same shape with a real
Postgres engine so the Postgres-only conformance surface (H-6 collision test, sequence race,
index catalog reads, btree tuple cap, partial-index predicate) can actually run.

Usage:

    docker compose up -d postgres          # amalthea/amalthea@127.0.0.1:5432
    pytest --ds=amalthea.settings.test_pg

The container's bootstrap user (POSTGRES_USER=amalthea) is a superuser, so pytest-django can
create/destroy its `test_amalthea` database. Point elsewhere with the standard POSTGRES_*
environment variables if the container is not the target.
"""

import os

from .base import *  # noqa: F403

DEBUG = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_TEST_NAME", "amalthea"),
        "USER": os.getenv("POSTGRES_USER", "amalthea"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "amalthea"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        # The conformance suite asserts on Postgres-specific DDL (descent into indexdef,
        # partial-index predicates); keep the test DB close to what prod.py would produce.
        "CONN_MAX_AGE": 0,
        "TEST": {"NAME": "test_amalthea"},
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
