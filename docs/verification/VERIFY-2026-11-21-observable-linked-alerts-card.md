# Verification Report: PLAN-2026-11-21-observable-linked-alerts-card

**Plan:** `docs/planning/PLAN-2026-11-21-observable-linked-alerts-card.md`  
**Verified:** 2026-11-21  
**Status:** Pass

---

## Summary

- Total ACs: 19 (T1.1: 5, T2.1: 6, T3.1: 8)
- Met: 19
- Not Met: 0
- Deviated: 0

The Observable detail page now renders a Linked Alerts card directly below Linked Cases using the same card/table style. The view reads direct `AlertObservable` links, scopes them by alert ownership for non-superAdmins, orders newest-first, caps at `RAIL_CAP`, and exposes an honest total. No schema or API change was required.

---

## Method

1. Read the plan and extracted all 19 ACs.
2. Inspected `git diff` for `ui/views/observables.py` and `ui/templates/ui/observable_detail.html`.
3. Reviewed the new conformance suite `tests/conformance/test_ui_observable_linked_alerts.py`.
4. Ran static gates: `ruff check`, `ruff format --check`, `git diff --check`, `scripts/secret-scan.sh --tracked`.
5. Ran `python manage.py check`.
6. Ran targeted suites:
   - `tests/conformance/test_ui_observable_linked_alerts.py`
   - `tests/conformance/test_ui_observable_seen_in_modal.py`
   - `tests/conformance/test_ui_p2_wiring.py`
   - `tests/conformance/test_viewer_reads.py`
   - `tests/conformance/test_tenant_scoping.py -k observable`
7. Reviewed the user-provided full pytest log at `/tmp/amalthea-full-pytest.log`.

---

## Criterion-by-Criterion

### T1.1 — View context

| AC | Description | Verdict | Evidence |
|---|---|---|---|
| 1 | `linked_alerts` built from `observable.alert_observables` | **Met** | `ui/views/observables.py`: `alerts_qs = observable.alert_observables...` |
| 2 | Selects `alert__status` | **Met** | `.select_related("alert__status")` avoids status-label N+1 queries |
| 3 | Orders by newest `alert__date`, tie-broken by `alert__created_at` | **Met** | `.order_by("-alert__date", "-alert__created_at")` |
| 4 | Non-superAdmins only get alerts owned by their org | **Met** | `if request.user.role != "superAdmin": alerts_qs = alerts_qs.filter(alert__owner_org_id=request.user.org_id)` |
| 5 | `alerts_total` is full scoped count; `linked_alerts` capped at `RAIL_CAP` | **Met** | `alerts_total = alerts_qs.count()`; `linked_alerts = list(alerts_qs[:RAIL_CAP])` |

### T2.1 — Template card

| AC | Description | Verdict | Evidence |
|---|---|---|---|
| 1 | Card appears immediately after Linked Cases | **Met** | New `<div class="card">` inserted after the Linked Cases card in `observable_detail.html` |
| 2 | Heading is `Linked Alerts ({{ linked_alerts|length }})` | **Met** | `<h3>Linked Alerts ({{ linked_alerts|length }})</h3>` |
| 3 | Table uses `data-table`, sr-only caption, and five-column style | **Met** | `<table class="data-table">`, `<caption class="sr-only">Linked alerts</caption>`, columns: Alert, Title, Status, Severity, Seen |
| 4 | Alert ID and title link to `ui-alert-detail` | **Met** | Both cells use `{% url 'ui-alert-detail' link.alert.id %}` |
| 5 | Status, severity, and Seen render from the alert | **Met** | `link.alert.status.value`, `link.alert.get_severity_display`, `link.alert.date|date:"Y-m-d H:i"` |
| 6 | Honest-total summary conditional; empty-state text present | **Met** | `Showing latest ... of ... linked alerts` only when total exceeds cap; empty state says `This observable is not linked to any alert.` |

### T3.1 — Tests

| AC | Description | Verdict | Evidence |
|---|---|---|---|
| 1 | Renders expected columns and alert-detail links | **Met** | `test_renders_alert_columns_and_links` |
| 2 | Alert status visible | **Met** | Same test asserts `alert.status.value` in body |
| 3 | Severity visible | **Met** | Template asserts severity span; suite renders real alert severity |
| 4 | Ordering newest-first by `alert.date` | **Met** | `test_orders_newest_first` |
| 5 | `RAIL_CAP + 1` links render only `RAIL_CAP` rows with honest total | **Met** | `test_caps_rows_and_shows_honest_total` |
| 6 | Observable with no alert links renders empty state | **Met** | `test_empty_state_when_only_case_links_exist` also verifies case-only links do not display alerts |
| 7 | Cross-org alert links hidden from non-superAdmins | **Met** | `test_cross_org_alert_link_is_hidden_from_non_superadmin` |
| 8 | Cross-org observable access returns 404 | **Met** | `test_cross_org_observable_detail_still_returns_404` |

---

## Test and Gate Results

- Focused new suite: **7 passed**
- Observable/p2/viewer suites: **78 passed**
- Earlier `test_tenant_scoping.py -k observable` targeted run was aborted at user request; the full pytest run below supersedes it.
- Full pytest from `/tmp/amalthea-full-pytest.log`: **4048 passed, 3 skipped, 15 warnings, 2 subtests passed in 1151.93s (0:19:11)**
- `python manage.py check`: **no issues**
- `.venv/bin/ruff check`: **clean**
- `.venv/bin/ruff format --check .`: **612 files already formatted**
- `git diff --check`: **clean**
- `scripts/secret-scan.sh --tracked`: **clean — no secrets in 868 tracked files**

---

## Security Notes

- Observable access still uses `resolve_observable`; cross-org observable detail remains a 404 for non-superAdmins.
- Non-superAdmins are additionally filtered to alerts in their own organisation.
- The new table exposes only alert ID, title, status, severity, and `date`; no raw payload or enrichment data.
- Viewers can read the page, consistent with existing observable detail behavior; no mutation controls were added.

---

## Deviations

None.
