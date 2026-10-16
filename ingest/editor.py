"""Editing a source's `mapping_config` — the pure half of the Settings editor (plan §7, P3).

**Why this is a module and not a Django `Form`.** The house style is a hand-parsed POST with per-field
errors echoed back into the template (`ui/views.py`), and the repo contains no `Form`/`ModelForm` at
all; introducing the first one here would make this page the only one that behaves differently on a
validation failure. Recorded as deviation **MAP-5**.

**Why it is not in the view.** Everything below is a pure function of strings and dicts: the POST is
`{f"paths__{target}": …, "transform__{target}": …, "default__{target}": …}` and the output is either the
config to store or the reasons not to. That is what lets the table be tested against the *engine*'s own
schema (`ingest.mapping.validate_config`) without a request, a session or a database.

**Four rules the table must obey, and why:**

1. **The table owns only `version` and `fields`.** Every other top-level key survives a table save
   verbatim — `value_maps` (edited only in the advanced JSON box, per plan Q3), `correlation_key` (its
   own reader, `extract_correlation_key`) and `on_missing`. A form that quietly deletes a key it does
   not render is a data-loss bug with a Save button.
2. **A path list is newline-separated, and only newline-separated** (**MAP-6**): a JSONPath may contain
   a comma (`$['a','b']` union, `$.x[1,2]`, a filter's `?(@.n == 'a,b')`), so a comma separator would
   silently split valid paths.
3. **An empty row means "no rule"**, which is a real, documented setting: the payload's own literal key
   is then read (`LEGACY_KEYS`), exactly as an unconfigured feed behaves. That is how an operator
   deletes a rule — clearing its paths — rather than with a separate delete control. A preserved
   `bands`/`max_length` cannot keep a rule alive on its own: a paths-less rule without a default
   answers nothing *and* stops the literal fallback, so keeping one would make "clear the row" mean
   "silently stop mapping this target" (`is_noop_save` is measured against this same fixed point).

4. **A conversion is announced, never silent.** Saving a legacy config through the table rewrites it as
   v2, and in v2 a rule's paths are the whole candidate list (`ingest.mapping.config_shape`); the view
   shows that consequence in words before the operator saves.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from ingest.mapping import (
    LEGACY_KEYS,
    TARGETS,
    TRANSFORMS,
    config_shape,
    read_rules,
    validate_config,
)

#: Top-level keys the table is responsible for. Everything else is preserved verbatim (rule 1).
TABLE_MANAGED_TOP_LEVEL: Final[frozenset[str]] = frozenset({"version", "fields"})

#: Rule keys the table writes. Anything else inside a rule (`bands`, `max_length`, …) is preserved.
MANAGED_RULE_KEYS: Final[frozenset[str]] = frozenset(
    {"paths", "transform", "default", "severity_default"}
)

#: The legacy top-level spellings the table *consumes* when it converts a config. Deliberately the
#: `LEGACY_KEYS` fallback list — those are exactly the keys `_normalize`'s legacy branch reads — and
#: deliberately *not* "every target name", so `correlation_key` (not a target) and any unimplemented
#: spelling survive the conversion untouched.
LEGACY_FIELD_SPELLINGS: Final[frozenset[str]] = frozenset(
    key for keys in LEGACY_KEYS.values() for key in keys
)

#: `transform` when the row does not choose one. Equal to the engine's own default, which is why the
#: table omits the key rather than writing it: `{"paths": [...]}` and `{"paths": [...],
#: "transform": "str"}` are the same rule, and a save that does not change behaviour should not change
#: a byte (AC12).
DEFAULT_TRANSFORM: Final[str] = "str"

TARGET_LABELS: Final[dict[str, str]] = {
    "title": "Title",
    "description": "Description",
    "summary": "Summary",
    "severity": "Severity",
    "date": "Date",
    "type": "Type",
    "source_ref": "Source reference",
}

#: One line per transform, for the editor's help text. Mirrors the plan §5.2 table; the closed set
#: itself is `ingest.mapping.TRANSFORMS` and is never re-declared here.
TRANSFORM_HELP: Final[dict[str, str]] = {
    "str": "Text, whitespace-collapsed, truncated to max_length.",
    "str_lower": "As str, lower-cased.",
    "int": 'An integer (`"3"` → `3`).',
    "severity_name": '`"Critical"` → `4`, by name, case-insensitively.',
    "severity_score": "A 0-100 score → 1-4 by bands (below 20 is 1).",
    "severity_int": "1-4 as-is.",
    "datetime": "RFC 3339/ISO 8601 or epoch seconds/milliseconds → a timestamp.",
    "list": "Any value → a list of strings.",
    "json": "The value verbatim (objects and arrays kept).",
    "tlp": '`"Amber"` or `2` → 0-4.',
    "pap": '`"Amber"` or `2` → 0-3.',
}


@dataclass(frozen=True)
class EditorResult:
    """One save attempt: the config to store, or the reasons not to.

    `config` is `None` exactly when `errors` is non-empty, so a caller cannot half-apply a rejected
    submission. `errors` is keyed by target, with `""` for a message about the config as a whole;
    `ingest.mapping.validate_config`'s `fields.<target>.…` messages are attributed to their row by
    `_error_target`, which keeps one schema authority instead of a second, form-shaped one.
    """

    config: dict[str, Any] | None = None
    errors: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


# --- Shared helpers ---------------------------------------------------------


def parse_paths(text: str | None) -> list[str]:
    """The paths a `<textarea>` holds: one per line, blanks dropped, duplicates collapsed.

    Newline only (**MAP-6**). Duplicates are dropped because a repeated candidate can never produce a
    different value (the engine walks the list in order, and the second copy answers exactly as the
    first), and keeping them would only make the stored config harder to read.
    """
    if not text:
        return []
    paths: list[str] = []
    for line in text.splitlines():
        path = line.strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def canonical(config: Any) -> str:
    """A stable comparison key for two configs — `sort_keys`, so key order is not a change.

    Used for AC12 ("submitting what is already stored writes nothing"). Values that `json` cannot
    encode cannot occur (the column is a `JSONField`), but the `repr` fallback keeps this total rather
    than making an unencodable config a 500.
    """
    try:
        return json.dumps(config, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - JSONField round-trips are JSON-encodable
        return repr(config)


def is_unchanged(before: Any, after: Any) -> bool:
    """True when storing `after` over `before` would change nothing."""
    return canonical(before) == canonical(after)


def is_noop_save(stored: Any, candidate: Any) -> bool:
    """AC12: would storing `candidate` over `stored` change anything the operator was shown?

    Not `is_unchanged(stored, candidate)`, and the two differences are the point:

    * the table writes the *canonical* rule, dropping a `transform: "str"` the engine defaults to
      anyway. A stored config that spells `"str"` out under every field (a plausible hand-edit, and
      what the JSON box invites) would otherwise look "changed" on every single save, which would make
      AC12's promise false for exactly the configs the page displays the most. The comparison is
      therefore against `canonical_form(stored)` — the rows the operator was actually shown.
    * a **legacy** config is not normalised here on purpose: converting it *is* a change (its literal
      keys stop being fallbacks once a rule names the target), so it must not be reported as "nothing
      to save" just because the operator left every row alone.
    """
    if config_shape(stored) == "legacy":
        return is_unchanged(stored, candidate)
    return is_unchanged(canonical_form(stored), candidate)


def submitted_rows(mapping_config: Any) -> dict[str, dict[str, str]]:
    """What the page posts when the operator saves without editing anything.

    The exact shape `ui/views.py::_source_mapping_rows` builds from a POST — `editor_rows` minus the
    fields that only exist to be displayed (`label`, `literal_keys`).
    """
    return {
        row["target"]: {
            "paths": row["paths"],
            "transform": row["transform"],
            "default": row["default"],
        }
        for row in editor_rows(mapping_config)
    }


def canonical_form(mapping_config: Any) -> Any:
    """The config the table stores when every row is saved exactly as displayed (see `is_noop_save`).

    A config the table cannot represent — anything `validate_config` refuses — is returned unchanged,
    so it can never read as "already stored": the operator goes through the fix-it flow instead.
    """
    result = build_table_config(mapping_config, submitted_rows(mapping_config))
    return mapping_config if result.config is None else result.config


def changed_keys(before: Any, after: Any, prefix: str = "") -> list[str]:
    """The dotted paths whose value differs, for the audit row (`fields.title.paths`, `value_maps`).

    A shape change (a list where there was a string) reports the path itself rather than a diff of its
    inside: the audit records *that* an operator changed a key, and by design never the values
    (`identity/audit.py::record_ingestion_source_updated`). A target that disappears entirely is
    reported as `fields.title` — "the title rule was removed".
    """
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in sorted({*before, *after}):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                changed.append(child)
            else:
                changed.extend(changed_keys(before[key], after[key], child))
        return changed
    return [] if before == after else [prefix or "mapping_config"]


_FIELDS_PREFIX: Final[str] = "fields."


def _error_target(message: str) -> str:
    """The row a `validate_config` message belongs to, or `""` for a whole-config message.

    `validate_config` already prefixes per-rule messages with `fields.<target>.`, and the legacy branch
    with `<key>: `; this reads that prefix back rather than re-deriving the rule. Anything unrecognised
    (a `value_maps` complaint, `fields must be a JSON object`) is shown once, above the table.
    """
    if message.startswith(_FIELDS_PREFIX):
        target = message[len(_FIELDS_PREFIX) :].split(".", 1)[0].split(" ", 1)[0]
        return target if target in TARGETS else ""
    key = message.split(":", 1)[0]
    return key if key in TARGETS else ""


# --- The table view ---------------------------------------------------------


def _preserved(mapping_config: Any) -> dict[str, Any]:
    """The top-level keys a table save must not touch (rule 1)."""
    if not isinstance(mapping_config, dict):
        return {}
    return {
        key: value
        for key, value in mapping_config.items()
        if key not in TABLE_MANAGED_TOP_LEVEL and key not in LEGACY_FIELD_SPELLINGS
    }


def _existing_rule_extras(mapping_config: Any, target: str) -> dict[str, Any]:
    """A target's already-stored keys that the table does not render (`bands`, `max_length`, …).

    Preserved so that opening the table and saving cannot silently drop a hand-tuned score band — the
    same rule 1 that protects `value_maps`, applied one level down.
    """
    if not isinstance(mapping_config, dict):
        return {}
    fields = mapping_config.get("fields")
    if not isinstance(fields, dict):
        return {}
    rule = fields.get(target)
    if not isinstance(rule, dict):
        return {}
    return {key: value for key, value in rule.items() if key not in MANAGED_RULE_KEYS}


def build_table_config(
    existing: Any,
    rows: Mapping[str, Mapping[str, str]],
) -> EditorResult:
    """Turn the submitted table into a v2 config, or into per-row errors.

    `rows` is `{target: {"paths": <textarea>, "transform": <select>, "default": <input>}}`, read by the
    view from the POST. A target with no paths, no default and no preserved extras is **omitted** —
    which is the documented "no rule" state (rule 3), and which is how the table deletes a rule.

    Defaults are validated here rather than by `validate_config`, because the schema has nothing to say
    about them beyond "it is stored verbatim, not transformed": a `severity` default has to be an int
    1-4 (`apply_mapping` drops anything else back to `default_severity`), and a `date` default can
    never become a `datetime`, so setting one is an intent that cannot be honoured. Everything else
    about the candidate — `paths` shape, the closed transform set, `max_length`, `value_maps` — is
    `validate_config`'s, unchanged.
    """
    base = existing if isinstance(existing, dict) else {}
    preserved = _preserved(base)
    errors: dict[str, list[str]] = {}
    fields: dict[str, Any] = {}

    for target in TARGETS:
        row = rows.get(target) or {}
        paths = parse_paths(row.get("paths"))
        transform = (row.get("transform") or "").strip() or DEFAULT_TRANSFORM
        default_text = (row.get("default") or "").strip()
        explicit_transform = transform != DEFAULT_TRANSFORM

        row_errors: list[str] = []
        rule: dict[str, Any] = {}
        if paths:
            rule["paths"] = paths
        # The engine's default is not written out (see DEFAULT_TRANSFORM), so a save that changes no
        # behaviour changes no bytes.
        if explicit_transform:
            rule["transform"] = transform
        if default_text:
            row_errors.extend(_apply_default(rule, target, default_text))

        if not paths and "default" not in rule:
            # Nothing on this row that could ever answer the target: there is no rule, so the payload's
            # own literal key does (rule 3). Note what this deliberately excludes — a preserved `bands`
            # or `max_length` from the stored rule cannot resurrect a rule on its own, because a rule
            # with no paths and no default answers *nothing* while still shadowing the literal
            # fallback, which is the opposite of what clearing the row means.
            if explicit_transform:
                # A transform with nothing to read is a row the operator meant to finish.
                row_errors.append(
                    f"No paths: {transform} has nothing to read from the payload. "
                    "Add at least one path, or leave the row empty for the literal-key fallback."
                )
            if row_errors:
                errors.setdefault(target, []).extend(row_errors)
            continue

        rule.update(_existing_rule_extras(base, target))
        fields[target] = rule
        if row_errors:
            errors.setdefault(target, []).extend(row_errors)

    # An empty table over an empty config stays empty: `{"version": 2, "fields": {}}` would be a write
    # that stores "the editor was here" and nothing else (AC12's spirit).
    if not fields and not preserved:
        candidate: dict[str, Any] = {}
    else:
        candidate = {"version": 2, "fields": fields, **preserved}

    for message in validate_config(candidate):
        errors.setdefault(_error_target(message), []).append(message)

    if errors:
        return EditorResult(errors={key: tuple(value) for key, value in errors.items()})
    return EditorResult(config=candidate)


def _apply_default(rule: dict[str, Any], target: str, text: str) -> list[str]:
    """Validate one row's `default` and, when it is usable, put it on `rule`."""
    if target == "severity":
        try:
            level = int(text)
        except ValueError:
            level = None
        if level is None or not 1 <= level <= 4:
            # Not "1-4 or nothing": a name (`"High"`) cannot work either, because a default is stored
            # verbatim and never passed through the transform.
            return ["Severity default must be a whole number 1-4 (a name is not transformed)."]
        rule["default"] = level
        return []
    if target == "date":
        return [
            "A date default cannot work: only a path's value becomes a timestamp. "
            "Leave it empty (the alert keeps its ingest time)."
        ]
    rule["default"] = text
    return []


def parse_json_config(text: str | None) -> EditorResult:
    """Read the advanced JSON box.

    An *empty* box is an error rather than "clear the mapping": `{}` is how an operator clears it, and
    an empty textarea is far more likely to be a submit that lost its content (a paste that did not
    land, a browser that dropped it) than an intent to delete every rule. `validate_config` then
    applies the same schema as the table.
    """
    raw = (text or "").strip()
    if not raw:
        return EditorResult(
            errors={"": ("Paste a JSON object — or {} to clear the mapping.",)},
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return EditorResult(
            errors={
                "": (f"Not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).",),
            }
        )
    if not isinstance(parsed, dict):
        return EditorResult(errors={"": ("The mapping must be a JSON object.",)})

    failures = validate_config(parsed)
    if failures:
        by_target: dict[str, list[str]] = {}
        for message in failures:
            by_target.setdefault(_error_target(message), []).append(message)
        return EditorResult(errors={key: tuple(value) for key, value in by_target.items()})
    return EditorResult(config=parsed)


# --- Rendering the table ----------------------------------------------------


def editor_rows(
    mapping_config: Any,
    *,
    submitted: Mapping[str, Mapping[str, str]] | None = None,
    errors: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """The table's rows, pre-filled from a config (GET) or from what was submitted (error re-render).

    `submitted` wins for any target it carries, so a rejected save shows the operator their own input
    rather than the stored config — the whole point of echoing a form back. A target it does *not*
    carry keeps its stored row, so a partial submission (a hand-written POST, a form that grows a
    field) cannot blank the rest of the table.
    """
    rules = read_rules(mapping_config)
    rows: list[dict[str, Any]] = []
    for target in TARGETS:
        submitted_row = (submitted or {}).get(target)
        if submitted_row is not None:
            paths_text = submitted_row.get("paths", "")
            transform = submitted_row.get("transform") or DEFAULT_TRANSFORM
            default_text = submitted_row.get("default", "")
        else:
            rule = rules.get(target, {})
            paths_text = "\n".join(rule.get("paths", []))
            transform = rule.get("transform") or DEFAULT_TRANSFORM
            # A stored `default: null` reads as "no default" rather than the string `"None"`; the field
            # is text, so anything else would round-trip a null into a literal.
            stored_default = rule.get("default")
            default_text = "" if stored_default is None else str(stored_default)
        rows.append(
            {
                "target": target,
                "label": TARGET_LABELS.get(target, target),
                "paths": paths_text,
                "transform": transform,
                "default": default_text,
                # What answers this target when the row is left empty — the literal-key fallback.
                "literal_keys": list(LEGACY_KEYS[target]),
                "errors": list((errors or {}).get(target, ())),
            }
        )
    return rows


def transforms_for_editor() -> list[tuple[str, str]]:
    """`(name, help)` for the transform `<select>`, in the engine's own order."""
    return [(name, TRANSFORM_HELP.get(name, "")) for name in TRANSFORMS]
