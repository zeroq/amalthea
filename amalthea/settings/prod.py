"""
Production settings.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

# Prod re-reads DJANGO_SECRET_KEY rather than validating the value inherited from base: base's
# placeholder fallback is what makes a forgotten key invisible, so the contract is restated here
# explicitly and the sentinel string stays in one obvious place.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 50 or SECRET_KEY == "dev-only-insecure-change-me":  # noqa: S105
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong, unique value (>=50 chars) in production. "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(64))'."
    )

# `strip()` on the filter so a whitespace-only value (e.g. `"   "`) is a boot-time refusal
# rather than a pattern that can never match a Host header.
ALLOWED_HOSTS = [host for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if host.strip()]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list at least one host in production.")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Content Security Policy — the strict 'self'-only policy (ADR-003) lives in `base.py`
# (`CONTENT_SECURITY_POLICY`), which is where `django-csp` 4.x reads it from. Production imports
# it unchanged rather than restating it, so there is a single definition to keep strict.

# Session cookie hardening — prevents XSS from accessing session cookie (TODO 6.11)
SESSION_COOKIE_HTTPONLY = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "amalthea"),
        "USER": os.getenv("POSTGRES_USER", "amalthea"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],  # noqa: F405
        },
    },
}
