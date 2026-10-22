# BRIEF — 2026-10-21 — Observables extracted from alerts need to reference which alert they came from

**For:** `django-backend` (P1–P2, P4 backend), `django-frontend` (P3, P4 UI)  
**Plan:** `docs/planning/PLAN-2026-10-21-observable-alert-provenance.md` (APPROVED)  
**Verifier:** `verifier` (P5)

## Goals
Persist alert provenance using existing `AlertObservable` when accepting suggestions from alerts; show in Case Observables as a **Source** column (most recent alert + "N other alerts"); backfill existing accepted suggestions; show alert provenance and **verdict value** in Alert detail.

## Decisions
1. Source column
2. Most recent alert + "N other alerts" if multiple
3. Backfill
4. Show in alert detail + verdict value
5. All roles that can see Observables tab
6. Wording "alert #N"

## Ground truth
- `alerts/models.py` `AlertObservable(alert, observable, added_by, tags, ...)` exists
- `observables/models.py` `ObservableSuggestion.source_alert`
- Accept paths: `ui/views.py:suggestion_accept`, `observables/views.py:suggestion_accept`, bulk accept
- Case observables template: `ui/templates/ui/_tab_observables.html`
- Alert detail: `alerts/views.py`, alert templates
- Manual `add_observable` unchanged

## P1 – Persist on acceptance (backend)
1. `ui/views.py:suggestion_accept` — in transaction after linking, if `suggestion.source_alert_id` create `AlertObservable.objects.get_or_create(alert=suggestion.source_alert, observable=observable)`
2. `observables/views.py:suggestion_accept` — same
3. Bulk accept (UI + API) — same per suggestion
4. `alerts/escalation.py` — defensive if direct linking happens
5. Idempotent, org-safe

## P2 – Backfill (backend)
Create data migration (prefer migration) to add `AlertObservable` rows for accepted suggestions with `source_alert` and `observable` not already linked.

## P3 – Case Observables UI (frontend)
1. `ui/views.py:case_detail` — prefetch `observable__alert_observables__alert` (order by alert created_at desc) and build map `obs_id -> {latest_alert, other_count}`
2. `ui/templates/ui/_tab_observables.html` — add **Source** column. Show `alert #N` linked to `ui-alert-detail` for latest; if `other_count > 0` show `N other alerts`. All roles.

## P4 – Alert detail (backend + frontend)
1. `alerts/views.py` — include observables via `AlertObservable` (and cases), include verdict value
2. Alert detail template — show verdict and observables/cases provenance

## P5 – Tests & verify
Tests for accept creating AlertObservable, backfill, UI source column, alert detail verdict/provenance. `make check` green.

## Constraints
- Use existing `AlertObservable`
- Manual add unchanged
- Org scoping
- Multiple alerts supported
