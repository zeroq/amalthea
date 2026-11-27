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

The §13.5 split moved the implementation into sibling modules; this package root preserves the
former `ingest.mapping` import surface unchanged.
"""

from .apply import (
    REASON_LABELS,
    UNRESOLVED_REASONS,
    AlertDraft,
    FieldWarning,
    apply_mapping,
)
from .config import (
    LEGACY_KEYS,
    MAX_FIELDS,
    MAX_PATH_LEN,
    MAX_PATHS_PER_FIELD,
    MAX_SUBMAPPING_KEY_LEN,
    MAX_SUBMAPPINGS,
    TARGETS,
    config_shape,
    read_rules,
    validate_config,
)
from .correlation import (
    MappingResolution,
    extract_correlation_key,
    resolve_mapping_config,
    submapping_count,
)
from .jsonpath import extract_all_by_jsonpath, extract_by_jsonpath
from .values import DEFAULT_MAX_LENGTH, DEFAULT_SCORE_BANDS, TRANSFORMS

__all__ = [
    "DEFAULT_MAX_LENGTH",
    "DEFAULT_SCORE_BANDS",
    "LEGACY_KEYS",
    "MAX_FIELDS",
    "MAX_PATHS_PER_FIELD",
    "MAX_PATH_LEN",
    "MAX_SUBMAPPINGS",
    "MAX_SUBMAPPING_KEY_LEN",
    "REASON_LABELS",
    "TARGETS",
    "TRANSFORMS",
    "UNRESOLVED_REASONS",
    "AlertDraft",
    "FieldWarning",
    "MappingResolution",
    "apply_mapping",
    "config_shape",
    "extract_all_by_jsonpath",
    "extract_by_jsonpath",
    "extract_correlation_key",
    "read_rules",
    "resolve_mapping_config",
    "submapping_count",
    "validate_config",
]
