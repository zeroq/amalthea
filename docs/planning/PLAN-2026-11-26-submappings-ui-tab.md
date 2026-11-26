# Plan — Submappings UI tab on source detail

**Date:** 2026-11-26
**Source item:** §25 “Source mapping — per-type submappings” follow-up (completed backend, new UI gap)
**Status:** Approved with defaults; amended 2026-11-26 for the Observable Mapping tab; implemented, pending verification
**Handoff to:** `django-backend` (pure editor helpers + view wiring), `django-frontend` (tab/template/forms), `qa-tester`, then `verifier` / `security-auditor`

## Summary

§25 shipped a v2-only `submappings` language inside `IngestionSource.mapping_config`, but the only way to add or edit a submapping today is the **Advanced — stored configuration** JSON textarea on the source detail **Mapping** tab. The field-rule table and observable-extraction card preserve submappings but cannot edit their internals.

This plan adds a first-class **Submappings** tab to the source detail page:

`Mapping | Observable Mapping | Submappings | Raw data`

The new tab lists raw `type` keys, supports add/delete with custom dialogs, and exposes the existing field-rule table plus observable-rules card for the selected submapping. The master config remains on the Mapping tab. The advanced JSON box remains the escape hatch for `value_maps`, `correlation_key`, and other v2 settings.

### Amendment — Observable Mapping tab (2026-11-26)

The user also asked to move the existing master observable-extraction card off the Mapping tab so the first tab is not crowded. This is a recorded deviation from the original three-tab order:

- Final tab order is `Mapping | Observable Mapping | Submappings | Raw data`.
- `?tab=observable-mapping` renders the master observable card and its apply-to-unlinked-alerts dialog.
- The Mapping tab keeps identity, field rules, health, tokens, advanced JSON, and a short pointer to Observable Mapping.
- Submapping-scoped field and observable rules stay on `?tab=submappings`.
- Raw-data click-to-map for observables now targets `?tab=observable-mapping&map_target=observable…`; plain field targets still use `?tab=mapping`.
- Recorded deviation `SUBUI-1`: the submapping picker is a native `<select>` plus an explicit **Open** button instead of per-key row links. This keeps keyboard/progressive-enhancement behaviour and avoids the inline `onchange` handler that the CSP conformance test rejects.

**Actor note:** source detail is admin/superAdmin-only today. This plan keeps that boundary. The word “analyst” in the request is treated as “operator/admin”; changing the role policy is explicitly out of scope.

## Goals

1. Give admins a structured, discoverable way to add, edit, and delete a submapping without hand-writing JSON.
2. Make the raw `type` selector legible: show the latest raw type from stored alerts and offer it as a prefill key.
3. Reuse the existing schema authority (`ingest.mapping.validate_config`) and existing write/audit/no-op machinery.
4. Keep master configuration and submapping configuration clearly separated.
5. Preserve §25 semantics: exact case-sensitive raw-type match, full replacement, master fallback, v2-only, no nested submappings, max 50 keys, key length ≤100.
6. Keep apply-to-unlinked-alerts correct with submappings: the whole effective config is applied per alert’s raw type.

## Non-goals

- No role-policy change: `analyst` users still get 404 on source detail.
- No new model, migration, REST endpoint, or URL route.
- No submapping rename; delete and re-add is the supported path.
- No nested submappings.
- No per-type-only remap filter; apply remains feed-wide with per-alert resolution.
- No silent conversion of a legacy flat master config.
- No removal of the advanced JSON editor.
- No native `confirm()` / `alert()` / `prompt()` anywhere in the flow.

## Architecture

### Stack and rationale

- **Django templates + server-rendered forms**: consistent with the existing source detail page and progressive-enhancement rule.
- **HTMX/Tailwind only where already used**: this feature is ordinary forms + `<dialog>`; no new JS module is required.
- **Django view orchestration**: same `ui-settings-source-detail` route, discriminated by `form=submappings` and `intent`.
- **Pure editor helpers in `ingest/`**: mirrors `ingest/editor.py` and `ingest/observable_editor.py`; keeps validation testable without a request.
- **No Channels/Celery changes**: ingestion and remap already resolve submappings through `ingest.mapping.resolve_mapping_config()`.

### ADR references

- **ADR-008** — source-less webhook routing: token identifies feed/org; mapping config stays feed-owned.
- **ADR-003** — keyboard-driven dark UI and custom modals; no native dialogs.
- **ADR-004** — tenant isolation and cross-org audit reads; keep `resolve_ingestion_source()` and `maybe_record_cross_org_read()` semantics.
- **ADR-006** — layered authorization; `not_viewer` + `_require_admin_ui()` + `write=True` on POST remain in force.

## Data model / DB schema

No migration. The only stored object remains:

- `ingest.IngestionSource.mapping_config` (`JSONField`, PostgreSQL JSONB)

Shape after this work is unchanged from §25:

```json
{
  "version": 2,
  "fields": { "...": "master field rules" },
  "observables": [],
  "value_maps": {},
  "submappings": {
    "ldt": {
      "version": 2,
      "fields": { "...": "rules that fully replace master for raw type 'ldt'" }
    }
  }
}
```

Invariants:

- `submappings` is v2-only.
- A submapping value is a complete v2 config.
- Nested `submappings` are rejected.
- Keys are non-empty strings, ≤100 characters, max 50 entries.
- Match is exact and case-sensitive against raw top-level `payload["type"]`.

## API / POST contract

No public REST API change. All writes go through the existing UI route:

`GET/POST /settings/sources/<source_id>` · name `ui-settings-source-detail`

GET additions:

| Query param | Meaning |
|---|---|
| `tab=submappings` | Show the new tab |
| `submapping=<urlencoded key>` | Select a submapping for editing |
| `submapping_prefill=<urlencoded raw type>` | Pre-fill the Add form key |

POST discriminator: `form=submappings`.

| `intent` | Required fields | Effect |
|---|---|---|
| `add` | `submapping_key` | Validate and add `{"version": 2, "fields": {}}` under that key |
| `save` | `submapping_key`, field-rule rows, observable rows | Save only the selected submapping |
| `delete` | `submapping_key` | Render custom confirmation dialog; write nothing |
| `delete_confirmed` | `submapping_key` | Delete the selected submapping |
| `apply` | `submapping_key`, field-rule rows, observable rows | Validate candidate, preview remap, open confirmation dialog |
| `apply_confirmed` | `pending_config` | Re-validate, persist whole config, remap feed |

Success is PRG, redirecting to:

`?tab=submappings&submapping=<urlencoded key>`

Rejection is a 200 re-render that echoes the submitted key/rows and keeps `active_tab="submappings"`.

## UI / UX

### Tab strip

`ui/templates/ui/source_detail.html` gains two tabs (final order, after amendment):

1. **Mapping** (default) — master identity, field rules, health, tokens, advanced JSON, and a pointer to Observable Mapping.
2. **Observable Mapping** — master observable-extraction card and observable-only apply dialog.
3. **Submappings** — new structured override editor.
4. **Raw data** — unchanged.

`aria-current="page"` and `tab-on` follow the selected tab. Invalid `?tab=` values fall back to `mapping`.

### Submappings tab layout

1. **Header card**
   - “N type override(s)” with cap `50`.
   - Explanation: exact case-sensitive raw `type`, full replacement, master fallback.
   - If a latest stored alert has a string raw `type`: show `Raw type: <code>ldt</code>` and an **Use this key** link that sets `submapping_prefill`.
   - If master shape is `legacy`: show an explanation and disable Add; do not auto-convert.
   - If master shape is `empty` or `v2`: Add is available.

2. **Submapping list**
   - One row per key, sorted by key.
   - Each key links to `?tab=submappings&submapping=<urlencoded key>`.
   - Keys are HTML-escaped and URL-encoded; `aria-current` marks the selected key.
   - Each row has an **Delete** button that posts `intent=delete` and opens the shared custom modal.
   - Delete confirm uses `_modal.html` with hidden `intent=delete_confirmed` and `submapping_key`.

3. **Selected submapping editor**
   - Reuses the existing field-rule table markup from the Mapping tab, scoped to the selected submapping’s `fields`.
   - Reuses the observable-extraction card markup, scoped to the selected submapping’s `observables`.
   - Buttons: **Save submapping**, **Apply to unlinked alerts…**.
   - The Apply dialog must say: “This applies the complete mapping — master and all submappings — using each alert’s raw type.”

4. **Empty state**
   - “No type overrides yet. Add one to give a raw alert `type` its own mapping.”

### Accessibility

- Custom `<dialog>` only; Esc closes, Tab trap comes from the existing modal pattern.
- Icon-only controls need an accessible name; decorative icons `aria-hidden="true"`.
- Severity/colour rules unchanged.
- Keyboard tab order: tab strip → Add form → submapping list → selected editor → action buttons.

## Phases and tasks

### Phase 1 — Pure submapping editor helpers

**Files**

- `ingest/submapping_editor.py` (new)
- `tests/conformance/test_submapping_editor.py` (new)

**Task B1 — helper functions**

Add pure helpers using `ingest.editor.EditorResult` and the existing validation/schema:

```python
def submapping_config(config: Any, key: str) -> dict[str, Any] | None: ...
def submapping_editor_rows(config: Any, key: str, submitted=None, errors=None) -> list[dict]: ...
def build_submapping_add(config: Any, key_text: str) -> EditorResult: ...
def build_submapping_delete(config: Any, key: str) -> EditorResult: ...
def build_submapping_config(
    config: Any,
    key: str,
    field_rows: Mapping[str, Mapping[str, str]],
    observable_rows: Mapping[str, str],
    types: Sequence[Any],
) -> EditorResult: ...
```

Rules:

- Normalize key text with `.strip()` only; do not case-fold.
- Bootstrap `{}` to `{"version": 2, "fields": {}}` when adding the first submapping.
- Refuse to add to a legacy flat master config.
- Reject duplicate keys, empty keys, keys >100 chars, and adding past 50.
- Save path: run `build_table_config()` on the submapping config, then `build_observable_config()`, then put the result back under `config["submappings"][key]`.
- Validate the **whole candidate** with `validate_config()`.
- Never mutate the input config.
- Use `changed_keys()` compatible nested paths via existing behavior (`submappings.<key>.fields.title.paths`).

**AC-B1.1:** Adding a key to `{}` or a valid v2 config produces a valid whole config with one empty submapping.
**AC-B1.2:** Adding to a legacy flat config returns an error and leaves the original untouched.
**AC-B1.3:** Duplicate, empty, >100-char, and 51st keys are rejected with human-readable errors.
**AC-B1.4:** Saving a selected submapping preserves master fields and sibling submappings byte-for-byte except for the selected key.
**AC-B1.5:** Observable-only and field-only edits inside a submapping preserve the other section.
**AC-B1.6:** Candidate with nested `submappings` is rejected.
**AC-B1.7:** `changed_keys(before, after)` reports nested submapping paths and never values.

### Phase 2 — View routing and context

**Files**

- `ui/views/ingestion_sources.py`
- `tests/conformance/test_source_detail_raw_data.py`
- `tests/conformance/test_settings_source_mapping.py`

**Task B2 — tab and latest-raw-type wiring**

- Add `submappings` to the `active_tab` whitelist.
- Extract latest-alert/raw-type lookup so it runs for both `raw-data` and `submappings`, not for `mapping`.
- Add `selected_submapping`, `submapping_prefill`, `submapping_keys`, `submapping_errors`, and delete-pending context.
- Route `form == "submappings"` to a new `_submappings_post()` before the existing table/JSON routing.

**AC-B2.1:** `?tab=submappings` renders the new tab; invalid tab still falls back to Mapping.
**AC-B2.2:** Mapping tab performs no extra latest-alert query.
**AC-B2.3:** Latest raw `type` is shown on the Submappings tab and remains distinct from mapped `Alert.type`.
**AC-B2.4:** `analyst` role gets 404 on GET and POST with `form=submappings`.
**AC-B2.5:** Cross-org superAdmin GET still records an audit read; cross-org POST still fails.

### Phase 3 — Template and form flows

**Files**

- `ui/templates/ui/source_detail.html`
- `ui/templates/ui/_source_submapping_delete_body.html` (new)
- `tests/conformance/test_source_submappings_ui.py` (new)
- `tests/conformance/test_tenant_scoping.py` (update if route/intent enumeration needs coverage)

**Task F1 — tab/list/add/edit UI**

Implement the layout described above, reusing existing CSS classes and table partial shapes.

**AC-F1.1:** Tab strip has Mapping, Submappings, Raw data in that order.
**AC-F1.2:** Add/delete/select controls are keyboard reachable and accessible.
**AC-F1.3:** Hostile key values (`<script>`, quotes, spaces, `/`, `&`) are escaped in HTML and URL-encoded in links.
**AC-F1.4:** Legacy master shows a disabled Add form and explanatory text.
**AC-F1.5:** Rejected add/save re-renders with the operator’s input visible and `active_tab="submappings"`.

**Task F2 — delete dialog**

Use the existing `_modal.html` pattern, not native dialogs.

**AC-F2.1:** `intent=delete` writes nothing and renders a custom `<dialog>`.
**AC-F2.2:** `intent=delete_confirmed` removes only the selected key.
**AC-F2.3:** Cancel/Esc leaves the config unchanged.
**AC-F2.4:** Audit records `submappings.<key>` changed, never the submapping contents.

**Task F3 — apply-to-unlinked-alerts**

Reuse `_confirm_mapping_apply()` / `_apply_mapping_to_alerts()` with the candidate whole config.

**AC-F3.1:** Preview uses `preview_alerts()` and resolves submappings per alert raw type.
**AC-F3.2:** Confirmation text explicitly states that master and all submappings are applied.
**AC-F3.3:** Confirm re-validates `pending_config` before writing.
**AC-F3.4:** MAP-9 remains intact: remap never rewrites alert `type` or `source_ref`.

### Phase 4 — Docs, gates, verification

**Files**

- `docs/spec/ui-style.md`
- `docs/spec/data-model.md` (only if the stored shape needs a UI note)
- `docs/planning/VERIFICATION-2026-11-26-submappings-ui-tab.md` (new, after verification)
- `docs/security/SECURITY-2026-11-26-submappings-ui-tab.md` (new, after security review)

**Task Q1 — tests and gates**

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

**AC-Q1.1:** All targeted tests pass.
**AC-Q1.2:** `make check` and `make css-check` pass.
**AC-Q1.3:** Full pytest remains user-owned; do not claim it unless the user supplies a log.

**Task Q2 — independent review**

Handoff completed diff to `verifier` and `security-auditor` against these ACs.

**AC-Q2.1:** Verifier report records each AC as Met / Not met / Deviated.
**AC-Q2.2:** Security review verdict is PASS with no unresolved High/Medium findings.
**AC-Q2.3:** Any deviation is recorded in the plan before merge.

## Security

- Authorization: unchanged admin/superAdmin boundary; viewer/analyst deny remains layered.
- Tenancy: `resolve_ingestion_source()` remains the only source lookup; cross-org reads audited.
- CSRF: all POSTs keep `{% csrf_token %}`.
- Audit: reuse `record_ingestion_source_updated()`; `changed_keys()` emits key paths only, never values.
- Input validation: central `validate_config()`; caps and v2-only rules enforced server-side.
- Output encoding: submapping keys are escaped in text and URL-encoded in query strings.
- No secrets: no new env vars, tokens, or credentials; secret scan remains clean.
- No native dialogs: all confirmations use the existing custom `<dialog>` pattern.

## Testing

- New pure helper tests in `tests/conformance/test_submapping_editor.py`.
- New UI conformance tests in `tests/conformance/test_source_submappings_ui.py`.
- Update source-detail tab tests for the final four-tab strip and the observable card’s move.
- Update raw-data click-to-map tests: field paths land on Mapping; observable paths land on Observable Mapping.
- Keep existing mapping-engine, mapping-editor, observable-editor, and settings-source-mapping suites green.
- Add tenant/role coverage for the new POST discriminator.
- Full suite is a user-owned local/manual gate; agent must not run it unless explicitly asked.

## Risks

1. **Legacy conversion trap** — silently converting a flat master config would change literal-key fallback behavior. Mitigation: refuse Add for legacy and show guidance.
2. **Key injection** — arbitrary raw `type` strings can be hostile. Mitigation: escape + URL-encode + cap length.
3. **Apply confusion** — an operator may think they are applying only the selected submapping. Mitigation: dialog text states whole-config, per-alert resolution.
4. **Partial editor state** — rejected POST must not fall back to the Mapping tab. Mitigation: explicit `active_tab="submappings"` in every rejection render.
5. **No-op/audit drift** — table/observable editors can canonicalize. Mitigation: reuse `is_noop_save()` and `_persist_mapping()`.
6. **Nested submappings** — must stay rejected even through the structured editor. Mitigation: final whole-config validation.

## Open questions

None. Defaults accepted 2026-11-26:

1. Admin/superAdmin-only access.
2. Tab order `Mapping | Observable Mapping | Submappings | Raw data` (final, after amendment).
3. Field table + observable card per submapping; no nested submappings; advanced JSON remains master-only.
4. Add form plus “Use latest raw type” prefill.
5. Apply-to-unlinked-alerts uses whole config with per-alert resolution.
6. Detailed submapping help moves to the new tab; Mapping JSON keeps a short pointer.

Deferred, not in this plan: submapping rename, per-type-only remap filter, analyst-role edit access.
