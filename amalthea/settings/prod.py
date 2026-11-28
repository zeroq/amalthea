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

if WEBHOOK_PREAUTH_RATE_IP_PER_MIN <= 0:  # noqa: F405
    raise ImproperlyConfigured(
        "AMALTHEA_WEBHOOK_PREAUTH_IP must be positive in production; the pre-auth webhook "
        "budget is the only throttle before an unauthenticated token lookup."
    )

# The MCP endpoint is fail-closed, but once enabled it must advertise a real
# HTTPS resource identifier and have an explicit Host allowlist.  Refusing at
# boot is safer than starting with localhost defaults in production.
if MCP_ENABLED:  # noqa: F405
    if not MCP_BASE_URL:  # noqa: F405
        raise ImproperlyConfigured(
            "AMALTHEA_MCP_BASE_URL must be set when AMALTHEA_MCP_ENABLED=true in production."
        )
    if not MCP_BASE_URL.startswith("https://"):  # noqa: F405
        raise ImproperlyConfigured("AMALTHEA_MCP_BASE_URL must use https:// in production.")
    if not MCP_ALLOWED_HOSTS:  # noqa: F405
        raise ImproperlyConfigured("AMALTHEA_MCP_ALLOWED_HOSTS must not be empty in production.")

# Phase 3 OAuth mode gating — fail fast if OAuth is configured without the
# required issuer/JWKS settings.  Static mode is always available; both/oauth
# require the full profile.
_auth_mode = os.getenv("AMALTHEA_MCP_AUTH_MODE", "static").lower()
if _auth_mode in ("oauth", "both"):
    _issuer = os.getenv("AMALTHEA_MCP_OAUTH_ISSUER", "").strip()
    _jwks_url = os.getenv("AMALTHEA_MCP_OAUTH_JWKS_URL", "").strip()
    if not _issuer:
        raise ImproperlyConfigured(
            "AMALTHEA_MCP_OAUTH_ISSUER must be set when MCP_AUTH_MODE is oauth or both."
        )
    if not _jwks_url:
        raise ImproperlyConfigured(
            "AMALTHEA_MCP_OAUTH_JWKS_URL must be set when MCP_AUTH_MODE is oauth or both."
        )
    if not _issuer.startswith("https://"):
        raise ImproperlyConfigured("AMALTHEA_MCP_OAUTH_ISSUER must use https:// in production.")
    if not _jwks_url.startswith("https://"):
        raise ImproperlyConfigured("AMALTHEA_MCP_OAUTH_JWKS_URL must use https:// in production.")
    # Reject localhost/private IP literals in JWKS URL (SSRF guard)
    from ipaddress import ip_address as _ip_addr

    try:
        host = _jwks_url.split("/")[2]
        _ip_addr(host)
        # If we get here, it's a valid IP literal; reject it
        raise ImproperlyConfigured(
            "AMALTHEA_MCP_OAUTH_JWKS_URL must not be a localhost or private IP literal."
        )
    except (IndexError, ValueError):
        # Not a valid IP literal — good, it's a hostname
        pass
    # Reject 'none' and symmetric algorithms
    _allowed = os.getenv("AMALTHEA_MCP_OAUTH_JWT_ALGS", "RS256").split(",")
    _allowed = [a.strip() for a in _allowed if a.strip()]
    if "none" in _allowed or any(a.startswith("HS") for a in _allowed):
        raise ImproperlyConfigured(
            "AMALTHEA_MCP_OAUTH_JWT_ALGS must not contain 'none' or symmetric algorithms such as HS256."
        )

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
