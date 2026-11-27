"""Shared helpers for wire serializers."""

from __future__ import annotations

from typing import Any


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _username(user: Any) -> str | None:
    return getattr(user, "login", None) if user is not None else None
