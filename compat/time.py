from __future__ import annotations

from datetime import UTC, datetime


def to_epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return int(dt.timestamp() * 1000)


def parse_timestamp(v: int | str | float | None) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if v < 0:
            return None
        ts = float(v)
        # Hostile magnitudes (`float("inf")`, `float("nan")`, `1e18`) make `fromtimestamp`
        # raise OverflowError/OSError/ValueError, which used to escape as a 500. Degrading to
        # None sends them down the same clean 400 "invalid timestamp" path as any other
        # unparseable value (auditor F5). `nan < 0` is False, so it reaches the seconds
        # branch and is caught there.
        try:
            if ts > 1_000_000_000_000:  # ms
                dt = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
            else:
                dt = datetime.fromtimestamp(ts, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
        return dt
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        # try ISO 8601
        try:
            if s.endswith("Z"):
                s2 = s[:-1] + "+00:00"
            else:
                s2 = s
            dt = datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)
            return dt
        except (ValueError, IndexError, TypeError):
            pass
        # try int
        try:
            return parse_timestamp(int(s))
        except (ValueError, IndexError, TypeError):
            pass
        try:
            return parse_timestamp(float(s))
        except (ValueError, IndexError, TypeError):
            pass
    return None
