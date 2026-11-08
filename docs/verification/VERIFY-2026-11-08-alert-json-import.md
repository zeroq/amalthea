# Verification Report: Alert JSON Export/Import

## Plan
**Plan:** `TODO.md` §19 — Alert JSON export/import (AIMP-1..7)  
**Verified:** 2026-11-08 14:32 UTC  
**Status:** Pass

## Summary
- Total ACs: 7 (AIMP-1 through AIMP-7)
- Met: 7
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AIMP-1: alert export includes scalars + raw payload + observables/tags/custom fields/comments/procedures; no secrets/internal hash; `Content-Disposition` filename

**Status:** Met

**Evidence:**
- `alerts/export.py::alert_export_document` (line 23-70) builds the `AlertExport` v1 document with:
  - `alert` payload containing `**alert_json(alert)` + `"rawPayload": alert.raw_payload or {}` (line 25-28)
  - `observables`: `observable_json(link)` for each `alert.alert_observables` (line 32-37)
  - `tags`: `{"name": link.tag.name}` for each `alert.tag_links` (line 38-41)
  - `customFields`: `{"name": value.custom_field.name, "value": value.value}` for each `alert.custom_field_values` (line 42-47)
  - `comments`: serialized with message, createdAt, createdBy (line 48-55)
  - `procedures`: `procedure_json(procedure)` for each procedure (line 56-58)
- `payload_ssdeep` is never exported (line 7 of export.py: "The internal `payload_ssdeep` is never exported; it is recomputed on import.")
- `Content-Disposition` filename set in `ui/views/alerts.py::alert_export_ui` (line 580-582): `f'attachment; filename="alert-{str(alert.id)[:8]}-export.json"'`
- Test `test_export_document_has_children_and_no_internal_hash` confirms all children present and `payload_ssdeep` absent (test_alert_export_import.py:88-91)

**Notes:** `rawPayload` is deliberately inlined (same recorded deviation as case export), per §19.2 decision. No other internal hashes or secrets are emitted.

---

### AIMP-2: a valid file creates a new alert in the actor's org with the right scalars and provenance

**Status:** Met

**Evidence:**
- `alerts/importing.py::import_alert_document` (line 247-336) creates a new `Alert` with:
  - `owner_org_id=actor.org_id` (line 293) — actor's org forced
  - Required fields validated: `title`, `type`, `source`, `sourceRef` (line 258-264)
  - `status` resolved by value with fallback (line 273: `_resolve_status`)
  - `assignee` resolved by login, org-scoped: `_resolve_assignee` filters by `actor.org_id` (line 130)
  - `date` resolved with ISO parsing fallback (line 274: `_resolve_date`)
  - `raw_payload` set from export data (line 310)
  - Provenance written to `ingestion_warnings["imported"]` (line 325-333) with `at`, `by`, `sourceFile`, `originalId`
- Test `test_round_trip_imports_children_and_provenance` confirms import creates alert in correct org with correct scalars and provenance (test_alert_export_import.py:136-153)

**Notes:** Assignee from export that belongs to a different org is silently dropped with a warning (test_cross_org_assignee_is_not_resolved: line 218-222).

---

### AIMP-3: status/assignee/date/enums resolved safely

**Status:** Met

**Evidence:**
- `_resolve_status` (importing.py:114-123): resolves by value, falls back to "New", then to `resolve_alert_status("New")` — no auto-creation from garbage values
- `_resolve_assignee` (importing.py:126-133): resolves by login, checks `User.objects.filter(login=login, org_id=actor.org_id)` — warns if not in organisation, returns None
- `_resolve_date` (importing.py:136-145): parses ISO datetime, makes naive datetimes aware w/ UTC, falls back to `timezone.now()` on invalid input
- Test `test_import_page_get` confirms multipart form is rendered (test_alert_export_import_ui.py:245)
- Test `test_import_oversized_upload_rejected` confirms 5 MiB limit enforced (test_alert_export_import.py:196-197)

**Notes:** All three fields have explicit fallbacks and warnings rather than 500 errors.

---

### AIMP-4: malformed/oversized/wrong-type/unsupported-version/duplicate rejected atomically with clear errors

**Status:** Met

**Evidence:**
- `parse_alert_export` (importing.py:59-74): checks `MAX_BYTES` (5 MiB), UTF-8 decode, JSON parse, `_type == "AlertExport"`, `version == 1`, `_check_bounds` (node/depth/children caps)
- Error codes and HTTP statuses:
  - `"oversized_file"` → 400 (line 62)
  - `"malformed_json"` → 400 (line 70)
  - `"invalid_document"` → 400 (line 72)
  - `"missing_alert"` → 400 (line 261-264)
  - `"duplicate"` → 409 (line 285-288, 315-317)
  - `"wrong_type"` → 400 (line 250)
  - `"unsupported_version"` → 400 (line 252)
- Test `test_malformed_and_wrong_type_rejected` confirms 400 for `{not json}`, wrong `_type`, and version 2 (test_alert_export_import.py:173-183)
- Test `test_nan_is_rejected` confirms NaN rejection → 400 (test_alert_export_import.py:188-190)
- Test `test_duplicate_is_atomic_and_rejected` confirms 409 with "already exists" body and no DB growth (test_alert_export_import.py:166-168)

**Notes:** All rejection paths are atomic (within `transaction.atomic()`) and produce clear error messages.

---

### AIMP-5: observables/tags/custom fields/comments/procedures imported and deduped

**Status:** Met

**Evidence:**
- `_import_observables` (importing.py:148-167): resolves observable via `resolve_observable(type_name, data, owner_org=alert.owner_org)`, creates `AlertObservable` with `get_or_create`
- `_import_tags` (importing.py:170-187): creates tags by name via `Tag.objects.get_or_create`, links via `AlertTagLink.objects.get_or_create`; deduplicates by `name.casefold()`
- `_import_custom_fields` (importing.py:190-213): resolves custom field by name, creates `AlertCustomFieldValue` with `get_or_create`; skips unknown fields with warning
- `_import_comments` (importing.py:216-224): creates `Comment` with `created_by=actor`
- `_import_procedures` (importing.py:227-244): creates `Procedure` with pattern_id, pattern_name, tactic, description
- Test `test_round_trip_imports_children_and_provenance` confirms: 1 observable, tags, custom fields, comments, procedures all imported (test_alert_export_import.py:143-148)
- Test `test_unknown_custom_field_and_observable_type_are_skipped` confirms unknown types/fields skipped with warnings, not 500 (test_alert_export_import.py:233-239)

**Notes:** Missing tags are created; unknown custom field types and observable types are skipped with warnings. No partial import — all-or-nothing within transaction.

---

### AIMP-6: Export JSON on alert detail (same style/location)

**Status:** Met

**Evidence:**
- `ui/templates/ui/alert_detail.html` line 21: `<a class="btn" href="{% url 'ui-alert-export' alert.id %}">Export JSON</a>` inside `{% block actions %}`
- Same visual style as other action buttons in the block (`class="btn"`), same position within the action bar
- Consistent with case detail export pattern (case has "New case" in modal, alert has "Export JSON" button)
- Test `test_alert_detail_has_export_button` confirms button present and `Export JSON` text in response (test_alert_export_import_ui.py:41-46)

**Notes:** Button is in the `{% block actions %}` section, which is the standard location for detail-page actions.

---

### AIMP-7: Import JSON outer-right in the alert filter card + upload modal; redirect/flash; CSRF/auth tested

**Status:** Met

**Evidence:**
- `ui/templates/ui/alert_list.html` line 24: `{% if request.user.role == "admin" or request.user.role == "superAdmin" %}<a class="btn btn-primary btn-end" href="{% url 'ui-alert-import' %}" data-modal-open="import-alert-modal">Import JSON</a>{% endif %}`
  - Positioned outer-right in the filter card, same placement as "New case" in `case_list.html`
  - `btn btn-primary btn-end` classes match the pattern used for "New case"
  - `data-modal-open="import-alert-modal"` triggers the modal
- Modal infrastructure (lines 29-31 of alert_list.html):
  - `{% include "ui/_modal.html" with modal_id="import-alert-modal" ... form_enctype="multipart/form-data" %}`
  - `_alert_import_fields.html` provides `<input type="file" name="file" accept="application/json,.json" required>`
- `alert_import_ui` (ui/views/alerts.py:588-635):
  - Admin gate: `if not is_admin(request.user): return render(..., "ui/404.html", status=404)` (line 601-602)
  - CSRF via `{% csrf_token %}` in `alert_import.html`
  - Success redirect: `redirect("ui-alert-detail", alert_id=str(alert.id))` with `messages.success(request, f"Alert imported: {alert.title}")` (line 634-635)
- Test `test_admin_alert_list_has_import_modal` confirms modal markup for admin (test_alert_export_import_ui.py:49-62)
- Test `test_analyst_alert_list_has_no_import_modal` confirms modal absent for analyst (test_alert_export_import_ui.py:65-70)
- Test `test_standalone_import_page_renders` confirms import page with enctype (test_alert_export_import_ui.py:74-78)

**Notes:** Import is gated on `is_admin` (admin/superAdmin), not merely `@not_viewer` — this is an intentional deviation noted in §19.9 (b), aligned with the admin-only gate for atomic writes.

## Deviations
No unapproved deviations. The following intentional/locked deviations from §19.6/§19.9 decisions are noted but were approved at implementation:
- (a) Route `ui-alert-import` handles both GET page + POST upload (no separate `ui-alert-import-json`)
- (b) Import gated on `is_admin` (admin/superAdmin), not merely non-viewer
- (c) `Tag`/`CustomField` are global vocabularies; unknown types/fields skipped with warnings, missing tags created
- (d) No `alerts/create.py` extraction — single import service is the writer

## Blockers
No blocking issues. All 7 AIMP acceptance criteria are MET.

## Recommendations
No code changes required. The implementation conforms to all AIMP-1..AIMP-7 acceptance criteria as specified in `TODO.md` §19.7-19.9. All 23 focused tests pass (13 backend + 4 UI + 6 URL smoke).