# Implementation Brief — Submappings UI tab on source detail

**Plan:** `docs/planning/PLAN-2026-11-26-submappings-ui-tab.md`
**Decisions:** all defaults accepted 2026-11-26; amended the same day to add an Observable Mapping tab.
**Handoff to:** `django-backend`, `django-frontend`, `qa-tester`, then `verifier` / `security-auditor`.

## Scope

Add a `Submappings` tab to `ui-settings-source-detail`:

`Mapping | Observable Mapping | Submappings | Raw data`

The master observable-extraction card moved out of Mapping into `?tab=observable-mapping`; submapping-scoped observable editing remains on Submappings. Raw-data click-to-map for observables must land on the Observable Mapping tab; field targets still land on Mapping. Recorded deviation `SUBUI-1`: the submapping picker is a native `<select>` plus an **Open** button (no inline `onchange`, which CSP forbids).

Admin/superAdmin only. No role change, no DB migration, no new URL, no REST API change. The advanced JSON editor stays as the escape hatch. §25 runtime behavior stays unchanged.

## Dependencies / constraints

- Reuse `ingest.mapping.validate_config()`, `MAX_SUBMAPPINGS = 50`, `MAX_SUBMAPPING_KEY_LEN = 100`.
- Reuse `ingest.editor.EditorResult`, `build_table_config()`, `is_noop_save()`, `changed_keys()`.
- Reuse `ingest.observable_editor.build_observable_config()`.
- Reuse `_persist_mapping()` so write, transaction, and audit semantics stay identical.
- Reuse `_modal.html` for delete and apply confirmations; never native `confirm()`.
- Keep `resolve_ingestion_source()` / `not_viewer` / `_require_admin_ui()` boundaries intact.
- Never auto-convert a legacy flat master config.

## Backend tasks — `django-backend`

### B1. Add `ingest/submapping_editor.py`

Pure helpers:

- `submapping_config(config, key) -> dict | None`
- `submapping_editor_rows(config, key, submitted=None, errors=None)`
- `build_submapping_add(config, key_text) -> EditorResult`
- `build_submapping_delete(config, key) -> EditorResult`
- `build_submapping_config(config, key, field_rows, observable_rows, types) -> EditorResult`

Behavior:

- Strip key text; do not case-fold.
- Bootstrap `{}` to `{"version": 2, "fields": {}}` on first add.
- Reject legacy master, duplicate/empty/>100-char keys, and the 51st key.
- Save path edits only `config["submappings"][key]`.
- Validate the whole candidate with `validate_config()`.
- Do not mutate inputs.

**AC-B1:** Helper tests cover add, save, delete, duplicate/cap/key-length, legacy refusal, nested rejection, preservation of master/siblings, and nested `changed_keys()` paths without values.

### B2. Extend `ui/views/ingestion_sources.py`

- Allow `active_tab="observable-mapping"` and `active_tab="submappings"`.
- Compute latest alert/raw type for both `raw-data` and `submappings`; skip the query for `mapping`.
- Route `form == "submappings"` to `_submappings_post()`.
- Add context: selected key, sorted keys, prefill key, submapping rows, errors, delete-pending.
- Rejections re-render with `active_tab="submappings"` and operator input preserved.
- Success redirects to `?tab=submappings&submapping=<urlencoded key>`.

**AC-B2:** Tab selection, prefill, role/org gates, error re-render, and PRG are covered by conformance tests.

## Frontend tasks — `django-frontend`

### F1. `ui/templates/ui/source_detail.html`

Add the updated tab strip and the following cards:

1. Header/count/latest raw type/Add form.
2. Sorted submapping key list with select + delete controls.
3. Selected submapping field table + observable card + Save/Apply buttons.

Use existing `.tabs`, `.tab`, `.card`, `.data-table`, `.control-row`, `.hint`, `.msg-error`, and `.mono` conventions.

**AC-F1:** Final four-tab strip renders in order; hostile keys are escaped/URL-encoded; legacy master shows disabled Add; errors echo input; master observable card is absent from Mapping and present on Observable Mapping.

### F2. Delete confirmation

Add `ui/templates/ui/_source_submapping_delete_body.html` and include via `_modal.html`.

POST flow:

- `intent=delete` → render modal, write nothing.
- `intent=delete_confirmed` → delete selected key.
- Cancel/Esc → no write.

**AC-F2:** Custom dialog only; audit records `submappings.<key>` and never values.

### F3. Apply confirmation

Reuse the existing apply modal and preview. Candidate config is the whole config after the selected submapping edit.

**AC-F3:** Dialog states master + all submappings are applied using each alert’s raw type; `pending_config` is revalidated; MAP-9 preserved.

## QA tasks — `qa-tester`

New/updated tests:

- `tests/conformance/test_submapping_editor.py` (new)
- `tests/conformance/test_source_submappings_ui.py` (new)
- `tests/conformance/test_source_detail_raw_data.py` (four tabs / invalid-tab fallback)
- `tests/conformance/test_settings_source_mapping.py` (preservation, no-op, audit)
- `tests/conformance/test_source_observable_editor.py` (observable tab + submapping-scoped observables)
- `tests/conformance/test_raw_click_map.py`, `tests/conformance/test_raw_click_map_template.py` (observable prefill tab)
- `tests/conformance/test_source_observable_apply_ui.py` (observable apply modal on its own tab)
- `tests/conformance/test_mapping_engine.py` (no regressions)
- `tests/conformance/test_tenant_scoping.py` (new POST discriminator)

Run:

```bash
pytest -q \
  tests/conformance/test_submapping_editor.py \
  tests/conformance/test_source_submappings_ui.py \
  tests/conformance/test_source_detail_raw_data.py \
  tests/conformance/test_settings_source_mapping.py \
  tests/conformance/test_source_observable_editor.py \
  tests/conformance/test_mapping_engine.py
make check
make css-check
```

**AC-Q:** All targeted tests and static gates pass. Do not run full pytest unless the user asks; it remains a user-owned local/manual gate.

## Review handoff

After implementation:

1. `verifier` writes `docs/planning/VERIFICATION-2026-11-26-submappings-ui-tab.md` against the plan ACs.
2. `security-auditor` writes `docs/security/SECURITY-2026-11-26-submappings-ui-tab.md` (expected verdict PASS).
3. Any deviation from the plan or this brief must be recorded before merge.

## Out of scope

- Analyst-role edit access
- Submapping rename
- Per-type-only remap filter
- Nested submappings
- Advanced JSON editor for an individual submapping
