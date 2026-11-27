# PLAN-2026-11-27 — Mapping engine modularization (§13.5)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Planner/tech lead; implementation by the main agent (no subagents).
**Source:** `TODO.md` §13.5, §13 audit 2026-11-04.
**Gate note:** §13.4 is implemented but still awaits the user-owned full `pytest` log. Default: do not begin §13.5 implementation until that gate closes, so regression attribution stays clean.

**D-13.5-1 (user override):** the user explicitly authorized starting §13.5 before the §13.4 full `pytest` gate, with one full suite to be run after §13 is complete. Targeted suites remain mandatory for each item.

**D-13.5-2 (implementation detail):** explicit `__all__` declarations were added to `ingest/mapping/__init__.py` and `ingest/mapping/jsonpath.py` for mypy export clarity. They are additive; the old public surface is unchanged.

**D-13.5-3 (test addition):** `tests/conformance/test_mapping_engine_characterization.py` was added to pin every public callable before the move, as required by the §13.5 DoD.

## Summary

`ingest/mapping.py` is currently 1,013 lines (TODO still says 842) and combines JSONPath extraction, value transforms, config normalization/validation, mapping application and submapping/correlation resolution. This plan creates an `ingest/mapping/` package with the five target modules from the TODO, leaves a compatibility `ingest/mapping/__init__.py`, and preserves every public import path and behavior.

This is a high-risk refactor: no behavior changes, and characterization tests for public functions are added or confirmed before code moves.

## Goals

- G1 — Replace `ingest/mapping.py` with an `ingest/mapping/` package: `jsonpath.py`, `values.py`, `config.py`, `apply.py`, `correlation.py`, plus `__init__.py`.
- G2 — Preserve every existing `from ingest.mapping import ...` path, including constants and private-ish aliases used inside the package.
- G3 — Preserve pure-function semantics: no ORM, settings, logging or HTTP is introduced into the engine.
- G4 — Add/find characterization tests for every public callable before moving code.
- G5 — Keep the user-owned full `pytest` gate authoritative for closeout.

## Non-goals

- No changes to mapping config language, legacy/v2 compatibility, transform names, warning reasons/labels, severity bands, submapping behavior or correlation-key precedence.
- No changes to webhook ingestion, remap, editor forms, UI templates or API contracts.
- No schema/migration changes.
- No reformatting or “cleanup” beyond what the move requires.
- No full `pytest` run by the implementation agent.

## Architecture

Stack remains as `AGENTS.md`: Django 5.x + DRF, PostgreSQL, Redis/Celery/Channels. The mapping engine remains pure Python at the presentation/ingest boundary.

**ADR reference:** none; no new architectural decision.

### Proposed module ownership

| Module | Owns |
|---|---|
| `ingest/mapping/jsonpath.py` | `_parse`, `_COMPILED`, `_COMPILED_MAX`, `_compile`, `extract_by_jsonpath`, `_extract_by_jsonpath`, `_flatten_match_values`, `extract_all_by_jsonpath` |
| `ingest/mapping/values.py` | `_SEVERITY_BY_NAME`, `DEFAULT_SCORE_BANDS`, `_WHITESPACE`, `_TIMESTAMP`, coercion helpers (`_as_text`, `_as_int`, `_severity_from_*`, `_as_datetime`, `_as_lower`, `_as_list`, `_as_json`, `_tlp`, `_pap`), `TRANSFORMS` |
| `ingest/mapping/config.py` | `TARGETS`, `LEGACY_KEYS`, `MAX_PATH_LEN`, `MAX_PATHS_PER_FIELD`, `MAX_FIELDS`, `DEFAULT_MAX_LENGTH`, `MAX_SUBMAPPINGS`, `MAX_SUBMAPPING_KEY_LEN`, `_Rule`, `_Config`, `_normalize`, `config_shape`, `read_rules`, `validate_config` |
| `ingest/mapping/apply.py` | `FieldWarning`, `UNRESOLVED_REASONS`, `REASON_LABELS`, `AlertDraft`, `_is_answer`, `_apply_transform`, `apply_mapping`, `_as_text_warning` |
| `ingest/mapping/correlation.py` | `extract_correlation_key`, `MappingResolution`, `submapping_count`, `resolve_mapping_config` |
| `ingest/mapping/__init__.py` | Compatibility shim re-exporting every public name; explicit `__all__` for mypy |

**Import direction:** `values.py` → none of the package; `jsonpath.py` → none; `config.py` → `values.py`, `jsonpath.py`; `apply.py` → `config.py`, `values.py`, `jsonpath.py`; `correlation.py` → `jsonpath.py`. No module imports `ingest.mapping` (the package root).

**Relative import update:** `validate_config` currently does `from .observable_rules import validate_observables`; in `ingest/mapping/config.py` this becomes `from ..observable_rules import validate_observables`.

## Data model

No model, migration, index or constraint changes.

## API

No endpoint or request/response contract changes. Existing imports from `ingest.mapping` must keep working; current external import set is 17 names:

- `AlertDraft`, `DEFAULT_SCORE_BANDS`, `LEGACY_KEYS`, `MAX_PATH_LEN`, `MAX_SUBMAPPINGS`, `MAX_SUBMAPPING_KEY_LEN`, `REASON_LABELS`, `TARGETS`, `TRANSFORMS`, `UNRESOLVED_REASONS`, `apply_mapping`, `config_shape`, `extract_all_by_jsonpath`, `read_rules`, `resolve_mapping_config`, `submapping_count`, `validate_config`.

Compatibility shim should additionally export `extract_by_jsonpath`, `DEFAULT_MAX_LENGTH`, `MAX_PATHS_PER_FIELD`, `MAX_FIELDS`, `FieldWarning`, `MappingResolution` and `extract_correlation_key` to preserve the module's current public surface.

## UI / UX

No UI changes. Wording constants (`REASON_LABELS`), warning reasons and form field names remain unchanged; `ui/templatetags/ui_tags.py`, `ingest/editor.py`, `ingest/submapping_editor.py` and `ui/views/ingestion_sources.py` keep importing from `ingest.mapping`.

## Phases and tasks

### Phase 0 — Gate and baseline

**T0.1 — Confirm §13.4 full `pytest` is closed.**
- Do not start §13.5 implementation before the §13.4 full-suite log is green and §13.4 is closed.
- **AC:** §13.4 closeout recorded; otherwise this plan remains draft/held.

**T0.2 — Characterization baseline.**
- Add or confirm direct tests for every public callable: `extract_by_jsonpath`, `extract_all_by_jsonpath`, `FieldWarning.as_dict`, `FieldWarning.as_text`, `apply_mapping`, `config_shape`, `read_rules`, `validate_config`, `extract_correlation_key`, `MappingResolution`, `submapping_count`, `resolve_mapping_config`.
- Existing strong suites (`test_mapping_engine.py`, `test_mapping_editor.py`, `test_settings_source_mapping.py`, `test_mapping_remap.py`) count when they pin the behavior; add a compact characterization file only for gaps.
- **AC:** every public callable has at least one direct or clearly named indirect characterization test before code moves; new tests pass on the old module.

### Phase 1 — Create package modules

**T1.1 — Add the mapping package.**
- Create the six files in the ownership table; move code verbatim.
- Preserve docstrings, constants, dataclass field order, warning text and transform registry order.
- **AC:** all top-level symbols exist exactly once under `ingest/mapping/`; no behavior-bearing literal is changed.

**T1.2 — Replace `ingest/mapping.py` with the compatibility shim.**
- Delete the old module file; add `ingest/mapping/__init__.py`.
- Re-export the full public surface with explicit `__all__`; no caller edits in `ingest/`, `ui/`, or `tests/`.
- **AC:** all 17 external imports and all exported public names resolve from `ingest.mapping`; no import cycles.

### Phase 2 — Verification and closeout

**T2.1 — Structural and static checks.**
- AST inventory before/after; `git diff --check`; `make check`; secret scan.
- **AC:** function/class/constant names and counts unchanged; no forbidden wire literals outside the boundary; static gates clean.

**T2.2 — Targeted test verification.**
- Run focused suites: `test_mapping_engine.py`, `test_mapping_editor.py`, `test_mapping_observables.py`, `test_mapping_remap.py`, `test_settings_source_mapping.py`, `test_submapping_editor.py`, `test_source_observable_editor.py`, `test_ingest_alert_observables.py`, `test_ingest_observable_remap.py`, `test_source_observable_apply.py`, `test_raw_click_map_template.py`, `test_wire_boundary.py`, plus the new characterization file if added.
- **AC:** all selected suites pass; no full `pytest` by the implementation agent.

**T2.3 — User-owned full suite and closeout.**
- User supplies/confirms the full `pytest` result after implementation.
- Update `TODO.md` §13.5, add a `COMPLETED.md` entry, and record the implementation commit hash.
- **AC:** full-suite log green with zero failures/errors; §13.5 closed only after that evidence exists.

## Security

- No secrets or credentials introduced.
- Operator-authored config remains lenient at ingest and strict at save time; error strings and warning tokens are moved verbatim.
- Pure-function boundary is preserved; no DB/ORM/network side effects enter the engine.
- Secret scan remains mandatory; no `--no-verify`.

## Testing

- Characterization tests before the move (T0.2).
- Targeted conformance suites in T2.2.
- `make check`, `git diff --check`, secret scan.
- Full `pytest` remains a user-owned gate.
- Do not print `.env`, secrets, raw payloads or exception text.

## Risks

- **High regression risk:** mapping engine is central to ingest/remap/editor; mitigated by characterization-first and no behavior changes.
- **Import-cycle risk:** config/validation and apply/resolution dependencies; mitigated by the strict one-way import direction.
- **Relative import move:** `observable_rules` import must become `..observable_rules`; mitigated by targeted tests.
- **Public import drift:** 17 external names plus internal public names must resolve from the shim; mitigated by AST inventory and explicit `__all__`.
- **Stale TODO count:** TODO says 842 lines; current file is 1,013. Inventory uses symbols/tests, not line count.
- **Cross-item attribution:** starting before §13.4 full pytest closes would blur regressions; mitigated by T0.1.

## Open Questions

- None. User override: implement before the §13.4 full suite; add characterization coverage; preserve all imports via the shim; do not edit callers.

## Implementation status

- Implemented 2026-11-27: `ingest/mapping.py` replaced by `ingest/mapping/` with `jsonpath.py`, `values.py`, `config.py`, `apply.py`, `correlation.py` and compatibility `__init__.py`.
- Characterization file added and first run green against the old module (`20 passed`).
- Structural verification: no old top-level symbol is missing; additive `__all__` declarations only; explicit shim exports 24 names covering the 17 external imports and the broader public surface.
- `make check`, `git diff --check`, and secret scan clean.
- Targeted suites from T2.2: **459 passed**, 7 pre-existing naive-datetime warnings, in 96.58s.
- Closed 2026-11-27: user-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green — **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**. Committed as `6b47c4be` (`refactor: complete section 13 modularization backlog`).
