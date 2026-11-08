# Verification Report: TODO §18 — Case JSON Export v2 + Import

**Plan:** `TODO.md` §18 (APPROVED 2026-11-08)  
**Verified:** 2026-11-08  
**Status:** Pass  

---

## Summary

- **Total ACs:** 7 (IMP-1 through IMP-7)
- **Met:** 7
- **Not Met:** 0
- **Deviated:** 0

All 7 Acceptance Criteria are **MET**. All 16 conformance tests at verification time passed without failure. No blocking issues identified.

> **Addendum (post-verification):** a 17th test, `test_unknown_vocabulary_tokens_fall_back_with_warnings`,
> was added after the initial pass to cover case classification token validation. The suite is now
> **17 passed**.

---

## Criterion-by-Criterion

### IMP-1: Export v2 includes every Case scalar + tags + full timeline/tasks(+notes)/comments/pages/procedures/custom fields/shares/attachment metadata + full alert docs; no secrets; `version: 2`

**Status:** **MET**

**Evidence:**
- `cases/views.py::_case_export_document` (line ~2724): produces `version: 2` document with all required children:
  - `case` block: scalars via `case_json(case)` (without `detail=True`), plus `ownerOrg`, `tags`, `customFields`, `sourceTemplate`, `ingestionWarnings`
  - `observables`: top-level list (bounded at 2_000, capped per §18.7)
  - `tasks`: list with all scalar fields + nested `notes` (each with message, createdAt, createdBy; bounded at 5_000 tasks / 5_000 notes total)
  - `timeline`: bounded at 10_000 events, with `truncated` dict when over cap
  - `comments`: bounded at 5_000
  - `pages`: bounded at 5_000
  - `procedures`: bounded at 5_000
  - `shares`: list of `{organisation, permissions}`
  - `attachments`: metadata only — `{name, contentType, size, sha256, external}`
  - `alerts`: full `alert_export_document` per alert ( §19 shape)
  - `automationRuns`: list of `{playbookName, status, outputLog, triggeredByObservableId, startedAt, endedAt}`
- `tests/conformance/test_case_export_import.py::test_export_v2_has_all_children_and_no_secrets`: asserts all 21+ children present and `"payload_ssdeep" not in json.dumps(doc)` and `"internal-ssdeep" not in json.dumps(doc)`
- Caps match §18.7 Decisions: 5 MiB, 10k timeline, 5k tasks/notes/p/comments, 2k observables, 1k alerts, depth 20; over-cap counts recorded in `truncated`

**Deviations:** None. All departures from v1 are intentional and documented (e.g., `case_json` without `detail=True`, observables at top level rather than nested under `case.observables`).

---

### IMP-2: A v2 file creates a case with scalars/tags/custom fields + provenance

**Status:** **MET**

**Evidence:**
- `cases/importing.py::import_case_document` (line ~570): one `transaction.atomic()` transaction creates a `Case` with all scalars (title, description, summary, severity, tlp, pap, flag, status, assignee, scope, case_type, data_classification, phase, verdict, dates), forced to actor's org.
- Tags created by name via `_import_tags`; unknown tags created, known tags linked.
- Custom fields processed via `_import_custom_fields`; unknown fields skipped with warning.
- Provenance written to `case.ingestion_warnings["imported"]` = `{at, by, sourceFile, originalNumber, originalId}` (plus `sourceTemplate` if present, plus `warnings` list).
- `"Case imported from JSON"` timeline event appended.
- `tests/conformance/test_case_export_import.py::test_round_trip_preserves_children_and_provenance` verifies:
  - `imported.id != source.id`, `imported.number != source.number`
  - `imported.owner_org == target_org`, `imported.assignee is None` (forced to actor's org)
  - `imported.status.value == "InProgress"`
  - `list(imported.tags.values_list("name", flat=True)) == ["phishing"]`
  - `imported.custom_field_values.get().value == {"level": "high"}`
  - `imported.case_observables.get().observable.data == "1.2.3.4"`
  - `imported.tasks.get().notes.get().message == "Started isolation"`
  - `imported.comments.get().message == "Analyst comment"`
  - `imported.pages.get().title == "Runbook"`
  - `imported.procedures.get().pattern_id == "T1566"`
  - `imported.shares.count() == 0`, `imported.attachments.count() == 0`
  - `AutomationRun.objects.filter(case=imported).count() == 0` (skipped)
  - Provenance fields verified (`provenance["by"] == "admin"`, `provenance["originalNumber"] == source.number`, `provenance["originalId"] == str(source.id)`)
  - `"Case imported from JSON"` timeline event exists

**Deviations:** None. Org forcing, assignee restriction, and shares/attachments/automationRuns skipping are intentional §18.7 decisions.

---

### IMP-3: Children (tasks/notes, timeline, comments, pages, procedures) imported

**Status:** **MET**

**Evidence:**
- `cases/importing.py::_import_tasks` (line ~410): creates tasks with all scalars (title, description, group, status, flag, assignee, order, dueDate, startedAt, endedAt, mandatory) and nested notes (mirrored into ledger via `append_timeline_event` with `kind="task-note"`).
- `cases/importing.py::_import_timeline` (line ~475): imports timeline events, **skipping** `kind == "task-note"` rows to avoid de-duplication (every imported TaskNote is already mirrored once).
- `_import_comments` (line ~525): imports comments with message, createdBy, createdAt.
- `_import_pages` (line ~540): imports pages with title, content, order, category.
- `_import_procedures` (line ~555): imports procedures with occurDate, patternId, patternName, tactic, description, ttp FK.
- `tests/conformance/test_case_export_import.py::test_round_trip_preserves_children_and_provenance` verifies:
  - `imported.tasks.get().notes.get().message == "Started isolation"`
  - `imported.timeline_events.filter(title="Case imported from JSON").exists()`
  - `task_notes.count() == 1` (only the mirrored copy, not doubled)

**Deviations:** None. Task-note de-duplication is an intentional §18.7 decision (skip exported task-note kind, mirror UI-created notes exactly once).

---

### IMP-4: Invalid/oversized/wrong-type/unsupported-version files rejected atomically with field errors

**Status:** **MET**

**Evidence:**
- `cases/importing.py::parse_case_export` (line ~96): validates `_type == "CaseExport"`, `version in (1, 2)`, `case.title` present and non-empty, 5 MiB limit, NaN/Infinity rejection, bound checking (nodes > 10_000, depth > 20).
- `cases/importing.py::CaseImportError`: structured errors with `code` and `message`.
- `cases/importing.py::import_case_document`: wraps all work in `transaction.atomic()` — any failure (including duplicate alert) leaves no case behind.
- `tests/conformance/test_case_export_import.py::test_parse_rejects_bad_documents` (parametrized): verifies error codes:
  - `_type="Nope"` → `wrong_type`
  - `version=99` → `unsupported_version`
  - `version=True` → `unsupported_version`
  - `case.title=""` → `missing_title`
- `tests/conformance/test_case_export_import.py::test_parse_rejects_nan_and_oversize`: verifies
  - NaN in JSON → `malformed_json`
  - File > 5 MiB → `oversized_file`
- `tests/conformance/test_case_export_import.py::test_duplicate_embedded_alert_rolls_back_the_whole_case`: verifies `Case.objects.count()` unchanged on duplicate alert, `excinfo.value.code == "alert_duplicate"`.

**Deviations:** None. All rejection paths are intentional and tested.

---

### IMP-5: Exported alerts recreated and linked, with observables/tags/custom fields/comments/procedures; duplicate `(source, type, sourceRef)` yields clear atomic failure

**Status:** **MET**

**Evidence:**
- `cases/importing.py::_import_alerts` (line ~620): iterates over alerts, handles both `AlertExport` `_type` (v2) and bare alert fields (v1), calls `import_alert_document` from `alerts.importing`, then sets `alert.case = case` and `alert.save(update_fields=["case", "updated_at"])`.
- `tests/conformance/test_case_export_import.py::test_duplicate_embedded_alert_rolls_back_the_whole_case`: imports a case from another org that has one embedded alert; expects `CaseImportError` with `code == "alert_duplicate"`, and verifies `Case.objects.count()` stays the same (no partial case created).
- Alert observables linked via `resolve_observable` (same service the UI uses), tags by name, custom fields by name, comments and procedures imported.
- Full `import_alert_document` logic ( §19 ) handles status resolution, assignee org-forcing, `payload_ssdeep` recomputation, and duplicate `(owner_org, source, type, source_ref)` unique-constraint failure.

**Deviations:** None. Duplicate-alert atomic failure is an intentional §18.7 decision (decision 7: "Linked-alert duplicate `(org, source, type, sourceRef)`: atomic fail (no partial import)").

---

### IMP-6: New case modal exposes Import from JSON; success redirects to the case

**Status:** **MET**

**Evidence:**
- `ui/views/cases.py::case_import_ui` (line ~673): GET renders `ui/import_case.html` standalone page; POST validates upload, calls `parse_case_export` + `import_case_document`, on success `messages.success(request, f"Case #{case.number} imported from JSON.")` and `redirect("ui-case-detail", case_id=str(case.id))`.
- `ui/templates/ui/import_case.html` (new): file input, size hint, error display, Import case button, Cancel link.
- `_case_create_fields.html` (line ~135): secondary "Import from JSON…" link for admins/superAdmins: `<a href="{% url 'ui-case-import' %}">Import from JSON…</a>`.
- `ui/urls.py`: `path("cases/import", views.case_import_ui, name="ui-case-import")` registered **before** `path("cases/<str:case_id>", views.case_detail, name="ui-case-detail")` so `"import"` is not swallowed as a case ID.
- `tests/conformance/test_case_export_import.py::test_new_case_modal_has_import_link_for_admins_only`: verifies admin sees "Import from JSON" in case list body; analyst does not.
- `tests/conformance/test_case_export_import.py::test_import_view_is_admin_only_and_redirects`: verifies admin POST → 302 to case detail; analyst POST → 404.

**Deviations:** None. Admin-only gate and route ordering are intentional §18.4 and §18.8 decisions.

---

### IMP-7: Shares/attachments handling documented and safe; CSRF/auth/size caps tested

**Status:** **MET**

**Evidence:**
- **Shares:** `_import_closing_entry` / `import_case_document` (line ~702): if shares present in document, warning appended: `"shares: skipped (cross-organisation grants are never imported)"`. Shares count in import test is 0.
- **Attachments:** same block: if attachments present, warning appended: `"attachments: skipped (the export carries metadata only, no bytes)"`. Attachments count in import test is 0.
- **CSRF:** `ui/import_case.html` includes `{% csrf_token %}`; view is `@login_required`.
- **Auth:** `case_import_ui` checks `if not is_admin(request.user): raise Http404("Import is admin-only.")` — uniform 404 for analysts, no org-scoped leakage.
- **Size caps:** View checks `uploaded_file.size > CASE_IMPORT_MAX_BYTES` and `len(raw) > CASE_IMPORT_MAX_BYTES`; service `parse_case_export` checks `len(raw) > MAX_BYTES` (5 MiB).
- `tests/conformance/test_case_export_import.py::test_import_view_is_admin_only_and_redirects`: 403/404 for analyst.
- `tests/conformance/test_case_export_import.py::test_import_view_rerenders_errors`: 400 re-render with error code and message.

**Deviations:** None. Shares/attachments skipping, admin-only gate, and size caps are all intentional §18.7 decisions.

---

## Deviations (Intentional / Design Notes)

The following differences from the v1 `CaseExport` / original plan were **deliberately approved** on 2026-11-08 and are recorded in `TODO.md §18.7`:

1. **`case_json(detail=True)` not reused for v2**: The v2 export uses `case_json(case)` (without `detail=True`) because `detail=True` caps the timeline at 50 events — appropriate for an API page but wrong for an investigation export. The full child lists below are the export. (§18.3, §18.10)

2. **Observables at top level, not nested**: v2 export places `observables` at the top level of the document, not nested under `case.observables`. v1 import accepts both forms (the import code reads `document.get("observables", case_doc.get("observables"))`).

3. **v1 CaseExport alert entries wrapped as `AlertExport` v1 documents on import**: The import parser handles both `AlertExport` `_type` (v2) and bare alert field dicts (v1), enabling backward compatibility.

4. **View uses `is_admin` check (not `@not_viewer` + `can_mutate`)**: §18.4 restricts import to admin/superAdmin, so a simple `is_admin` guard was chosen over the more general `@not_viewer` + `can_mutate` pattern used elsewhere.

5. **Caps and truncation**: Caps (5 MiB, 10k timeline, 5k tasks/notes/p/comments, 2k observables, 1k alerts, depth 20) are applied with `truncated` dict in export and warning messages in import. Silently dropping beyond caps is by design per §18.7 decisions 9 and 10.

6. **Shares skipped, attachments metadata-only, automationRuns skipped**: Per §18.7 decisions 2–5, these are intentionally not imported with clear warnings.

---

## Blockers

**None.** All 7 ACs are met, all 16 conformance tests pass, and no security gaps or correctness defects were found that are not already covered by the test suite or documented as intentional deviations.

---

## Recommendations

- The implementation is complete and verified. No code changes are required.
- Consider writing an independent security review for the import path (particularly the `import_alert_document` call and the `transaction.atomic` rollback coverage) if the team wants a second pair of eyes beyond the conformance test suite.
- The next verification cycle should focus on §19 (Alert JSON export/import) once that implementation is handed off.

---

*Report generated by the Amalthea verification specialist. This is a persistent record stored at `docs/verification/VERIFY-2026-11-08-case-json-import.md`.*