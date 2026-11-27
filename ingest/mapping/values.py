"""Value coercion and transform registry for the mapping engine."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Final

#: Severity synonyms observed across vendors, mapped onto the project's 1-4 vocabulary
#: (`core.enums.SEVERITY_CHOICES`). Keys are compared case-insensitively after stripping.
_SEVERITY_BY_NAME: Final[dict[str, int]] = {
    # 1 — Low
    "unknown": 1,
    "informational": 1,
    "info": 1,
    "negligible": 1,
    "low": 1,
    "minor": 1,
    "benign": 1,
    "intel": 1,
    "intelligence": 1,
    "not classified": 1,
    # 2 — Medium
    "medium": 2,
    "moderate": 2,
    "suspicious": 2,
    "unverified": 2,
    "test": 2,
    "ambiguous": 2,
    # 3 — High
    "high": 3,
    "severe": 3,
    "major": 3,
    "malicious": 3,
    "important": 3,
    "elevated": 3,
    # 4 — Critical
    "critical": 4,
    "urgent": 4,
    "emergency": 4,
    "catastrophic": 4,
    "system down": 4,
}

#: A 0-100 score → severity, as `(threshold, severity)` steps. A score *below* the first threshold is
#: severity 1, so the bands read "at 20 it is a Medium, at 50 a High, at 80 a Critical" — the plan's
#: `<20→1, 20-49→2, 50-79→3, ≥80→4`. Overridable per rule (`fields.severity.bands`).
DEFAULT_SCORE_BANDS: Final[tuple[tuple[int, int], ...]] = ((20, 2), (50, 3), (80, 4))

DEFAULT_MAX_LENGTH: Final[int] = 200

_WHITESPACE = re.compile(r"\s+")
#: Fractional seconds are optional and may be long: CrowdStrike sends
#: `2026-10-15T15:31:38.403385204Z` (9 digits of nanoseconds).
_TIMESTAMP = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[Tt ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def _as_text(value: Any, max_length: int = DEFAULT_MAX_LENGTH) -> str | None:
    if isinstance(value, (dict, list)):
        # A whole subtree is almost never a title; `str()` of it would be a JSON blob in a column
        # sized for a sentence. Callers that want the node use `json`/`list` transforms.
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    if not text:
        return None
    return text[:max_length]


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _severity_from_name(value: Any, value_maps: dict[str, int] | None = None) -> int | None:
    if isinstance(value, bool):
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip().lower()
    if not text:
        return None
    if value_maps and text in value_maps:
        return value_maps[text]
    if text.isdigit():
        return _as_int(text) if 1 <= int(text) <= 4 else None
    return _SEVERITY_BY_NAME.get(text)


def _severity_from_score(
    value: Any, bands: tuple[tuple[int, int], ...] = DEFAULT_SCORE_BANDS
) -> int | None:
    score = _as_int(value)
    if score is None:
        return None
    if score < 0 or score > 100:
        # A vendor that means 1-4 by "score" is not answered by guessing at 0-100 bands.
        return None
    result = 1  # below the first threshold

    for threshold, severity in bands:
        if score >= threshold:
            result = severity
    return result


def _severity_from_int(value: Any) -> int | None:
    number = _as_int(value)
    return number if number is not None and 1 <= number <= 4 else None


def _as_datetime(value: Any) -> datetime | None:
    """ISO-8601/RFC-3339 (any fractional-second width, `Z` or an offset) or epoch s/ms.

    Hand-rolled rather than `dateutil`/`fromisoformat` because both of Amalthea's real feeds send
    something CPython's `fromisoformat` only learned in 3.11 *and* with nanosecond digits it still
    refuses — and returning `None` (the field then falls back) beats a guess.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds since the epoch
            seconds /= 1000.0
        if seconds <= 0:
            return None
        return datetime.fromtimestamp(seconds, tz=UTC)
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP.match(value.strip())
    if not match:
        return None
    try:
        # Unpacked into names rather than splatted: `datetime(*(int(part) for part in ...))` is a
        # star-arg the stubs cannot map onto the parameter list, and mypy is the pre-push gate.
        year, month, day = (int(part) for part in match.group("date").split("-"))
        hour, minute, second = (int(part) for part in match.group("time").split(":"))
        frac = match.group("frac") or ""
        micro = int((frac + "000000")[:6]) if frac else 0
        moment = datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
    except ValueError:
        return None
    tz = match.group("tz")
    if tz is None or tz == "Z":
        # A naive timestamp is read as UTC. Amalthea's own API stores aware datetimes, and every feed
        # seen so far either sends `Z` or an offset, so an offset-less string is far more likely to be
        # UTC than "local time of whatever worker happens to poll it".
        return moment.replace(tzinfo=UTC)
    sign = 1 if tz[0] == "+" else -1
    digits = tz[1:].replace(":", "")
    try:
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
    except ValueError:
        return None
    # `offset` is the *wall clock's* offset from UTC: `2026-01-02T03:04:05+02:00` is 01:04:05 UTC.
    return moment.replace(tzinfo=timezone(sign * offset)).astimezone(UTC)


def _as_lower(value: Any) -> str | None:
    text = _as_text(value)
    return text.lower() if text else None


def _as_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple)) else [value]
    out = [text for item in items if (text := _as_text(item)) is not None]
    return out or None


def _as_json(value: Any) -> Any:
    return value


def _tlp(value: Any) -> int | None:
    names = {"white": 0, "green": 1, "amber": 2, "red": 3, "unknown": 4}
    number = _as_int(value)
    if number is not None:
        return number if 0 <= number <= 4 else None
    return names.get(str(value).strip().lower())


def _pap(value: Any) -> int | None:
    names = {"white": 0, "green": 1, "amber": 2, "red": 3}
    number = _as_int(value)
    if number is not None:
        return number if 0 <= number <= 3 else None
    return names.get(str(value).strip().lower())


#: The closed set of transform names. A name outside this mapping is a *config* error, reported by
#: `validate_config` at save time rather than discovered while an alert is being triaged.
TRANSFORMS: Final[dict[str, Callable[..., Any]]] = {
    "str": _as_text,
    "str_lower": _as_lower,
    "int": _as_int,
    "severity_name": _severity_from_name,
    "severity_score": _severity_from_score,
    "severity_int": _severity_from_int,
    "datetime": _as_datetime,
    "list": _as_list,
    "json": _as_json,
    "tlp": _tlp,
    "pap": _pap,
}
