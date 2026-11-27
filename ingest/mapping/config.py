"""Config language, normalization and validation for the mapping engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from .jsonpath import _parse, extract_by_jsonpath
from .values import DEFAULT_MAX_LENGTH, DEFAULT_SCORE_BANDS, TRANSFORMS

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

MAX_PATH_LEN: Final[int] = 200
MAX_PATHS_PER_FIELD: Final[int] = 8
MAX_FIELDS: Final[int] = 20
MAX_SUBMAPPINGS: Final[int] = 50
MAX_SUBMAPPING_KEY_LEN: Final[int] = 100


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

    # Observables are an optional top-level section in either shape; validate them before the legacy
    # early return so a legacy config with an observable section is held to the same schema.
    from ..observable_rules import validate_observables

    errors.extend(validate_observables(mapping_config))

    # --- Validate submappings (v2-only) ------------------------------------
    submappings = mapping_config.get("submappings")

    if mapping_config.get("version") == 2:
        # v2 config: submappings are v2-only and optional
        if submappings is not None:
            if not isinstance(submappings, dict):
                errors.append("submappings: must be a JSON object")
            else:
                # Max 50 keys
                if len(submappings) > MAX_SUBMAPPINGS:
                    errors.append(
                        f"submappings: at most {MAX_SUBMAPPINGS} keys may be configured "
                        f"(got {len(submappings)})"
                    )
                # Validate each key and value using validate_config recursively
                for key, value in submappings.items():
                    # Key must be non-empty string <= 100 chars
                    if not isinstance(key, str) or not key.strip():
                        errors.append(f"submappings.{key}: key must be a non-empty string")
                    elif len(key) > MAX_SUBMAPPING_KEY_LEN:
                        errors.append(
                            f"submappings.{key}: key exceeds {MAX_SUBMAPPING_KEY_LEN} characters"
                        )
                    else:
                        # Value must be a complete valid v2 config; reject nested submappings
                        if not isinstance(value, dict):
                            errors.append(f"submappings.{key}: value must be a JSON object")
                        elif value.get("version") != 2:
                            errors.append(f"submappings.{key}: value must have version 2")
                        elif value.get("submappings") is not None:
                            # Nested submappings are not supported
                            errors.append(
                                f"submappings.{key}: nested submappings are not supported"
                            )
                        else:
                            # Recursively validate as a v2 config; prefix errors with submapping key
                            nested_errors = validate_config(value)
                            for e in nested_errors:
                                errors.append(f"submappings.{key}.{e}")
    else:
        # Legacy config: submappings are not available
        if submappings is not None:
            errors.append("submappings: only available in v2 mapping configs (version 2)")

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
