from __future__ import annotations

from typing import Any

from argon2 import PasswordHasher
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.request import Request

from identity.models import ApiKey

User = get_user_model()
ph = PasswordHasher()


class ApiKeyAuthentication(BaseAuthentication):
    def authenticate(self, request: Request) -> tuple[Any, Any] | None:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth or not auth.startswith("Bearer "):
            return None
        token = auth.split("Bearer ", 1)[1].strip()
        if not token:
            raise AuthenticationFailed("Invalid API key")

        prefix = token[:12] if len(token) > 12 else token
        try:
            key = ApiKey.objects.filter(prefix=prefix, revoked_at__isnull=True).first()
            if not key:
                raise AuthenticationFailed("Invalid API key")
            try:
                ph.verify(key.key_hash, token)
            except Exception as exc:
                raise AuthenticationFailed("Invalid API key") from exc
            if key.user:
                return (key.user, key)
            return (AnonymousUser(), key)
        except AuthenticationFailed:
            raise
        except Exception as exc:
            raise AuthenticationFailed("Invalid API key") from exc

    def authenticate_header(self, request: Request) -> str:
        return "Bearer"


class ScopePermission:
    def has_permission(self, request: Request, view: Any) -> bool:
        auth = request.auth
        if (
            isinstance(auth, ApiKey)
            and auth.scope == "read"
            and request.method not in ("GET", "HEAD", "OPTIONS")
        ):
            raise PermissionDenied("Forbidden")
        return True
