# IB-2026-11-27 — Mapping engine modularization (§13.5)

**Plan:** `docs/planning/PLAN-2026-11-27-mapping-engine-modularization.md`
**Status:** Implemented 2026-11-27; awaiting one final full `pytest` after §13 is complete. No subagents were used; full `pytest` was not run by the implementation agent.

## Tasks

### IB-0 — Gate check
- Superseded by user override D-13.5-1: proceed without waiting for the §13.4 full `pytest` log; final full suite will run after §13 is complete.
- **AC:** override is recorded in the plan; targeted suites still gate implementation.

### IB-1 — Characterization baseline
- Add/confirm direct characterization tests for every public callable:
  `extract_by_jsonpath`, `extract_all_by_jsonpath`, `FieldWarning.as_dict`, `FieldWarning.as_text`, `apply_mapping`, `config_shape`, `read_rules`, `validate_config`, `extract_correlation_key`, `MappingResolution`, `submapping_count`, `resolve_mapping_config`.
- **AC:** every public callable is covered directly or by a clearly named indirect characterization test; new tests pass against the old module before moving code.

### IB-2 — Create `ingest/mapping/` package
- `jsonpath.py`: `_parse`, `_COMPILED`, `_COMPILED_MAX`, `_compile`, `extract_by_jsonpath`, `_extract_by_jsonpath`, `_flatten_match_values`, `extract_all_by_jsonpath`.
- `values.py`: `_SEVERITY_BY_NAME`, `DEFAULT_SCORE_BANDS`, `_WHITESPACE`, `_TIMESTAMP`, coercion helpers, `TRANSFORMS`.
- `config.py`: `TARGETS`, `LEGACY_KEYS`, path/limit constants, `_Rule`, `_Config`, `_normalize`, `config_shape`, `read_rules`, `validate_config`; change observable-rules import to `from ..observable_rules import validate_observables`.
- `apply.py`: `FieldWarning`, `UNRESOLVED_REASONS`, `REASON_LABELS`, `AlertDraft`, `_is_answer`, `_apply_transform`, `apply_mapping`, `_as_text_warning`.
- `correlation.py`: `extract_correlation_key`, `MappingResolution`, `submapping_count`, `resolve_mapping_config`.
- `__init__.py`: compatibility shim with explicit `__all__` covering the current public surface.
- Delete `ingest/mapping.py`.
- **AC:** all top-level symbols exist once; no caller edits; no behavior-bearing literal changes; no import cycles.

### IB-3 — Verification
- Structural AST inventory, `git diff --check`, `make check`, secret scan, targeted suites from plan §T2.2.
- **AC:** all green; no full `pytest` by the implementation agent; user full-suite gate remains pending before §13.5 closure.

## Dependencies and constraints
- Read/use `.venv/bin/python` and `.venv/bin/pytest`.
- Do not print `.env`, secrets, raw payloads or exception text.
- No model/migration/API/UI behavior changes.
- No caller edits in `ingest/`, `ui/`, or `tests/` except new characterization tests.
- Do not commit unless explicitly requested.
