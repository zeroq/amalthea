"""Login rate limiting utilities.

Provides IP-based and username-based rate limiting for authentication endpoints.
Uses Django's cache framework (Redis in production, locmem in dev).
"""

from __future__ import annotations

from django.core.cache import cache
from django.http import HttpRequest

# Configuration
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_WINDOW = 300  # 5 minutes
LOGIN_WINDOW = 900  # 15 minutes


def _get_client_ip(request: HttpRequest) -> str:
    """Extract client IP from request, handling proxies."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _make_keys(request: HttpRequest, username: str | None = None) -> tuple[str, str | None]:
    """Generate cache keys for IP and optional username."""
    ip = _get_client_ip(request)
    ip_key = f"login_fail:ip:{ip}"
    user_key = f"login_fail:user:{username}" if username else None
    return ip_key, user_key


def _get_attempts(key: str) -> int:
    """Get current failed attempt count for a key."""
    return cache.get(key, 0)


def _increment_attempts(key: str) -> int:
    """Increment and return failed attempt count for a key."""
    current = cache.get(key, 0)
    new_count = current + 1
    cache.set(key, new_count, LOGIN_WINDOW)
    return new_count


def _clear_attempts(key: str) -> None:
    """Clear failed attempts for a key."""
    cache.delete(key)


def _is_locked_out(request: HttpRequest, username: str | None = None) -> tuple[bool, int]:
    """Check if IP or username is locked out.

    Returns (is_locked, retry_after_seconds).
    """
    ip_key, user_key = _make_keys(request, username)

    # Check IP lockout
    ip_attempts = _get_attempts(ip_key)
    if ip_attempts >= LOGIN_MAX_ATTEMPTS:
        ttl = _get_cache_ttl(ip_key)
        return True, max(ttl, 1)

    # Check username lockout
    if user_key:
        user_attempts = _get_attempts(user_key)
        if user_attempts >= LOGIN_MAX_ATTEMPTS:
            ttl = _get_cache_ttl(user_key)
            return True, max(ttl, 1)

    return False, 0


def _get_cache_ttl(key: str) -> int:
    """Get TTL for a cache key, with fallback for LocMemCache which doesn't support ttl()."""
    try:
        return cache.ttl(key)
    except AttributeError:
        # LocMemCache doesn't support ttl(), fall back to window duration
        return LOGIN_WINDOW


def record_failed_login(request: HttpRequest, username: str | None = None) -> None:
    """Record a failed login attempt for IP and optional username."""
    ip_key, user_key = _make_keys(request, username)
    _increment_attempts(ip_key)
    if user_key:
        _increment_attempts(user_key)


def record_successful_login(request: HttpRequest, username: str | None = None) -> None:
    """Clear failed login counters on successful login."""
    ip_key, user_key = _make_keys(request, username)
    _clear_attempts(ip_key)
    if user_key:
        _clear_attempts(user_key)


def check_login_rate_limit(request: HttpRequest, username: str | None = None) -> tuple[bool, int]:
    """Check if login should be rate limited.

    Returns (is_allowed, retry_after_seconds).
    """
    is_locked, retry_after = _is_locked_out(request, username)
    return not is_locked, retry_after
