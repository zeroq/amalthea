"""Mapping engine — the rules that turn a vendor payload into alert fields.

**Why this module owns the config language.** `mapping_config` was read in three unrelated spellings:
`ingest/views.py::_mapped` (a JSONPath, then a literal key, from a hardcoded key list),
`ingest/pipeline.py::extract_correlation_key` (seven spellings of one key, newest-first), and
`IngestionSource.mapping_config` itself (documented in prose only). The result was that a payload
which *states* its own meaning could only be read correctly if it happened to use the words we
guessed. On the dev data, `crowdstrike-edr` sends:

    {"severity_name": "Critical", "severity": 90, "name": "HiveCredTheft", …}

and every alert was ingested as `title="Alert"`, `severity=2` (Medium), with
`severity_defaulted: no severity mapping` — because a severity is a 1-4 int here and no transform
existed to read a name. This module is that transform, plus the schema around it.

**The contract.** `mapping_config` is one of two shapes, and both are supported forever:

*legacy* — `{"title": "$.title", "severity": "$.severity"}`: a bare `{target: jsonpath}` dict.
Normalized on read; the literal-key fallback of the old `_mapped` is preserved exactly, so a source
that was never configured keeps its old behaviour and AC4.3 ("an unconfigured source ingests without
error") stays provable from the same tests.

*v2* — `{"version": 2, "fields": {<target>: {"paths": [...], "transform": <name>, "default": …,
  "max_length": …}}, "value_maps": {…}}`: the shape the Settings editor writes.

Three properties are deliberate and load-bearing:

1. **`apply_mapping` is pure.** No ORM, no settings, no logging, no HTTP. Every tolerance is returned
   to the caller (warnings) rather than raised, so an operator's typo produces a recorded, visible
   alert instead of a 500 that teaches the sender to stop retrying — and so the same function can back
   the editor's "test this payload" preview without persisting anything.
2. **A malformed config is not an exception.** It is data an operator typed; the engine warns and
   degrades (`version` aside, every rule is validated leniently here and *strictly* by
   `validate_config`, which is what the form calls).
3. **`None` ≠ `""`.** An empty string counts as no match, because a payload with
   `{"description": ""}` did not answer the question — falling through to the next path is the
   behaviour that makes a candidate list useful.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Final

try:
    from jsonpath_ng import parse as _parse  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    # The package is a hard dependency; this branch exists so a missing wheel degrades the mapping
    # to "no mapping" instead of taking the whole ingest app down at import time.
    _parse = None


# --- JSONPath extraction ----------------------------------------------------


#: Compiled JSONPath expressions, keyed by source string. See `_compile`.
_COMPILED: dict[str, Any] = {}
#: A source's rules are at most 7 targets x 8 paths plus correlation keys; 512 leaves room for
#: dozens of configured feeds without unbounded growth.
_COMPILED_MAX = 512


def _compile(path: str) -> Any | None:
    """Return the compiled expression for `path`, or `None` if it is not a path.

    `jsonpath_ng.parse` runs a Lark grammar and costs ~1.8 ms here — and the engine walks up to a few
    dozen paths per webhook, so *not* caching this would spend tens of milliseconds per alert
    re-parsing strings that have not changed since the source was configured. The compiled expression
    is stateless: `find()` builds its own visitor, so sharing it across requests is safe. `None` is
    cached too, so a typo'd path costs nothing after the first alert.

    Bounded by hand rather than with `lru_cache` because `None` (a bad path) is a meaningful cached
    value and eviction order does not matter beyond "recently used".
    """
    if _parse is None or not path:
        return None
    if path in _COMPILED:
        return _COMPILED[path]
    try:
        compiled = _parse(path)
    except Exception:
        compiled = None
    if len(_COMPILED) >= _COMPILED_MAX:
        _COMPILED.clear()
    _COMPILED[path] = compiled
    return compiled


def extract_by_jsonpath(path: str, payload: Any) -> Any | None:
    """First value at JSONPath `path` in `payload`, or `None`.

    A bad path returns `None` rather than raising: `mapping_config` is operator-authored data and a
    typo in it should produce an alert with a `severity_defaulted` warning, not a 500 that teaches
    the sender to stop retrying.
    """
    expression = _compile(path)
    if expression is None:
        return None
    try:
        matches = expression.find(payload)
    except Exception:
        return None
    return matches[0].value if matches else None


# Retained for callers that predate the rename; `extract_by_jsonpath` is the public name.
_extract_by_jsonpath = extract_by_jsonpath


# --- Alert fields this engine fills -----------------------------------------

#: Every target a rule may name. `tlp`/`pap`/`tags` are deliberately absent (they came out of the
#: first wave), and `correlation_key` is not a target either — it keeps its own reader below.
TARGETS: Final[tuple[str, ...]] = (
    "title",
    "description",
    "summary",
    "severity",
    "date",
    "type",
    "source_ref",
)

#: The literal payload keys the *legacy* path also looked at, per target. These are the old
#: `_mapped(payload, mapping, "title", "summary", "message")` call sites, moved here so the engine
#: and the schema cannot drift apart. Order matters: it is the fallback chain.
LEGACY_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "title": ("title", "summary", "message"),
    "description": ("description",),
    "summary": ("summary",),
    "severity": ("severity", "severityLevel"),
    "date": ("date", "timestamp", "created_at"),
    "type": ("type", "event", "alertType"),
    "source_ref": ("sourceRef", "source_ref", "id"),
}

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

MAX_PATH_LEN: Final[int] = 200
MAX_PATHS_PER_FIELD: Final[int] = 8
MAX_FIELDS: Final[int] = 20
DEFAULT_MAX_LENGTH: Final[int] = 200

_WHITESPACE = re.compile(r"\s+")
#: Fractional seconds are optional and may be long: CrowdStrike sends
#: `2026-10-15T15:31:38.403385204Z` (9 digits of nanoseconds).
_TIMESTAMP = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[Tt ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


# --- Warnings ---------------------------------------------------------------


@dataclass(frozen=True)
class FieldWarning:
    """Why one target ended up defaulted.

    The free-text list on `Alert.ingestion_warnings["warnings"]` predates this engine and is pinned by
    existing tests, so both exist: a human string there, this structured form under `"mapping"`, where
    the *paths* that failed are what an operator needs to fix a rule.
    """

    target: str
    reason: str  # not_found | unknown_value | bad_type | bad_transform | truncated
    paths: tuple[str, ...] = ()
    value_any: Any = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"field": self.target, "reason": self.reason}
        if self.paths:
            out["paths"] = list(self.paths)
        if self.value_any is not None:
            out["value_preview"] = str(self.value_any)[:80]
        return out

    def as_text(self) -> str:
        paths = ", ".join(self.paths) if self.paths else "(no paths)"
        if self.reason == "not_found":
            return f"{self.target}_unmapped: nothing at {paths}"
        if self.reason == "unknown_value":
            return f"{self.target}_unknown_value: {paths} held {self.value_any!r}"
        return f"{self.target}_{self.reason}: {paths}"


@dataclass(frozen=True)
class AlertDraft:
    """What the payload said, resolved against one source's rules. Pure data."""

    title: str | None = None
    description: str | None = None
    summary: str | None = None
    severity: int | None = None
    date: datetime | None = None
    type: str | None = None
    source_ref: str | None = None
    warnings: tuple[str, ...] = ()
    field_warnings: tuple[FieldWarning, ...] = ()


# --- Transforms -------------------------------------------------------------


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
        hour, minute, second = (int(part) for part in match.group("time").split(":"))
        frac = match.group("frac") or ""
        micro = int((frac + "000000")[:6]) if frac else 0
        moment = datetime(
            *(int(part) for part in match.group("date").split("-")),
            hour,
            minute,
            second,
            micro,
            tzinfo=UTC,
        )
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


# --- Config normalization ---------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    target: str
    paths: tuple[str, ...] = ()
    transform: str = "str"
    has_default: bool = False
    default: Any = None
    max_length: int = DEFAULT_MAX_LENGTH
    bands: tuple[tuple[int, int], ...] = DEFAULT_SCORE_BANDS


@dataclass(frozen=True)
class _Config:
    version: int = 1
    rules: dict[str, _Rule] = field(default_factory=dict)
    value_maps: dict[str, dict[str, int]] = field(default_factory=dict)
    legacy_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _normalize(mapping_config: Any) -> _Config:
    """Read either shape into rules. Never raises: a bad config is data, not a crash.

    A v2 config is `{"version": 2, "fields": {...}}`; anything else that is a dict is the legacy
    `{target: jsonpath}` form, whose non-path keys (`correlation_key`, an unknown spelling) are
    ignored here rather than warned about — `apply_mapping` cannot tell a typo from a key another
    reader (`FALLBACK_KEYS`/`extract_correlation_key`) legitimately uses.
    """
    if not isinstance(mapping_config, dict) or not mapping_config:
        return _Config()

    fields = mapping_config.get("fields")
    value_maps = mapping_config.get("value_maps")
    if mapping_config.get("version") == 2 or isinstance(fields, dict):
        rules: dict[str, _Rule] = {}
        raw_value_maps = value_maps if isinstance(value_maps, dict) else {}
        # `fields` may itself be the operator's typo (`{"fields": "nope"}`); an empty rule set is the
        # only useful reading of that, and `validate_config` is what refuses to save it.
        for target, raw in (fields if isinstance(fields, dict) else {}).items():
            if target not in TARGETS or not isinstance(raw, dict):
                continue
            paths = tuple(
                path.strip()
                for path in (raw.get("paths") or [])
                if isinstance(path, str) and path.strip() and len(path.strip()) <= MAX_PATH_LEN
            )[:MAX_PATHS_PER_FIELD]
            transform = raw.get("transform") or "str"
            if transform not in TRANSFORMS:
                transform = "str"
            payload_default = raw.get("severity_default", raw.get("default"))
            max_length = raw.get("max_length")
            if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
                max_length = DEFAULT_MAX_LENGTH
            bands = DEFAULT_SCORE_BANDS
            if transform == "severity_score":
                raw_bands = raw.get("bands")
                if isinstance(raw_bands, list):
                    parsed = []
                    for step in raw_bands:
                        if (
                            isinstance(step, (list, tuple))
                            and len(step) == 2
                            and all(isinstance(part, int) for part in step)
                        ):
                            parsed.append((int(step[0]), int(step[1])))
                    if parsed:
                        bands = tuple(parsed)
            rules[target] = _Rule(
                target=target,
                paths=paths,
                transform=transform,
                has_default="default" in raw or "severity_default" in raw,
                default=payload_default,
                max_length=max_length,
                bands=bands,
            )
        return _Config(version=2, rules=rules, value_maps=raw_value_maps)

    # Legacy: every string value is a JSONPath for the key it sits under. The key list is the old
    # `_mapped(payload, mapping, *keys)` fallback, so `{"title": "$.title"}` still answers "title".
    legacy: dict[str, tuple[str, ...]] = {}
    for target, keys in LEGACY_KEYS.items():
        paths = tuple(
            mapping_config[key].strip()
            for key in keys
            if isinstance(mapping_config.get(key), str) and mapping_config[key].strip()
        )
        if paths:
            legacy[target] = paths
    return _Config(version=1, legacy_paths=legacy)


# --- The engine -------------------------------------------------------------


def _is_answer(value: Any) -> bool:
    """`None` and `""` are not answers; `0`/`False` are.

    An empty description is a field the payload did not fill, so the next candidate path should get a
    turn — which is the whole point of a candidate list.
    """
    return value is not None and not (isinstance(value, str) and not value.strip())


def _apply_transform(rule: _Rule, value: Any, value_maps: dict[str, int] | None) -> Any:
    """Run one rule's transform. The four that need rule/context parameters are spelled out; the rest
    are the plain functions in `TRANSFORMS`."""
    name = rule.transform
    if name == "severity_name":
        return _severity_from_name(value, value_maps)
    if name == "severity_score":
        return _severity_from_score(value, rule.bands)
    if name == "str":
        return _as_text(value, rule.max_length)
    if name == "str_lower":
        text = _as_text(value, rule.max_length)
        return text.lower() if text else None
    function = TRANSFORMS.get(name)
    return None if function is None else function(value)


def apply_mapping(
    mapping_config: dict[str, Any] | None,
    payload: Any,
    *,
    default_severity: int = 2,
) -> AlertDraft:
    """Resolve `payload` into an `AlertDraft` using one source's rules.

    Every field is best-effort and order-dependent:

    1. a **v2 rule**'s candidate paths, transformed;
    2. the **legacy** key list (`mapping_config["title"]` as a JSONPath, …);
    3. the **literal** payload key (`payload["title"]`), which is what makes a source with no config
       at all still produce something;
    4. the rule's `default`, else the field's own fallback (`default_severity`, empty string).

    Nothing raises. Whatever was left unanswered is recorded in `field_warnings`, and the free-text
    equivalents in `warnings` — because a silent default is how a Medium alert hides a Critical.
    """
    config = _normalize(mapping_config)
    warnings: list[FieldWarning] = []
    values: dict[str, Any] = {}

    for target in TARGETS:
        rule = config.rules.get(target)
        # A v2 rule's paths are authoritative: when it names any, they are the whole candidate list.
        if rule is not None:
            candidates = [("rule", path) for path in rule.paths]
        else:
            legacy = config.legacy_paths.get(target, ())
            candidates = [("mapping", path) for path in legacy]
            candidates += [("literal", key) for key in LEGACY_KEYS[target]]

        found = None
        for kind, candidate in candidates:
            if kind == "literal":
                value = payload.get(candidate) if isinstance(payload, dict) else None
            else:
                value = extract_by_jsonpath(candidate, payload)
            if not _is_answer(value):
                continue
            if rule is None:
                # No rule (or a legacy config): the value is used as-is — except `severity`, whose
                # column is constrained to 1-4. That coercion is the old `_coerce_severity`, kept so an
                # unconfigured feed behaves exactly as it did before this module grew a schema.
                if target == "severity":
                    coerced = _severity_from_int(value)
                    if coerced is None:
                        continue
                    value = coerced
                found = value
                break
            transformed = _apply_transform(rule, value, config.value_maps.get(target))
            if transformed is None:
                warnings.append(
                    FieldWarning(
                        target,
                        "unknown_value" if rule.transform == "severity_name" else "bad_type",
                        rule.paths or (candidate,),
                        value,
                    )
                )
                continue
            found = transformed
            break

        if found is None and rule is not None:
            # A rule *was* configured for this target and nothing answered it. That is worth saying:
            # the operator expected a value here, so the alert's fallback is a tolerance to record.
            # (`severity` is reported by the block below instead, which knows the default it used.)
            if target != "severity":
                warnings.append(FieldWarning(target, "not_found", rule.paths))
            if rule.has_default:
                found = rule.default
        if found is not None:
            values[target] = found

    # `severity` is the one target whose absence is *always* reported: it decides triage order, so a
    # silent fallback is how a Medium alert hides a Critical. The column is an int 1-4, and a value
    # that is not one (a typo'd transform returning a string, a rule matching the wrong field) is
    # defaulted and reported rather than passed to the model to raise.
    severity_rule = config.rules.get("severity")
    severity = values.get("severity")
    if isinstance(severity, bool) or not isinstance(severity, int):
        warnings.append(
            FieldWarning(
                "severity",
                "not_found" if severity is None else "bad_type",
                tuple(severity_rule.paths) if severity_rule else (),
                severity,
            )
        )
        severity = None
    elif not 1 <= severity <= 4:
        warnings.append(FieldWarning("severity", "bad_type", (), severity))
        severity = None
    if severity is None:
        severity = default_severity

    return AlertDraft(
        title=values.get("title"),
        description=values.get("description"),
        summary=values.get("summary"),
        severity=severity,
        date=values.get("date") if isinstance(values.get("date"), datetime) else None,
        type=values.get("type"),
        source_ref=values.get("source_ref"),
        warnings=tuple(_as_text_warning(field, default_severity) for field in warnings),
        field_warnings=tuple(warnings),
    )


def _as_text_warning(field: FieldWarning, default_severity: int) -> str:
    """The human string for one field warning.

    `severity_defaulted:` is not decoration: it is the prefix the pipeline has always used for this
    tolerance (`ingest/pipeline.py`) and that the UI's warning list and the pipeline tests key on, so
    the engine's reason is appended to it rather than replacing it.
    """
    if field.target == "severity" and field.reason == "not_found":
        tried = ", ".join(field.paths) if field.paths else "no severity mapping"
        return f"severity_defaulted: {tried}; defaulted to {default_severity}"
    return field.as_text()


# --- Config validation (used by the Settings form) --------------------------


def config_shape(mapping_config: Any) -> str:
    """Which of the two shapes `mapping_config` is: `"empty"`, `"legacy"` or `"v2"`.

    The editor needs this for the one sentence it must not get wrong: saving a legacy config through
    the table **converts** it, and conversion is not free. In the legacy shape the literal payload keys
    are a fallback *after* the configured paths (`LEGACY_KEYS`); in v2 a rule's `paths` are the whole
    candidate list, so once the table writes `fields.title.paths`, a payload whose `title`/`summary`
    keys used to answer `title` no longer does.

    The test is deliberately the same one `_normalize` makes, so the answer describes the reader rather
    than a second opinion about it.
    """
    if not isinstance(mapping_config, dict) or not mapping_config:
        return "empty"
    if mapping_config.get("version") == 2 or isinstance(mapping_config.get("fields"), dict):
        return "v2"
    return "legacy"


def read_rules(mapping_config: Any) -> dict[str, dict[str, Any]]:
    """The rules a config carries, in v2 shape — for display, never for ingest.

    Returns `{target: {"paths": [...], "transform": ..., "default": ...}}`, with `default` present
    only when one was configured (so an editor cannot mistake "unset" for "defaults to null").
    A legacy config reads as its v2 equivalent (`transform` is then the literal `"str"`).

    This exists so Settings can show a config — and P3 can pre-fill its table — without
    re-implementing the v1/v2 shapes: `_normalize` is the single reader, and a second one is exactly
    the fork it prevents. Never raises: a corrupt config must still be openable and fixable.
    """
    config = _normalize(mapping_config)
    rules: dict[str, dict[str, Any]] = {
        target: {
            "paths": list(rule.paths),
            "transform": rule.transform,
            **({"default": rule.default} if rule.has_default else {}),
        }
        for target, rule in config.rules.items()
    }
    for target, paths in config.legacy_paths.items():
        rules.setdefault(target, {"paths": list(paths), "transform": "str"})
    return rules


def validate_config(mapping_config: Any) -> list[str]:
    """Return human-readable errors for `mapping_config`, or `[]` when it is acceptable.

    Strict where `apply_mapping` is lenient, and for the same reason in reverse: an alert must never
    fail because a rule is wrong, but a rule must not be *saved* wrong. Unknown target, unknown
    transform, a path jsonpath-ng will not compile, inverted score bands and oversize lists are all
    refused here so the form can point at the field.
    """
    errors: list[str] = []
    if mapping_config in (None, {}):
        return errors
    if not isinstance(mapping_config, dict):
        return ["mapping_config must be a JSON object"]

    fields = mapping_config.get("fields")
    # The branch is `_normalize`'s branch. Branching on `fields is None` alone would validate
    # `{"version": 2}` as *legacy* while `_normalize` reads it as v2 (no rules) — a config that passes
    # validation and then does something else.
    if mapping_config.get("version") != 2 and not isinstance(fields, dict):
        # Legacy shape: every value must be a path we can compile.
        for key, value in mapping_config.items():
            if isinstance(value, str) and value.strip():
                if value.strip()[:1] != "$":
                    errors.append(f"{key}: a JSONPath starts with $. (got {value!r})")
                elif _parse is not None and extract_by_jsonpath is not None:
                    try:
                        _parse(value.strip())
                    except Exception:
                        errors.append(f"{key}: {value!r} is not a valid JSONPath")
        return errors

    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        return ["fields must be a JSON object"]
    if len(fields) > MAX_FIELDS:
        errors.append(f"at most {MAX_FIELDS} targets may be mapped (got {len(fields)})")

    for target, raw in fields.items():
        where = f"fields.{target}"
        if target not in TARGETS:
            errors.append(f"{where}: unknown target (allowed: {', '.join(TARGETS)})")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{where}: a rule must be a JSON object")
            continue
        paths = raw.get("paths")
        if paths is not None and not isinstance(paths, list):
            errors.append(f"{where}.paths: must be a list")
            continue
        paths = paths or []
        if len(paths) > MAX_PATHS_PER_FIELD:
            errors.append(f"{where}.paths: at most {MAX_PATHS_PER_FIELD} candidates")
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                errors.append(f"{where}.paths: every entry must be a non-empty string")
                continue
            if len(path.strip()) > MAX_PATH_LEN:
                errors.append(f"{where}.paths: {path[:20]!r}… exceeds {MAX_PATH_LEN} characters")
            elif path.strip()[:1] != "$":
                errors.append(f"{where}.paths: {path!r} must start with $")
            elif _parse is not None:
                try:
                    _parse(path.strip())
                except Exception:
                    errors.append(f"{where}.paths: {path!r} is not a valid JSONPath")
        transform = raw.get("transform", "str")
        if transform not in TRANSFORMS:
            errors.append(
                f"{where}.transform: unknown transform {transform!r} "
                f"(allowed: {', '.join(sorted(TRANSFORMS))})"
            )
        max_length = raw.get("max_length")
        if max_length is not None and (not isinstance(max_length, int) or max_length < 1):
            errors.append(f"{where}.max_length: must be a positive integer")
    value_maps = mapping_config.get("value_maps")
    if value_maps is not None and not isinstance(value_maps, dict):
        errors.append("value_maps must be a JSON object")
    else:
        severity_maps = (value_maps or {}).get("severity")
        if severity_maps is not None and not isinstance(severity_maps, dict):
            errors.append("value_maps.severity must be a JSON object")
        elif isinstance(severity_maps, dict):
            for name, level in severity_maps.items():
                if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 4:
                    errors.append(f"value_maps.severity: {name!r} must map to 1-4 (got {level!r})")
    return errors


# --- correlation_key (unchanged behaviour, kept here) -----------------------


def extract_correlation_key(mapping_config: dict[str, Any] | None, payload: Any) -> str:
    """Extract correlation_key from payload using mapping_config.

    Looks for a rule in mapping_config that defines how to derive correlation_key.
    Common keys: "correlation_key", "correlation", "correlationKey",
    "correlationKeyPath", "correlation_key_path".
    """
    if not mapping_config:
        return ""

    # The first spelling that yields a value wins, so a source configured with both a legacy and a
    # current key still correlates. Order is newest-convention-first.
    for key in (
        "correlation_key",
        "correlationKey",
        "correlation_key_path",
        "correlationKeyPath",
        "correlation_id",
        "correlationId",
        "correlation",
    ):
        path = mapping_config.get(key)
        if isinstance(path, str) and path.strip():
            result = extract_by_jsonpath(path.strip(), payload)
            if result is not None:
                return result.strip() if isinstance(result, str) else str(result)

    return ""
