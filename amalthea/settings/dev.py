"""
Development settings.
"""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "amalthea.sqlite3",  # noqa: F405
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# Local dev has no Redis broker: run Celery eagerly so the MVP loop (webhook → case →
# observable extraction → playbook execution → timeline) works end-to-end out of the box.
# Production overrides these via Celery config; see celery.py and the prod deployment docs.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
