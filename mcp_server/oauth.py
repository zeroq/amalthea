"""OAuth 2.1 resource-server JWT access-token verification for Amalthea MCP.

Amalthea is a resource server only: short-lived access tokens are issued by an
external IdP and validated locally against a configured issuer/JWKS. Tokens are
never stored, and no token or full claim set is logged.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from mcp.server.auth.provider import AccessToken, TokenVerifier

from mcp_server.context import current_request
from mcp_server.models import PHASE1_SCOPES, PHASE2_SCOPES, McpOAuthBinding

logger = logging.getLogger(__name__)

try:
    import jwt
except ImportError:  # pragma: no cover - dependency is pinned in requirements
    jwt = None  # type: ignore[assignment]

_jwt: Any = jwt

JWKS_CACHE_PREFIX = "mcp_oauth_jwks"
JWKS_CACHE_MIN_SECONDS = 300
JWKS_CACHE_DEFAULT_SECONDS = 3600
CLOCK_SKEW_DEFAULT_SECONDS = 30
CLOCK_SKEW_MAX_SECONDS = 60
ALLOWED_SCOPES = frozenset(PHASE1_SCOPES) | frozenset(PHASE2_SCOPES)
RESERVED_SCOPES = frozenset({"mcp:write", "mcp:admin"})


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


def _is_safe_jwks_url(url: str) -> bool:
    """HTTPS-only, literal/private/loopback-host拒绝 before any network access."""
    if not url or not url.startswith("https://"):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False

    candidates: list[str] = [host]
    try:
        candidates = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            candidates = []
            for info in socket.getaddrinfo(host, parsed.port or 443):
                sockaddr = info[4]
                if isinstance(sockaddr, (tuple, list)) and sockaddr:
                    resolved = sockaddr[0]
                    if isinstance(resolved, str):
                        candidates.append(resolved)
            if not candidates:
                return False
        except OSError:
            return False

    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            return False
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def _fetch_jwks(jwks_url: str) -> dict[str, Any] | None:
    """Fetch JWKS once. No redirects; TLS verification stays enabled."""
    if not _is_safe_jwks_url(jwks_url):
        return None
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(  # noqa: S310 - scheme/host validated by _is_safe_jwks_url
        jwks_url, headers={"User-Agent": "amalthea-mcp"}
    )
    try:
        with opener.open(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        return None
    return payload


def _log_failure(reason: str) -> None:
    request = current_request()
    logger.info(
        "mcp oauth auth_failed reason=%s request_id=%s",
        reason,
        request.request_id or "-",
    )


class McpOAuthTokenVerifier(TokenVerifier):
    """Validate RFC 9068-style JWT access tokens and resolve OAuth bindings."""

    def __init__(self) -> None:
        self._jwks_url = str(getattr(settings, "MCP_OAUTH_JWKS_URL", "") or "").strip()
        self._issuer = str(getattr(settings, "MCP_OAUTH_ISSUER", "") or "").strip()
        self._resource = str(getattr(settings, "MCP_RESOURCE_URL", "") or "").strip()
        self._jwks_cache_seconds = max(
            JWKS_CACHE_MIN_SECONDS,
            int(getattr(settings, "MCP_OAUTH_JWKS_CACHE_SECONDS", JWKS_CACHE_DEFAULT_SECONDS)),
        )
        self._clock_skew = min(
            CLOCK_SKEW_MAX_SECONDS,
            max(
                0,
                int(getattr(settings, "MCP_OAUTH_CLOCK_SKEW_SECONDS", CLOCK_SKEW_DEFAULT_SECONDS)),
            ),
        )
        raw_algs = getattr(settings, "MCP_OAUTH_JWT_ALGS", ["RS256"])
        if isinstance(raw_algs, str):
            raw_algs = [item.strip() for item in raw_algs.split(",")]
        self._allowed_algs = tuple(item for item in raw_algs if item) or ("RS256",)

    async def verify_token(self, token: str) -> AccessToken | None:
        return await sync_to_async(self._verify_sync, thread_sensitive=True)(token)

    def _get_jwks(self, *, force: bool = False) -> dict[str, Any] | None:
        cache_key = f"{JWKS_CACHE_PREFIX}:{self._jwks_url}"
        cached_value = cache.get(cache_key)
        cached: dict[str, Any] | None = cached_value if isinstance(cached_value, dict) else None
        if cached is not None and not force:
            return cached
        fetched = _fetch_jwks(self._jwks_url)
        if fetched is not None:
            cache.set(cache_key, fetched, self._jwks_cache_seconds)
            return fetched
        return cached

    def _reject(self, reason: str) -> AccessToken | None:
        _log_failure(reason)
        return None

    def _verify_sync(self, token: str) -> AccessToken | None:
        if _jwt is None:
            return self._reject("jwks_fetch_failed")
        if not isinstance(token, str) or token.count(".") != 2:
            return self._reject("invalid_token")

        try:
            header = _jwt.get_unverified_header(token)
        except Exception:
            return self._reject("invalid_token")

        if header.get("typ") not in ("at+jwt", "application/at+jwt"):
            return self._reject("invalid_token")
        alg = header.get("alg")
        if (
            not alg
            or alg not in self._allowed_algs
            or alg.lower() == "none"
            or alg.startswith("HS")
        ):
            return self._reject("bad_algorithm")
        kid = header.get("kid")
        if not kid:
            return self._reject("unknown_kid")

        jwks = self._get_jwks()
        if jwks is None:
            return self._reject("jwks_fetch_failed")
        jwk = next((item for item in jwks.get("keys", []) if item.get("kid") == kid), None)
        if jwk is None:
            # Key rotation: allow one forced refresh per kid within a bounded window.
            refresh_key = f"{JWKS_CACHE_PREFIX}:refresh:{self._jwks_url}:{kid}"
            if cache.add(refresh_key, 1, timeout=60):
                refreshed = self._get_jwks(force=True)
                if refreshed is not None:
                    jwks = refreshed
                    jwk = next(
                        (item for item in jwks.get("keys", []) if item.get("kid") == kid),
                        None,
                    )
        if jwk is None:
            return self._reject("unknown_kid")
        try:
            key = _jwt.PyJWK(jwk).key
        except Exception:
            return self._reject("jwks_fetch_failed")

        try:
            claims = _jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self._resource,
                issuer=self._issuer,
                leeway=self._clock_skew,
                options={"require": ["iss", "aud", "exp"]},
            )
        except _jwt.ExpiredSignatureError:
            return self._reject("expired")
        except _jwt.InvalidAudienceError:
            return self._reject("invalid_audience")
        except _jwt.InvalidIssuerError:
            return self._reject("invalid_issuer")
        except Exception:
            return self._reject("bad_signature")

        if claims.get("iss") != self._issuer:
            return self._reject("invalid_issuer")
        audience = claims.get("aud")
        audience_values = [audience] if isinstance(audience, str) else list(audience or [])
        if self._resource not in audience_values:
            return self._reject("invalid_audience")

        now = int(time.time())
        try:
            exp = int(claims["exp"])
        except (KeyError, TypeError, ValueError):
            return self._reject("invalid_token")
        if exp < now - self._clock_skew:
            return self._reject("expired")
        iat = claims.get("iat")
        if iat is not None:
            try:
                if int(iat) > now + self._clock_skew:
                    return self._reject("invalid_token")
            except (TypeError, ValueError):
                return self._reject("invalid_token")

        subject = claims.get("sub")
        client_id = claims.get("client_id")
        if not isinstance(subject, str) or not subject.strip():
            return self._reject("invalid_token")
        if not isinstance(client_id, str) or not client_id.strip():
            return self._reject("invalid_token")
        subject = subject.strip()
        client_id = client_id.strip()

        binding = (
            McpOAuthBinding.objects.select_related("organisation", "service_user")
            .filter(
                issuer=self._issuer,
                client_id=client_id,
                subject=subject,
                is_active=True,
            )
            .first()
        )
        if binding is None:
            return self._reject("unknown_binding")

        now_dt = timezone.now()
        McpOAuthBinding.objects.filter(pk=binding.pk).filter(
            Q(last_used_at__isnull=True) | Q(last_used_at__lte=now_dt - timedelta(minutes=1))
        ).update(last_used_at=now_dt)

        token_scopes = claims.get("scope", "")
        scope_values = (
            token_scopes.split() if isinstance(token_scopes, str) else list(token_scopes or [])
        )
        validated_scopes: set[str] = set()
        for scope in scope_values:
            if scope in RESERVED_SCOPES:
                return self._reject("scope_denied")
            if not scope.startswith("mcp:"):
                continue
            if scope not in ALLOWED_SCOPES:
                return self._reject("scope_denied")
            validated_scopes.add(scope)
        final_scopes = sorted(validated_scopes & set(binding.allowed_scopes or []) & ALLOWED_SCOPES)
        if not final_scopes:
            return self._reject("scope_denied")

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=final_scopes,
            expires_at=exp,
            resource=self._resource,
            subject=subject,
            claims={
                "auth_mode": "oauth",
                "oauth_binding_id": str(binding.id),
                "organisation_id": str(binding.organisation_id),
                "user_id": str(binding.service_user_id),
                "role": binding.service_user.role,
                "client_id": client_id,
                "subject": subject,
                "resource": self._resource,
            },
        )
