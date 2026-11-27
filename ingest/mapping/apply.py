"""Pure application of a mapping config to a payload."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from .config import LEGACY_KEYS, TARGETS, _normalize, _Rule
from .jsonpath import extract_by_jsonpath
from .values import (
    TRANSFORMS,
    _as_text,
    _severity_from_int,
    _severity_from_name,
    _severity_from_score,
)


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


#: The reasons that mean "the rule produced **no** value", as opposed to `truncated`, which means it
#: produced one and shortened it. One set, because two callers count exactly these: the re-map's
#: result message ("severity produced no value on 19 of 19 alerts") and the alert page's badge.
#:
#: `not_found` is the honest "nothing at those paths"; `bad_type`/`unknown_value`/`bad_transform` are
#: "the value was there but the transform could not read it" — the difference an operator needs, because
#: only the second group usually means the *transform* is wrong (`severity` given a 0-100 score with no
#: `severity_score` transform is the one this distinction was added for).
UNRESOLVED_REASONS: Final[frozenset[str]] = frozenset(
    {"not_found", "unknown_value", "bad_type", "bad_transform"}
)

#: Wording per reason, for the UI. The engine keeps its tokens (they are stored and asserted); the
#: sentence belongs to presentation, and every surface that renders a `mapping` entry wants the same one.
REASON_LABELS: Final[dict[str, str]] = {
    "not_found": "nothing at the configured path",
    "unknown_value": "a value the transform does not recognise",
    "bad_type": "a value of the wrong type",
    "bad_transform": "a transform that could not run",
    "truncated": "a value shortened to fit the column",
}


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
