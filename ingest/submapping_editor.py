"""Structured editor helpers for v2 `submappings` on one ingestion source.

The mapping engine owns the config language (`ingest.mapping.validate_config`); this module owns only
the *editing* operations a source-detail tab performs on that language. It deliberately mirrors
`ingest/editor.py` and `ingest/observable_editor.py`: pure functions of a stored config plus submitted
strings, returning an `EditorResult` or a whole candidate config. No request, session or ORM object is
touched here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ingest.editor import EditorResult, build_table_config
from ingest.mapping import MAX_SUBMAPPING_KEY_LEN, MAX_SUBMAPPINGS, config_shape, validate_config
from ingest.observable_editor import build_observable_config


def _as_config(config: Any) -> dict[str, Any]:
    return dict(config) if isinstance(config, dict) else {}


def normalize_key(key_text: str | None) -> str:
    """Trim surrounding whitespace; raw `type` matching stays exact and case-sensitive."""
    return key_text.strip() if isinstance(key_text, str) else ""


def submapping_config(config: Any, key: str) -> dict[str, Any] | None:
    """Return a copy of one submapping value, or `None` when the key is absent/invalid."""
    if not isinstance(config, dict):
        return None
    submappings = config.get("submappings")
    if not isinstance(submappings, dict):
        return None
    value = submappings.get(key)
    return dict(value) if isinstance(value, dict) else None


def submapping_keys(config: Any) -> list[str]:
    """Sorted list of configured submapping keys for the UI list."""
    if not isinstance(config, dict):
        return []
    submappings = config.get("submappings")
    if not isinstance(submappings, dict):
        return []
    return sorted(key for key in submappings if isinstance(key, str))


def submapping_editor_rows(
    config: Any,
    key: str,
    *,
    submitted: Mapping[str, Mapping[str, str]] | None = None,
    errors: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """Field-rule rows for one submapping, reusing the master table's row builder."""
    from ingest.editor import editor_rows

    return editor_rows(submapping_config(config, key) or {}, submitted=submitted, errors=errors)


def _legacy_error(config: Any) -> str | None:
    shape = config_shape(config)
    if shape == "legacy":
        return (
            "This feed still stores a legacy flat mapping. Save the master mapping as v2 first; "
            "submappings cannot be added to a legacy config without changing its fallback behaviour."
        )
    return None


def _validate_new_key(config: Any, key: str) -> tuple[str, ...]:
    errors: list[str] = []
    if not key:
        errors.append("Enter the raw top-level `type` value to use as the submapping key.")
    if len(key) > MAX_SUBMAPPING_KEY_LEN:
        errors.append(
            f"Submapping key is too long ({len(key)} characters; maximum {MAX_SUBMAPPING_KEY_LEN})."
        )
    legacy = _legacy_error(config)
    if legacy:
        errors.append(legacy)
    submappings = config.get("submappings") if isinstance(config, dict) else None
    if isinstance(submappings, dict):
        if key and key in submappings:
            errors.append(f"A submapping for raw type `{key}` already exists.")
        if key and len(submappings) >= MAX_SUBMAPPINGS:
            errors.append(
                f"At most {MAX_SUBMAPPINGS} submappings may be configured; delete one first."
            )
    return tuple(errors)


def _validate_candidate(candidate: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    failures = validate_config(candidate)
    return {"": tuple(failures)} if failures else {}


def build_submapping_add(config: Any, key_text: str | None) -> EditorResult:
    """Add an empty v2 submapping under a trimmed raw-type key."""
    before = _as_config(config)
    key = normalize_key(key_text)
    errors = _validate_new_key(before, key)
    if errors:
        return EditorResult(errors={"submapping_key": errors})

    candidate = _as_config(before)
    if not candidate:
        candidate = {"version": 2, "fields": {}}
    existing = candidate.get("submappings")
    submappings = dict(existing) if isinstance(existing, dict) else {}
    submappings[key] = {"version": 2, "fields": {}}
    candidate["submappings"] = submappings
    failures = _validate_candidate(candidate)
    if failures:
        return EditorResult(errors=failures)
    return EditorResult(config=candidate)


def build_submapping_delete(config: Any, key: str) -> EditorResult:
    """Delete one submapping, preserving the master config and every sibling."""
    before = _as_config(config)
    submappings = before.get("submappings")
    if not isinstance(submappings, dict) or key not in submappings:
        return EditorResult(errors={"submapping_key": (f"No submapping for raw type `{key}`.",)})

    candidate = _as_config(before)
    remaining = dict(submappings)
    remaining.pop(key)
    if remaining:
        candidate["submappings"] = remaining
    else:
        candidate.pop("submappings", None)
    failures = _validate_candidate(candidate)
    if failures:
        return EditorResult(errors=failures)
    return EditorResult(config=candidate)


def build_submapping_config(
    config: Any,
    key: str,
    field_rows: Mapping[str, Mapping[str, str]],
    observable_rows: Mapping[str, str],
    types: Sequence[Any],
) -> EditorResult:
    """Save the selected submapping's field table and observable card as one candidate config."""
    before = _as_config(config)
    submapping = submapping_config(before, key)
    if submapping is None:
        return EditorResult(errors={"submapping_key": (f"No submapping for raw type `{key}`.",)})

    field_result = build_table_config(submapping, field_rows)
    errors: dict[str, list[str]] = {
        name: list(messages) for name, messages in field_result.errors.items()
    }
    if not field_result.ok:
        return EditorResult(errors={name: tuple(messages) for name, messages in errors.items()})

    field_config = field_result.config if field_result.config is not None else submapping
    # A submapping value is a complete v2 config even when its table is empty. `build_table_config`
    # deliberately returns `{}` for an empty table, so restore the shape before handing it on.
    submapping_base = {"version": 2, "fields": {}, **field_config}
    submapping_base["version"] = 2
    observable_result = build_observable_config(submapping_base, observable_rows, types)
    for name, messages in observable_result.errors.items():
        errors.setdefault(name, []).extend(messages)
    if not observable_result.ok:
        return EditorResult(errors={name: tuple(messages) for name, messages in errors.items()})

    saved_submapping = dict(observable_result.config or submapping_base)
    saved_submapping.setdefault("version", 2)
    saved_submapping.setdefault("fields", {})
    candidate = _as_config(before)
    submappings = dict(before.get("submappings") or {})
    submappings[key] = saved_submapping
    candidate["submappings"] = submappings
    failures = _validate_candidate(candidate)
    if failures:
        return EditorResult(errors=failures)
    return EditorResult(config=candidate)
