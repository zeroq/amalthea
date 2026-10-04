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
        if ts > 1_000_000_000_000:  # ms
            dt = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
        else:
            dt = datetime.fromtimestamp(ts, tz=UTC)
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
