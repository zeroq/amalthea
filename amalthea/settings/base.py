"""
Amalthea base settings.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "channels",
    "csp",
    "core",
    "identity",
    "compat",
    "cases",
    "alerts",
    "observables",
    "ingest",
    "automation",
    "realtime",
    "services",
    "ui",
    "query",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Content Security Policy — strict 'self' only (ADR-003). All assets (HTMX, Font Awesome,
# Tailwind-compiled CSS) are self-hosted under /static/, so nothing external is allowed and
# neither `'unsafe-inline'` nor `'unsafe-eval'` is needed. Defined here rather than in `prod.py`
# so every environment (test included) translates it identically and one end-to-end response
# assertion exercises the exact directive set production emits.
#
# django-csp 4.x reads ONLY `CONTENT_SECURITY_POLICY`; the legacy `CSP_*` names are a silent
# no-op (and were never even validated while the `csp` app was absent from INSTALLED_APPS —
# `csp.E001`). `object-src 'none'` and `frame-ancestors 'none'` re-state the X-Frame-Options:
# DENY / MIME-sniffing hardening at the CSP layer for older or non-header-honouring agents.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "font-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
    }
}

ROOT_URLCONF = "amalthea.urls"
WSGI_APPLICATION = "amalthea.wsgi.application"
ASGI_APPLICATION = "amalthea.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Attachments (T2 P3) --------------------------------------------------
# Hard ceiling for one uploaded attachment, checked *before* the bytes are written to permanent
# storage. 25 MiB holds a PCAP slice or a mailbox export while keeping a single compromised
# credential from filling the volume in one request.
ATTACHMENT_MAX_BYTES = int(os.getenv("AMALTHEA_MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024)))
# Prefix under MEDIA_ROOT that attachment blobs live in: the stored name is opaque and the prefix
# only groups them so an operator can find (and quota) the attachment volume.
ATTACHMENT_STORAGE_PREFIX = os.getenv("AMALTHEA_ATTACHMENT_PREFIX", "attachments")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "identity.User"

# `@login_required` on the UI views must redirect to the analyst sign-in page, not Django's
# stock `/accounts/login/`. The route is resolved by URL name so the setting survives URL moves.
LOGIN_URL = "login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "compat.auth.ApiKeyAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        # Read-only API keys must fail closed on every mutating verb (plan §11-5, deviation
        # P10-z). The class was defined in Phase 8 but never listed here, so `scope="read"`
        # could PATCH/POST/DELETE exactly like a readwrite key. It runs *after*
        # `IsAuthenticated`, so an anonymous caller is still a 401 and never reaches it.
        # `ScopePermission` returns True for anything that is not a read-scoped ApiKey, so
        # session and basic auth are unaffected; `login`/`logout` carry an explicit `AllowAny`,
        # which replaces this list outright for those two views.
        "compat.auth.ScopePermission",
    ],
    "DEFAULT_PAGINATION_CLASS": None,
    "PAGE_SIZE": None,
    "EXCEPTION_HANDLER": "compat.errors.thehive_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/min",
        "user": "1000/min",
    },
}

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- Ingestion limits (AC4.5 / AC4.7) ------------------------------------
# These have named env vars in `.env.example` but were never read, so the documented knobs were
# inert and every deployment silently ran on the view-module fallbacks. Reading them here makes the
# documented limit the effective one.
WEBHOOK_MAX_BODY_SIZE = int(os.getenv("AMALTHEA_MAX_WEBHOOK_BYTES", str(5 * 1024 * 1024)))
# Guards the mapping engine's recursive walk over an attacker-supplied document.
WEBHOOK_MAX_DEPTH = int(os.getenv("AMALTHEA_MAX_WEBHOOK_DEPTH", "30"))
# Per-source and per-IP, per minute. A webhook is machine-to-machine, so DRF's `AnonRateThrottle`
# does not apply (it keys on the authenticated user, which is `None` here); the receiver throttles
# itself on these instead.
WEBHOOK_RATE_SOURCE_PER_MIN = int(os.getenv("AMALTHEA_WEBHOOK_RATE_SOURCE", "60"))
WEBHOOK_RATE_IP_PER_MIN = int(os.getenv("AMALTHEA_WEBHOOK_RATE_IP", "300"))

# --- Cache ---------------------------------------------------------------
# Backs the webhook rate limiter. `LocMemCache` is per-process, so a multi-process deployment
# would enforce the limit once per worker — the setting names Redis explicitly so an operator can
# move the counters somewhere shared, which is what production needs.
CACHES = {
    "default": {
        "BACKEND": os.getenv(
            "AMALTHEA_CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"
        ),
        "LOCATION": os.getenv("AMALTHEA_CACHE_LOCATION", "amalthea-webhook-throttle"),
    }
}
