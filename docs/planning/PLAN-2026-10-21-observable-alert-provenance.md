# Plan — Observables extracted from alerts need to reference which alert they came from

**Date:** 2026-10-21  
**Status:** APPROVED — ready for implementation  
**Owner:** Planner/Tech Lead  
**Scope:** Persist alert provenance for observables that originated from alerts and display it in the Case Observables tab and Alert detail.

## Decisions (from user)
1. **Source column** in the Artifacts table
2. If multiple alerts contributed, show **most recent alert** + note **"N other alerts"**
3. **Backfill** `AlertObservable` for observables already accepted from suggestions
4. Show alert provenance in **Alert detail view** and also show **alert verdict value** there
5. Visible to **all roles** that can see the Observables tab
6. Wording: **"alert #N"**

## Current state (verified)
- `observables/models.py` `ObservableSuggestion` has `source_alert` (FK to `alerts.Alert`), unique on `(owner_org, data_type, normalized_data, case, source_alert)`
- `alerts/models.py` `AlertObservable` exists (alert <-> observable join) with per-link tags/added_by
- Suggestions UI already shows provenance
- On acceptance (`ui/views.suggestion_accept` and API `observables/views.suggestion_accept`) we create/link `Observable` + `CaseObservable` but **do not** create `AlertObservable`
- Case Observables tab has no alert provenance column
- Manual `add_observable` must remain unchanged

## Goals
- **G1 Persist:** When accepting a suggestion with `source_alert`, create `AlertObservable(alert, observable)` if missing. Ensure alert extraction/escalation paths also create it where appropriate.
- **G2 Display in Case Observables:** Add a **Source** column showing most recent alert + "N other alerts" if multiple; link to alert detail.
- **G3 Display in Alert detail:** Show which cases/observables came from this alert, and display the **alert verdict value**
- **G4 Backfill:** Create `AlertObservable` rows for already-accepted suggestions that have `source_alert`
- **G5 Preserve invariants:** Global dedup of Observable, org scoping, manual add unchanged, no behavior change to triggers

## Implementation

### P1 – Persist alert provenance on acceptance (backend)
1. **UI accept** (`ui/views.py:suggestion_accept`): if `suggestion.source_alert_id`, do `AlertObservable.objects.get_or_create(alert=suggestion.source_alert, observable=observable)` in the same transaction.
2. **API accept** (`observables/views.py:suggestion_accept`): same.
3. **Bulk accept** (`ui/views.suggestions_bulk_accept`, `observables/views.suggestions_bulk_accept`): handle per-suggestion.
4. **Alert extraction/escalation** (`alerts/escalation.py`): if observables are linked directly, ensure `AlertObservable` created when coming from alert context. With staging flow most go through suggestions, but be defensive.
5. **Imports:** add `AlertObservable` where needed.

**AC P1:** Accepting suggestion from alert creates `AlertObservable` row; idempotent.

### P2 – Backfill (data migration or management command)
1. Create a **data migration** in `observables/migrations/` (or `alerts/migrations/`) or a management command. Prefer migration for deploy simplicity.
2. Find all `ObservableSuggestion` with `status='accepted'` and `source_alert_id IS NOT NULL` and `observable_id IS NOT NULL` where `AlertObservable(alert=source_alert, observable=observable)` doesn't exist → create it.
3. Idempotent, org-safe.

**AC P2:** Existing accepted suggestions have `AlertObservable` rows created.

### P3 – Case Observables UI (frontend)
1. **View:** In `ui/views.py:case_detail`, prefetch alert provenance for observables on the case. Build a structure mapping `observable_id -> {latest_alert, other_count, alerts[]}` using `observable.alert_observables.select_related('alert').order_by('-alert__created_at')` or similar. Consider performance.
2. **Template:** Update `ui/templates/ui/_tab_observables.html` to add **Source** column after "Seen in" (or as specified). For each observable: show `alert #N` linking to `ui-alert-detail` for most recent, and if `other_count > 0` show `"N other alerts"` (could be a tooltip or small text). Visible to all roles.
3. **Styles:** Add minimal CSS if needed (keep existing classes).

**AC P3:** Source column shows most recent alert + "N other alerts" when multiple; links work; visible to all roles.

### P4 – Alert detail view (backend + frontend)
1. **Backend:** Update `alerts/views.py` alert detail to include observables/cases provenance and **alert verdict value**.
   - Show observables linked via `AlertObservable` (and/or observables from linked case)
   - Display verdict: check `Alert.verdict` field/model (look up existing usage)
2. **Template:** Update alert detail template to show observables list with cases, and display verdict value clearly.
3. **Permissions:** follow existing alert visibility rules.

**AC P4:** Alert detail shows verdict and lists observables/cases it contributed to.

### P5 – Tests & verification
- Test accepting suggestion from alert creates AlertObservable
- Test backfill creates missing rows
- Test UI shows source column correctly
- Test alert detail shows verdict and provenance
- `make check` green

## Files to touch
- `ui/views.py` (accept + case_detail prefetch)
- `observables/views.py` (API accept)
- `alerts/views.py` (alert detail)
- `observables/migrations/` or `alerts/migrations/` (backfill)
- `ui/templates/ui/_tab_observables.html`
- `alerts/templates/` alert detail template
- `tests/conformance/` new tests

## Notes
- Use existing `AlertObservable` model (don't create new model)
- Multiple alerts possible → show most recent + count
- Manual add unchanged
- Org scoping required

## Deviations (recorded during implementation)

- **Alert identity is not a number.** The plan (Decision 6) called for wording "alert #N", but
  `Alert` has no `number` field — only `Case` does (`AllocatedNumberField`). Alerts are identified
  by UUID and title. The Source column and the suggestion provenance therefore link on the alert
  **title** (`alert: <truncated title>`), with the full title in the `title=` attribute. The
  "N other alerts" rollup is unchanged. A future alert-number field would allow the original
  wording without a template change.
- **Backfill lives in `observables/migrations/0009_...`**, not `alerts/migrations/`, because it
  reads `ObservableSuggestion` and writes `AlertObservable`; the dependency graph is declared
  explicitly (`alerts.0017`, `observables.0008`).
- **Alert detail is rendered by `ui/views.py::alert_detail`**, not `alerts/views.py`; the UI is a
  separate server-rendered surface from the DRF API.
