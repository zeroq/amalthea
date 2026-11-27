"""JSONPath extraction helpers for the mapping engine."""

from __future__ import annotations

from typing import Any

try:
    from jsonpath_ng import parse as _parse  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    # The package is a hard dependency; this branch exists so a missing wheel degrades the mapping
    # to "no mapping" instead of taking the whole ingest app down at import time.
    _parse = None


__all__ = ["_parse", "extract_all_by_jsonpath", "extract_by_jsonpath"]


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


def _flatten_match_values(value: Any, out: list[Any]) -> None:
    """Collect scalar JSON leaves, recursively flattening arrays and skipping objects.

    Observables are scalar artifacts: a matched object or `null` says nothing typed about the
    payload, while an array can legitimately hold many candidates (`$.recipients[*].address`).
    """
    if isinstance(value, (list, tuple)):
        for item in value:
            _flatten_match_values(item, out)
        return
    if isinstance(value, dict) or value is None or isinstance(value, bool):
        return
    out.append(value)


def extract_all_by_jsonpath(path: str, payload: Any) -> list[Any]:
    """Every scalar match at JSONPath `path` in `payload`, in document order.

    The scalar-field engine deliberately takes the first match (one title, one severity). Observable
    rules are the opposite case: every address in `$.recipients[*].address` is an artifact, not a
    fallback candidate. This helper keeps the compiled-path cache and tolerance in one place while
    returning all of them; a bad path still returns `[]` rather than raising.
    """
    expression = _compile(path)
    if expression is None:
        return []
    try:
        matches = expression.find(payload)
    except Exception:
        return []
    values: list[Any] = []
    for match in matches:
        _flatten_match_values(match.value, values)
    return values
