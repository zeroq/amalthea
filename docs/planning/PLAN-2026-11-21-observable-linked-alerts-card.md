# PLAN-2026-11-21 — Observable Detail Linked Alerts Card

**Status:** Implemented — verification Pass  
**Owner:** Planning / Tech Lead  
**Target:** `ui-observable-detail` (`/observables/<observable_id>`)  
**References:** `AGENTS.md` §2 Module C/§6, ADR-004 tenant ownership, `docs/planning/PLAN-2026-11-20-agent-access-credential-actions.md` for current uncommitted work

## Summary

The Observable detail page already renders a **Linked Cases** card fed by `observable.case_observables`. There is also a direct, indexed `AlertObservable` join model with `observable.alert_observables`, explicitly built for observable→alert fan-out. Add a **Linked Alerts** card immediately below Linked Cases, styled and structured like the existing table.

The card will show the alerts directly linked to the observable, newest first, capped at `RAIL_CAP` with an honest total line, and linked to `ui-alert-detail`.

## Goals

- Add a `Linked Alerts (N)` card directly after the Linked Cases card.
- Reuse the Linked Cases table classes, heading style, cap/summary pattern, and empty-state treatment.
- Use the existing direct `AlertObservable` relation; no schema change.
- Preserve tenant scoping: users only see alerts they are allowed to read.
- Add conformance coverage for rendering, ordering, capping, empty state, and scoping.

## Non-goals

- No changes to alert ingestion, extraction, escalation, or observable linking logic.
- No new API endpoint or serializer fields.
- No pagination controls; use the same `RAIL_CAP` + honest-total pattern as Linked Cases.
- No inclusion of alerts linked only through a shared case unless they also have a direct `AlertObservable` row.
- No changes to the Linked Cases card itself.

## Architecture

Stack remains Django template + view context; no JS or HTMX required.

- View: `ui/views/observables.py::observable_detail`.
- Template: `ui/templates/ui/observable_detail.html`.
- Source relation: `alerts.models.AlertObservable` via `observable.alert_observables`.
- Alert detail route: `ui-alert-detail`, kwarg `alert_id`.
- Cap: `ui.pagination.RAIL_CAP` (currently 200).

Rationale: the index `alertobs_obs_alert_idx (observable, alert)` already supports this access pattern, and the view already applies `resolve_observable`, so the new rail needs only an alert-visibility filter plus ordering/capping.

## Data Model

No migration or model change.

Relevant relation:

```python
observable.alert_observables  # AlertObservable rows; use link.alert
```

Columns to render:

| Column | Source |
|---|---|
| Alert | `link.alert.id` shortened/truncated, linked to `ui-alert-detail` |
| Title | `link.alert.title`, truncated to 50 chars, linked to `ui-alert-detail` |
| Status | `link.alert.status.value` in a `pill sev-*` span |
| Severity | `link.alert.get_severity_display` in a `sev sev-*` span |
| Seen | `link.alert.date`, formatted `Y-m-d H:i` |

## Query / Context

In `observable_detail()` after the linked-cases query:

```python
alerts_qs = observable.alert_observables.select_related("alert__status").order_by(
    "-alert__date", "-alert__created_at"
)
if request.user.role != "superAdmin":
    alerts_qs = alerts_qs.filter(alert__owner_org_id=request.user.org_id)
alerts_total = alerts_qs.count()
linked_alerts = list(alerts_qs[:RAIL_CAP])
```

Pass `linked_alerts` and `alerts_total` through `_base_context`.

Notes:
- Use distinct context names (`linked_alerts`, `alerts_total`) to avoid colliding with nav badge context.
- `resolve_observable` already guarantees the observable itself is readable; the extra alert filter prevents any cross-org alert link from being disclosed.
- `select_related("alert__status")` avoids N+1 queries for status labels.

## UI / UX

Add this immediately after the Linked Cases `</div>` in `observable_detail.html`:

```django
<div class="card">
  <h3>Linked Alerts ({{ linked_alerts|length }})</h3>
  {% if linked_alerts and alerts_total is not None and alerts_total > linked_alerts|length %}
    <p class="list-summary dim">Showing latest {{ linked_alerts|length }} of {{ alerts_total }} linked alerts</p>
  {% endif %}
  {% if linked_alerts %}
  <div class="table-wrap">
  <table class="data-table">
    <caption class="sr-only">Linked alerts</caption>
    <thead>
      <tr>
        <th scope="col">Alert</th>
        <th scope="col">Title</th>
        <th scope="col">Status</th>
        <th scope="col">Severity</th>
        <th scope="col">Seen</th>
      </tr>
    </thead>
    <tbody>
          {% for link in linked_alerts %}
          <tr data-linked-alert-id="{{ link.alert.id }}">
            <td class="mono"><a href="{% url 'ui-alert-detail' link.alert.id %}">{{ link.alert.id|stringformat:"s"|truncatechars:8 }}</a></td>
        <td><a href="{% url 'ui-alert-detail' link.alert.id %}">{{ link.alert.title|truncatechars:50 }}</a></td>
        <td><span class="pill sev-{{ link.alert.severity }}">{{ link.alert.status.value }}</span></td>
        <td><span class="sev sev-{{ link.alert.severity }}">{{ link.alert.get_severity_display }}</span></td>
        <td>{{ link.alert.date|date:"Y-m-d H:i" }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="empty">This observable is not linked to any alert.</p>
  {% endif %}
</div>
```

Dark theme, text labels, and status/severity colour classes match the Linked Cases card exactly.

## Phases and Tasks

### Phase 1 — View context

**T1.1 Add scoped linked-alert queryset**

File: `ui/views/observables.py`

AC:
- `observable_detail` builds `linked_alerts` from `observable.alert_observables`.
- Query selects `alert__status` and orders by newest `alert__date`, tie-broken by `alert__created_at`.
- Non-superAdmins only get alerts owned by their org.
- `alerts_total` is the full scoped count before capping.
- `linked_alerts` is capped at `RAIL_CAP`.

### Phase 2 — Template card

**T2.1 Render Linked Alerts card**

File: `ui/templates/ui/observable_detail.html`

AC:
- New card appears immediately after Linked Cases.
- Heading is `Linked Alerts ({{ linked_alerts|length }})`.
- Table uses `data-table`, `<caption class="sr-only">Linked alerts</caption>`, and the same five-column style as Linked Cases.
- Alert ID and title link to `ui-alert-detail`.
- Status, severity, and `Seen` values render from the alert.
- Honest-total summary appears only when `alerts_total > linked_alerts|length`.
- Empty state says `This observable is not linked to any alert.`

### Phase 3 — Tests

**T3.1 Add conformance coverage**

File: `tests/conformance/test_ui_observable_linked_alerts.py` (new)

AC:
- Linked alerts render with the expected columns and alert-detail links.
- Alert status and severity are visible.
- Ordering is newest-first by `alert.date`.
- `RAIL_CAP + 1` links render only `RAIL_CAP` rows and show the honest total.
- Observable with no alert links renders the empty state.
- Case-only observables do not display alerts.
- Cross-org alert links are not disclosed to non-superAdmins.
- Cross-org observable access still returns 404 for non-superAdmins.

## Security

- Reuse `resolve_observable`; no change to observable access rules.
- Filter linked alerts by `alert__owner_org_id` for non-superAdmins.
- No raw payload or enrichment data is exposed by the new table.
- Alert ID is already visible elsewhere in the UI; no new sensitive identifier is introduced.
- Viewer role can read the rail; no mutation affordances are added.

## Testing

Targeted suite:

- `tests/conformance/test_ui_observable_linked_alerts.py`
- `tests/conformance/test_ui_p2_wiring.py`
- `tests/conformance/test_viewer_reads.py`
- `tests/conformance/test_tenant_scoping.py -k observable`

Static gates:

- `.venv/bin/ruff check` on touched Python files
- `.venv/bin/ruff format --check` on touched Python files
- `scripts/secret-scan.sh --tracked`
- No full pytest unless explicitly requested.

## Risks

- **Column ambiguity:** Linked Cases uses Created; alerts carry both `created_at` and event `date`. Recommended default is `Seen` using `alert.date`, matching the alert list.
- **Cross-org sharing:** observables can be shared across cases, but direct alert links should be filtered by alert ownership. Tests must cover this.
- **Context name collision:** use `linked_alerts`, not `alerts`, to avoid confusing base-context alert counts.
- **Cap semantics:** keep the same honest-total pattern as Linked Cases so large fan-out is not silently hidden.

## Open Questions / Defaults Used

1. **Direct links only?** Recommended/default: yes — only `AlertObservable`, not case-derived alerts.
2. **Columns?** Recommended/default: Alert, Title, Status, Severity, Seen (`alert.date`).
3. **Cap and honest total?** Recommended/default: yes, reuse `RAIL_CAP`.
4. **Cross-org alerts?** Recommended/default: hide from non-superAdmins via `alert__owner_org_id`.
5. **Source column?** Recommended/default: no, keep exactly five columns like Linked Cases.

## Verification Record

- Verification report: `docs/verification/VERIFY-2026-11-21-observable-linked-alerts-card.md`
- Status: **Pass** — 19/19 ACs met, 0 deviations.
- Full pytest: `4048 passed, 3 skipped, 15 warnings, 2 subtests passed in 1151.93s (0:19:11)`.
- Targeted observable/p2/viewer suites: `78 passed`; new suite: `7 passed`.
- Static checks, Django check, `git diff --check`, and secret scan all clean.

## Implementation Brief

Primary files:
- `ui/views/observables.py`
- `ui/templates/ui/observable_detail.html`
- `tests/conformance/test_ui_observable_linked_alerts.py` (new)

Dependencies:
- Existing `AlertObservable` model and index.
- Existing `ui-alert-detail` route.
- Existing `RAIL_CAP`.
- No migration.

Deviations:
- Record any deviation from the defaults above here before merge.
