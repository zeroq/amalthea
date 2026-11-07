"""Observable rules in a feed's mapping config — pure schema and extraction.

An observable rule is intentionally smaller than a field rule: it has a type and a list of JSONPaths,
and **every** matching scalar becomes a candidate. Field rules stop at the first answer because a
title is singular; a recipient list is not. Keeping this reader pure means the same rules can back
ingest, the re-map pass and the source editor without an ORM or request in sight.

The module is lenient when reading (a bad rule is skipped, never fatal) and strict in
`validate_observables` (a bad rule must not be saved). That split mirrors `ingest.mapping`: the
webhook must preserve the alert even when an operator's rule is wrong, while the editor should refuse
to store it in the first place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from ingest.mapping import MAX_PATH_LEN, extract_all_by_jsonpath

try:
    from jsonpath_ng import parse as _parse  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - hard dependency; mirrors ingest.mapping
    _parse = None

MAX_OBSERVABLE_RULES: Final[int] = 32
MAX_OBSERVABLE_PATHS: Final[int] = 8
MAX_OBSERVABLE_CANDIDATES: Final[int] = 200
MAX_OBSERVABLE_VALUE_CHARS: Final[int] = 2048
MAX_OBSERVABLE_TYPE_CHARS: Final[int] = 100


@dataclass(frozen=True)
class ObservableRule:
    """One configured type and the paths that feed it."""

    type_name: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ObservableCandidate:
    """One scalar value a rule selected, before normalization or database lookup."""

    type_name: str
    raw_value: str
    source_path: str


@dataclass(frozen=True)
class ObservableExtraction:
    """The bounded result of reading one payload against one config.

    `truncated` is deliberately broader than "the candidate cap was hit": an oversized rule list, an
    overlong path value, or a rule that fanned out past the cap all mean the operator's config was not
    fully honoured, and all of them deserve the same warning on the alert.
    """

    candidates: tuple[ObservableCandidate, ...] = ()
    truncated: bool = False
    skipped_values: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


def _read_rules(config: Any) -> tuple[tuple[ObservableRule, ...], bool]:
    """Leniently read rules plus whether any structural cap or malformed entry shortened them."""
    if not isinstance(config, dict):
        return (), False
    raw_rules = config.get("observables")
    if not isinstance(raw_rules, list) or not raw_rules:
        return (), False

    limited = len(raw_rules) > MAX_OBSERVABLE_RULES
    rules: list[ObservableRule] = []
    for raw in raw_rules[:MAX_OBSERVABLE_RULES]:
        if not isinstance(raw, dict):
            limited = True
            continue
        raw_type = raw.get("type")
        if not isinstance(raw_type, str) or not raw_type.strip():
            limited = True
            continue
        type_name = raw_type.strip()[:MAX_OBSERVABLE_TYPE_CHARS]
        raw_paths = raw.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            limited = True
            continue
        if len(raw_paths) > MAX_OBSERVABLE_PATHS:
            limited = True
        paths: list[str] = []
        for raw_path in raw_paths[:MAX_OBSERVABLE_PATHS]:
            if not isinstance(raw_path, str):
                limited = True
                continue
            path = raw_path.strip()
            if not path or len(path) > MAX_PATH_LEN or not path.startswith("$"):
                limited = True
                continue
            if path not in paths:
                paths.append(path)
        if not paths:
            limited = True
            continue
        rules.append(ObservableRule(type_name=type_name, paths=tuple(paths)))
    return tuple(rules), limited


def observable_rules(config: Any) -> tuple[ObservableRule, ...]:
    """The usable observable rules in `config`; never raises."""
    return _read_rules(config)[0]


def validate_observables(config: Any) -> list[str]:
    """Strict save-time validation for the optional `observables` section.

    Unknown type names are accepted: this function has no database access, and the type vocabulary is
    analyst-editable. Ingest skips an unknown type with a warning; the editor offers the real
    vocabulary, so a normal UI save cannot create one.
    """
    if not isinstance(config, dict) or "observables" not in config:
        return []
    raw_rules = config.get("observables")
    if not isinstance(raw_rules, list):
        return ["observables must be a JSON array"]
    errors: list[str] = []
    if len(raw_rules) > MAX_OBSERVABLE_RULES:
        errors.append(
            f"observables: at most {MAX_OBSERVABLE_RULES} rules may be configured "
            f"(got {len(raw_rules)})"
        )
    for index, raw in enumerate(raw_rules[:MAX_OBSERVABLE_RULES]):
        where = f"observables[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: a rule must be a JSON object")
            continue
        type_name = raw.get("type")
        if not isinstance(type_name, str) or not type_name.strip():
            errors.append(f"{where}.type: must be a non-empty string")
        elif len(type_name.strip()) > MAX_OBSERVABLE_TYPE_CHARS:
            errors.append(
                f"{where}.type: at most {MAX_OBSERVABLE_TYPE_CHARS} characters "
                f"(got {len(type_name.strip())})"
            )
        paths = raw.get("paths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"{where}.paths: must be a non-empty JSON array")
            continue
        if len(paths) > MAX_OBSERVABLE_PATHS:
            errors.append(
                f"{where}.paths: at most {MAX_OBSERVABLE_PATHS} paths per rule (got {len(paths)})"
            )
        for path_index, path in enumerate(paths[:MAX_OBSERVABLE_PATHS]):
            path_where = f"{where}.paths[{path_index}]"
            if not isinstance(path, str) or not path.strip():
                errors.append(f"{path_where}: must be a non-empty string")
                continue
            value = path.strip()
            if len(value) > MAX_PATH_LEN:
                errors.append(f"{path_where}: exceeds {MAX_PATH_LEN} characters")
            elif not value.startswith("$"):
                errors.append(f"{path_where}: a JSONPath starts with $. (got {value!r})")
            elif _parse is not None:
                try:
                    _parse(value)
                except Exception:
                    errors.append(f"{path_where}: {value!r} is not a valid JSONPath")
    return errors


def _as_candidate_value(value: Any) -> tuple[str | None, bool]:
    """Stringify one scalar and report whether it was rejected for length."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        if not text:
            return None, False
        if len(text) > MAX_OBSERVABLE_VALUE_CHARS:
            return None, True
        return text, False
    return None, False


def extract_observable_candidates(config: Any, payload: Any) -> ObservableExtraction:
    """Read every scalar a config's observable rules select, with all caps applied.

    Deterministic by construction: rules are read in config order, paths in listed order, matches in
    JSON document order. Duplicate `(type, raw value)` pairs are collapsed before the candidate cap,
    so a path repeated across two rules cannot consume the budget twice.
    """
    rules, limited = _read_rules(config)
    if not rules:
        return ObservableExtraction(truncated=limited)

    candidates: list[ObservableCandidate] = []
    seen: set[tuple[str, str]] = set()
    skipped = 0
    truncated = limited

    for rule in rules:
        for path in rule.paths:
            for value in extract_all_by_jsonpath(path, payload):
                raw, rejected = _as_candidate_value(value)
                if rejected:
                    skipped += 1
                    truncated = True
                    continue
                if raw is None:
                    continue
                key = (rule.type_name, raw)
                if key in seen:
                    continue
                if len(candidates) >= MAX_OBSERVABLE_CANDIDATES:
                    truncated = True
                    return ObservableExtraction(
                        candidates=tuple(candidates),
                        truncated=True,
                        skipped_values=skipped,
                        notes=_truncation_notes(),
                    )
                seen.add(key)
                candidates.append(
                    ObservableCandidate(
                        type_name=rule.type_name,
                        raw_value=raw,
                        source_path=path,
                    )
                )

    notes = _truncation_notes() if truncated else ()
    return ObservableExtraction(
        candidates=tuple(candidates),
        truncated=truncated,
        skipped_values=skipped,
        notes=notes,
    )


def _truncation_notes() -> tuple[str, ...]:
    return (
        f"observables_truncated: observable extraction is capped at "
        f"{MAX_OBSERVABLE_CANDIDATES} candidates and {MAX_OBSERVABLE_RULES} rules; "
        "some configured values were not linked",
    )
