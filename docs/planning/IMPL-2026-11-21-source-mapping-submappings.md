# Implementation Brief — Source mapping per-type submappings

**Plan:** `TODO.md` §25 — Source mapping — per-type submappings
**Decisions:** Q25-1…Q25-10 answered 2026-11-21; all defaults accepted.
**Handoff to:** `django-backend` (mapping engine + ingest/remap threading), `django-frontend` (JSON editor help/list/raw type display), `qa-tester`, then `verifier` / `security-auditor`.

## Scope decisions

- Add optional v2-only `submappings` inside `IngestionSource.mapping_config`.
- Selector is raw top-level `payload["type"]`; exact case-sensitive match only.
- Missing/non-string/unknown type uses the master config.
- Matching submapping fully replaces the master config; no merge.
- Submappings may contain all normal v2 sections except nested `submappings`.
- Advanced JSON editing only; no first-class per-type editor in this iteration.
- No new ingestion warnings for selected/unknown type.
- Keep MAP-9: remap does not rewrite existing alert `type` / `source_ref`.
- Caps: max 50 submappings, max 100-character keys.
- No DB migration, no new model, no REST contract change.

Deferred ideas are recorded in `TODO.md §25.12` and must not be implemented in this brief.

## Tasks

### 1. `django-backend` — resolver, validation, editor helpers

Files:

- `ingest/mapping.py`
- `ingest/editor.py` (only if needed for helper preservation)

Work:

- Add constants near existing mapping caps:

```python
MAX_SUBMAPPINGS = 50
MAX_SUBMAPPING_KEY_LEN = 100
```

- Add:

```python
@dataclass(frozen=True)
class MappingResolution:
    config: dict[str, Any]
    matched_type: str | None = None
    used_submapping: bool = False


def resolve_mapping_config(
    mapping_config: dict[str, Any] | None,
    payload: Any,
) -> MappingResolution: ...
```

- Resolver behaviour:
  - Return master for empty config, no `submappings`, non-mapping payload, missing `type`, non-string `type`, or no exact key match.
  - On exact match return the submapping object as `resolution.config`, set `matched_type`, `used_submapping=True`.
  - Do not mutate `mapping_config` or `payload`.
  - Do not recurse into nested submappings.
- Extend `validate_config()`:
  - `submappings` must be a v2-only top-level object.
  - Reject `submappings` on legacy flat configs.
  - Max 50 keys; each key non-empty string ≤100 chars.
  - Each value must be a complete valid v2 config with `version: 2`.
  - Recursively validate nested `fields`, `value_maps`, `observables`, `correlation_key`, `on_missing` using existing rules/caps.
  - Reject nested `submappings`.
- Add a small helper for submapping count used by UI (e.g. `submapping_count(config)`), without changing existing `config_shape()` / `read_rules()` return contracts unless tests are updated deliberately.
- Ensure `ingest/editor.py` canonicalization/no-op checks handle nested submappings and `changed_keys()` emits dotted paths such as `submappings.ldt.fields.title.paths`.
- Ensure table/observable-only writers preserve `submappings` through `_preserved()`.

**Acceptance criteria**

- AC-B1: Pure resolver selects exact raw type match and full-replaces mapping config.
- AC-B2: All fallback cases return master unchanged without mutation.
- AC-B3: Validation accepts valid submappings and rejects nested/legacy/oversized/invalid nested configs.
- AC-B4: Audit changed-key paths include submapping paths but never values.
- AC-B5: Existing master-only configs behave exactly as before.

### 2. `django-backend` — thread effective config through all runtime paths

Files:

- `ingest/views.py`
- `ingest/pipeline.py`
- `ingest/remap.py`
- `ingest/observable_apply.py`
- `observables/alert_sync.py`
- `ui/views/ingestion_sources.py` (observables-only save guard)

Work:

- Webhook path:
  - In `webhook_alerts()`, after JSON object validation, call `resolve_mapping_config(source.mapping_config, payload)`.
  - Use `resolution.config` for `apply_mapping()`.
  - Pass the effective config into `store_ingested_alert(...)` as a keyword argument.
- `store_ingested_alert()`:
  - Accept `mapping_config: dict[str, Any] | None = None`.
  - If omitted, fall back to `source.mapping_config` for compatibility.
  - Use effective config for `extract_correlation_key()` and `sync_alert_observables(...)`.
- Extend `sync_alert_observables()` with an optional config argument; preserve current default behaviour when omitted.
- Remap/preview:
  - In `ingest/remap.py::_evaluate()`, resolve per alert from `alert.raw_payload`.
  - Use resolved config for `apply_mapping()` and observable sync.
  - `preview_alerts()` inherits the behaviour.
- Observable preview/apply:
  - In `ingest/observable_apply.py`, resolve per alert from `alert.raw_payload` before extraction/sync.
  - Preserve MAP-9: `type` and `source_ref` are not rewritten on existing alerts.
- UI apply paths:
  - Update `_observables_only_change()` so unchanged `submappings` do not make a master observables-only save look like an illegal top-level change.

**Acceptance criteria**

- AC-B6: Webhook field mapping, correlation key, and observables all use the same resolved config for one alert.
- AC-B7: Typeless/unknown-type payloads use master for all three paths.
- AC-B8: Remap and preview resolve per alert and agree on effective config.
- AC-B9: Observable preview/apply resolve per alert.
- AC-B10: Existing alert `type` / `source_ref` remain unchanged during remap.
- AC-B11: No webhook/auth/throttling/body-limit behaviour changes.

### 3. `django-frontend` / docs — JSON-first UI and documentation

Files:

- `ui/templates/ui/source_detail.html`
- `ui/templates/ui/sources_list.html`
- `ui/views/ingestion_sources.py`
- `docs/spec/api.md`
- `docs/spec/data-model.md`
- `docs/spec/identity-auth.md`
- `docs/spec/deviations.md`
- Optional: `README.md` only if adding a v2 example

Work:

- Advanced JSON box:
  - Add concise help text: v2-only `submappings`, raw top-level `type` selector, exact match, full replacement, master fallback.
  - Validation errors from nested submappings must render cleanly.
- Source detail/list:
  - Show submapping count alongside existing rule count, e.g. `N rules · M type overrides · severity←…`.
  - Do not hide master mapping status.
- Raw data tab:
  - Show raw payload `type` used for submapping selection, distinct from mapped alert `type`.
- Table/observable editors:
  - Continue editing master mapping only.
  - Preserve `submappings` on save.
- Docs:
  - Update mapping sections in `docs/spec/api.md`, `docs/spec/data-model.md`, and `docs/spec/identity-auth.md`.
  - Add MAP-10 to `docs/spec/deviations.md` documenting full replacement, v2-only validation, raw type selector,
    master fallback, and MAP-9 interaction.
  - Keep the README legacy example labelled as legacy or replace with a v2 example.

**Acceptance criteria**

- AC-F1: JSON editor accepts valid submappings and rejects invalid nested configs with readable errors.
- AC-F2: Source list/detail show submapping count without hiding master rules.
- AC-F3: Raw data tab displays raw `type` used for selection.
- AC-F4: Master table/observable saves preserve submappings.
- AC-F5: Docs and MAP-10 describe the shipped semantics.

### 4. `qa-tester` — tests

Primary suites to add/extend:

- `tests/conformance/test_mapping_engine.py`
- `tests/conformance/test_webhook_hardening.py`
- `tests/conformance/test_mapping_editor.py`
- `tests/conformance/test_settings_source_mapping.py`
- `tests/conformance/test_mapping_remap.py`
- `tests/conformance/test_ingest_alert_observables.py`
- `tests/conformance/test_ingest_observable_remap.py`
- `tests/conformance/test_source_observable_editor.py`
- `tests/conformance/test_source_observable_apply.py`
- `tests/conformance/test_observable_apply.py`
- `tests/conformance/test_source_detail_raw_data.py`
- `tests/conformance/test_tenant_scoping.py`

Required coverage:

- Resolver: exact match, missing type, non-string type, unknown type, no mutation, no nested recursion.
- Validation: valid submapping, legacy+submappings rejected, nested submappings rejected, nested invalid paths/transforms,
  over-cap count/key length.
- Webhook end-to-end: CrowdStrike-style payload with raw `"type": "ldt"` selects submapping affecting title,
  severity, correlation key, and observables.
- Typeless payload uses master for all interpreted fields.
- Remap/preview/apply with mixed raw types in one source use different effective configs.
- Observable extraction/sync uses effective config on live ingest, remap, and observable apply.
- Editor: JSON save, no-op/canonicalization, nested audit changed keys, table/observable-only saves preserve submappings.
- Tenant scoping unchanged.

**Acceptance criteria**

- AC-Q1: Targeted suites pass.
- AC-Q2: `make check` / full static gate passes with pinned `.venv`.
- AC-Q3: Secret scan clean.
- AC-Q4: Full pytest is run only as the final DoD gate unless explicitly requested.

## Constraints

- Use `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`.
- No schema change, migration, new model, new dependency, rest endpoint, Celery task, or persisted selection state.
- Do not modify `TODO.md §25` except to record deviations before merge.
- Do not implement deferred ideas from `TODO.md §25.12`.
- Never write secrets into repo files, tests, docs, fixtures, logs, or audit reports.
- Do not use `--no-verify`, destructive `git reset`, or `git checkout`.

## Definition of done

- AC-B1…B11, AC-F1…F5, and AC-Q1…Q4 pass.
- Verification report records any deviation from `TODO.md §25.11`.
- Security review confirms tenant isolation, bounded config size/validation, no payload logging, and unchanged webhook auth.
- Full pytest and secret scan are clean before merge.
