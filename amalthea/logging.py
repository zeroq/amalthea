"""Logging helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast


class SanitizeRequestPathFilter(logging.Filter):
    """Escape CR/LF in request log records to prevent log forging.

    Django's ``django.request`` logger emits ``request.path`` verbatim in 4xx/5xx
    messages. An attacker-controlled path can inject forged log lines. We sanitize
    the formatted record args and message defensively.
    """

    @staticmethod
    def _sanitize(value: object) -> object:
        if isinstance(value, str):
            return value.replace("\r", "\\r").replace("\n", "\\n")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._sanitize(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: self._sanitize(v) for k, v in record.args.items()}
        elif record.args is not None:
            # django-stubs narrows args to tuple/mapping/None; other runtime shapes are
            # still sanitized, but the cast keeps the assignment type-checkable.
            record.args = cast(
                "tuple[object, ...] | Mapping[str, object] | None",
                self._sanitize(record.args),
            )

        record.msg = self._sanitize(record.msg)
        return True
