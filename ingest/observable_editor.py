"""Pure helpers for the source page's observable-rules card.

The card is deliberately a third writer over the same `mapping_config`, with one concern: the
top-level `observables` list. It must not disturb field rules, `value_maps`, correlation keys or any
future top-level setting, and a JSON-only rule that the card cannot render must survive a card save.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ingest.editor import EditorResult, is_unchanged, parse_paths
from ingest.observable_rules import validate_observables


def _type_name(rule: Any) -> str:
    if not isinstance(rule, Mapping):
        return ""
    value = rule.get("type")
    return value.strip() if isinstance(value, str) else ""


def _path_text(rule: Any) -> str:
    if not isinstance(rule, Mapping):
        return ""
    paths = rule.get("paths")
    if not isinstance(paths, list):
        return ""
    return "\n".join(path for path in paths if isinstance(path, str) and path.strip())


def observable_editor_rows(
    config: Any,
    types: Sequence[Any],
    *,
    submitted: Mapping[str, str] | None = None,
    errors: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """One row per vocabulary type, pre-filled from config or from a rejected submission.

    Existing rules for the same type are unioned in config order. A duplicated type in the advanced
    JSON therefore becomes one editable row without losing either path list, while a type absent from
    the vocabulary is not rendered (and is preserved by `build_observable_config`).
    """
    known = {getattr(obs_type, "name", str(obs_type)): obs_type for obs_type in types}
    existing_paths: dict[str, list[str]] = {name: [] for name in known}
    raw_rules = config.get("observables") if isinstance(config, dict) else None
    if isinstance(raw_rules, list):
        for rule in raw_rules:
            name = _type_name(rule)
            if name not in existing_paths or not isinstance(rule, Mapping):
                continue
            paths = rule.get("paths")
            if not isinstance(paths, list):
                continue
            for path in paths:
                if (
                    isinstance(path, str)
                    and path.strip()
                    and path.strip() not in existing_paths[name]
                ):
                    existing_paths[name].append(path.strip())

    rows: list[dict[str, Any]] = []
    for name in sorted(known):
        if submitted is not None and name in submitted:
            paths_text = submitted.get(name, "")
        else:
            paths_text = "\n".join(existing_paths[name])
        rows.append(
            {
                "type": name,
                "label": name,
                "paths": paths_text,
                "errors": list((errors or {}).get(name, ())),
            }
        )
    return rows


def _row_error(type_name: str, paths: list[str]) -> list[str]:
    """Validate one row through the shared schema, with the row prefix stripped for display."""
    if not paths:
        return []
    errors = validate_observables({"observables": [{"type": type_name, "paths": paths}]})
    prefix = "observables[0]."
    cleaned: list[str] = []
    for message in errors:
        if message.startswith(prefix):
            cleaned.append(message[len(prefix) :])
        elif not message.startswith("observables:"):
            cleaned.append(message)
    return cleaned


def build_observable_config(
    existing: Any,
    rows: Mapping[str, str],
    types: Sequence[Any],
) -> EditorResult:
    """Turn the card's textareas into a config, preserving every key and rule it does not own.

    `rows` is `{type_name: newline-separated paths}`. An empty row removes that type's rule. Rules for
    types outside the current vocabulary are appended unchanged. The card never converts a legacy
    field mapping: adding observables must not change how the existing fields are read.
    """
    known_types = {getattr(obs_type, "name", str(obs_type)) for obs_type in types}
    base = dict(existing) if isinstance(existing, dict) else {}
    raw_rules = base.get("observables")
    old_rules = raw_rules if isinstance(raw_rules, list) else []

    row_rules: dict[str, dict[str, Any]] = {}
    errors: dict[str, list[str]] = {}
    for name in sorted(known_types):
        paths = parse_paths(rows.get(name, ""))
        if not paths:
            continue
        row_errors = _row_error(name, paths)
        if row_errors:
            errors[name] = row_errors
            continue
        row_rules[name] = {"type": name, "paths": paths}

    if errors:
        return EditorResult(errors={key: tuple(value) for key, value in errors.items()})

    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for rule in old_rules:
        name = _type_name(rule)
        if name not in known_types:
            ordered.append(rule)
            continue
        if name in emitted:
            continue
        if name in row_rules:
            ordered.append(row_rules[name])
        emitted.add(name)
    for name in sorted(known_types):
        if name in row_rules and name not in emitted:
            ordered.append(row_rules[name])
            emitted.add(name)

    candidate = dict(base)
    if ordered:
        candidate["observables"] = ordered
    else:
        candidate.pop("observables", None)
    if not base and ordered:
        # An empty config has no shape; storing the rules in the current shape makes them visible to
        # the table/JSON views instead of leaving a legacy-looking object with only a v2 section.
        candidate = {"version": 2, "fields": {}, "observables": ordered}
    config_errors = validate_observables(candidate)
    if config_errors:
        # Row-level paths are validated above; this catches whole-list limits such as MAX_RULES after
        # preserved unknown-type rules are appended.
        return EditorResult(errors={"": tuple(config_errors)})
    return EditorResult(config=candidate)


def observable_save_is_noop(existing: Any, candidate: Any) -> bool:
    """The card's no-op test. Full-equality is right here: unlike the table, it makes no canonical form."""
    return is_unchanged(existing if isinstance(existing, dict) else {}, candidate)
