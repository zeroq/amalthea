# Implementation Brief — "Similar alerts" card on the Case Details → Similar tab

**Plan:** `docs/planning/PLAN-2026-10-20-similar-alerts-card.md` (APPROVED 2026-10-20; §9 decisions locked)
**Status:** APPROVED — ready to implement. **No schema migration. No API change. No new model.**
**Agents:** `django-backend` (Phase A: correlation helper + view + route) · `django-frontend`
(Phase B: card + link form + AC-11 status-label fixes) · `qa-tester` (Phase C tests).

> Any deviation from this brief must be **recorded in the plan §11.1 Deviations** and reported back.
> Do not silently diverge.

---

## 0. Context (read these first)

- `ui/views.py:_case_detail` — the Similar tab block (~1047-1068) runs **only when
  `active_tab == "similar"`**. It builds `Finding`s from the case's `CaseObservable`s (excluding
  `ignore_similarity`) and calls `cases_sharing_findings(findings, user=request.user, exclude_case=case)`.
  The context is assembled in the `_base_context(...)` call (~1105-1160); `similar_cases` /
  `similar_total` are passed there.
- `cases/correlation.py` — read in full.
  - `cases_sharing_findings(...)` (case-side grouped query).
  - `candidate_cases_for_alert(alert, *, user, limit=None)` — the **alert→case** algorithm to invert:
    dry-run `find_observables(alert.title, json_leaves(alert.raw_payload))` + a synthetic finding from
    `alert.correlation_key` (`type_map` maps `dest_ip`/`src_ip`→`ip`, `domain`→`fqdn`, etc.).
  - `SimilarCase` dataclass; `MAX_CORRELATION_OBS = 1000` bound; `record_alert_bundled(...)`.
  - Imports `org_q` is **not** currently imported here — use `identity.scoping.org_q` (module-level
    `from identity.scoping import case_visible_q` is done *inside* functions — match that local style).
- `alerts/models.py` — `Alert` (`case` FK nullable, `owner_org` FK, `correlation_key` indexed,
  `status` FK with `.value`/`.stage`, `severity`, `date`, `title`, `summary`, `raw_payload`),
  `AlertObservable` (`related_name="alert_observables"`, index `(observable, alert)`).
- `alerts/escalation.py:merge_alert_into_case(alert, case, *, actor)` — the linking verb (sets
  `alert.case`, status→`Imported`, extracts observables, appends ledger). Raises nothing for the
  happy path; both it and `record_alert_bundled` **refuse** closed case / cross-org / viewer actor.
- `ui/views.py:alert_merge` (~524) — the **exact pattern** for the new POST view: `@not_viewer
  @login_required @require_POST`, `resolve_alert`/`resolve_case` with `write=True`, `transaction.atomic`
  around `merge_alert_into_case(...)` + `record_alert_bundled(..., reason=…, source="merge")`,
  then `_case_detail_redirect(...)`.
- `ui/templates/ui/_tab_similar.html` — the existing card (mirror its structure).
- `_tab_alerts.html` — the alerts table markup to mirror for the new card's rows
  (`sev_class`/`sev_label` filters, `pill pill-{{ alert.status.stage|lower }}`, `linkish`).
- `identity/scoping.py:org_q(field, user)` — own-org / superAdmin-all. Used for alerts.
- `ui/pagination.py` — `RAIL_CAP = 200`, `PICKER_PAGE_SIZE = 200`.
- `_case_detail_redirect(request, case_number, tab=...)` — **verify** whether it accepts a `tab`
  argument; if not, add `?tab=similar` support or extend it (document the choice).

---

## 1. Phase A — backend

### A1 — `alerts_similar_to_case` in `cases/correlation.py`  → AC-2, AC-5, AC-7, AC-8

Add, next to `candidate_cases_for_alert`:

```python
@dataclass(frozen=True)
class SimilarAlert:
    """An unlinked alert whose signals overlap this case's attached alerts."""

    alert: Alert
    shared_count: int
    shared: list[tuple[str, str]]  # ordered (type_name, value)
```

```python
def alerts_similar_to_case(
    case: Case, *, user: User, limit: int | None = None
) -> tuple[list[SimilarAlert], int]:
    """Rank UNLINKED, same-org alerts by signal overlap with `case`'s attached alerts.

    Signals (Q1/Q2): an unlinked alert is a candidate when it overlaps the signals of ANY alert
    already attached to `case`. Signals are drawn from (a) non-empty `correlation_key`s, and
    (b) payload-extracted findings (`find_observables(alert.title, json_leaves(alert.raw_payload))`),
    mirroring `candidate_cases_for_alert`. Extraction is bounded and never raises on garbage payloads.
    """
```

**Algorithm (implement exactly):**

1. `from ui.pagination import RAIL_CAP`; `limit = RAIL_CAP if limit is None else limit`.
2. Build the **case's alert signal set** from every alert attached to the case
   (`Alert.objects.filter(case=case)` — bound the extraction pass at a constant, e.g.
   `MAX_CORRELATION_OBS`; document the cap):
   - `keys: set[str]` = non-empty `correlation_key`s.
   - `findings: set[tuple[str,str]]` = `(type_name, normalized)` for extracted findings from each
     attached alert's `title` + `summary` + `json_leaves(raw_payload)`, using the same
     `ObservableType` vocabulary dict as `candidate_cases_for_alert`, guarded by
     `WEBHOOK_MAX_BODY_SIZE`/`WEBHOOK_MAX_DEPTH` and `except (Exception, RecursionError, MemoryError)`.
   - Also add the case's own observable findings? **NO** — Q1: basis is the case's *attached alerts*.
3. If `keys` and `findings` are both empty → return `([], 0)` (nothing to compare).
4. Candidates: `Alert.objects.filter(case__isnull=True).filter(org_q("owner_org", user))
   .select_related("status", "owner_org")` (Q4 strictly unlinked; org-scoped AC-5).
5. **Cheap pre-filter:** if `keys` is non-empty, restrict candidates to
   `Q(correlation_key__in=keys) | <package of payload candidates>`; because a blank
   `correlation_key` alert can still match via payload, also keep candidates whose `correlation_key`
   is blank **only if** the payload pass is affordable — implement as: take candidates matching the
   key OR with `correlation_key=""`, bound the number of payload extractions at the constant.
6. For each candidate, extract findings (same bounded dry-run) + include its non-empty
   `correlation_key` as a synthetic finding; compute the intersection with the case's signal set;
   `shared_count = len(intersection)`, `shared = sorted((type_name, value))`.
7. Keep only alerts with `shared_count > 0`. Rank `shared_count` desc, then `-date`.
8. Return `(rows[:limit], total_matching)`.

- [ ] AC-2: an unlinked same-org alert sharing a `correlation_key` or an extracted artifact with an
      attached alert appears; a non-overlapping one does not.
- [ ] AC-5: an alert in another org never appears (org filter).
- [ ] AC-7: an already-linked alert (`case_id` not null) never appears.

### A2 — context in `_case_detail`  → AC-1, AC-8

Inside the existing `if active_tab == "similar":` block, after computing `similar_cases`:

```python
similar_alerts, similar_alerts_total = alerts_similar_to_case(case, user=request.user)
```

Initialize `similar_alerts = []` / `similar_alerts_total = 0` alongside `similar_cases` (so non-similar
tabs pass empty). Pass both into the `_base_context(...)` call.

- [ ] AC-8: no `alerts_similar_to_case` call when the tab isn't active (assert in tests).

### A3 — new POST view + URL  → AC-3, AC-4, AC-5, AC-6, AC-7

New view (name `case_alert_link`) mirroring `alert_merge`:

```python
@not_viewer
@login_required
@require_POST
def case_alert_link(request: HttpRequest, case_id: str) -> HttpResponse:
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    alert = resolve_alert(request.user, request.POST.get("alert_id") or "", write=True)
    if alert is None:
        raise Http404()
    if alert.case_id is not None:
        messages.info(request, "Alert is already linked to a case.")
        return _case_detail_redirect(request, case.number)
    try:
        with transaction.atomic():
            merge_alert_into_case(alert, case, actor=request.user)
            record_alert_bundled(
                case,
                alert,
                actor=request.user,
                reason="Alert linked from Similar tab",
                source="merge",
            )
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return _case_detail_redirect(request, case.number)
    messages.success(request, f"Alert linked to case {case.number}.")
    return _case_detail_redirect(request, case.number)
```

> **`_case_detail_redirect` reads the tab from `request.POST["tab"]`** (validated against
> `CASE_TAB_SLUGS`) — it does **not** take a `tab=` kwarg. So the **template's form must include a
> hidden `<input type="hidden" name="tab" value="similar">`** for the redirect to land back on the
> Similar tab. Do **not** add a kwarg to the helper. Verify `"similar"` is in `CASE_TAB_SLUGS`.
- Route in `ui/urls.py`: `path("cases/<str:case_id>/link-alert", views.case_alert_link,
  name="ui-case-alert-link")` (no trailing slash — matches module convention). Verify the `<str:case_id>`
  converter matches the other case routes so the **number** resolves (`resolve_case` accepts number).
- `resolve_alert` already enforces org scope → uniform 404 for a cross-org alert (AC-5).
- [ ] AC-4: after POST, `alert.case == case`, a `correlation-bundle` TimelineEvent and a
      `case.alert_bundled` AuditLog row exist; response redirects to the Similar tab.
- [ ] AC-6: `@not_viewer` refuses viewers.
- [ ] AC-7: already-linked → info message, no duplicate ledger row.

---

## 2. Phase B — frontend (`ui/templates/ui/_tab_similar.html`)  → AC-1, AC-3, AC-6, AC-11

### B1 — the "Similar alerts" card (below the existing "Similar cases" section)
Mirror the existing card structure and the `_tab_alerts.html` row markup:
- `<section class="card" aria-labelledby="h-similar-alerts">` + `<h2 id="h-similar-alerts">Similar alerts</h2>`.
- Rows: source (`alert.ingestion_source.name|default:alert.source` — mirror `_tab_alerts.html`; if
  unavailable, use `alert.source`), title (link to `ui-alert-detail`), severity
  (`sev-{{ alert.severity|sev_class }}` / `sev_label`), status
  (`pill pill-{{ alert.status.stage|lower }}` + `alert.status.value`), received (`alert.date`), plus the
  **Link** control.
- **Link control** (AC-3), gated by `{% if can_mutate %}` (AC-6 — viewers get no button):
  ```html
  <form method="post" action="{% url 'ui-case-alert-link' case.number %}">
    {% csrf_token %}
    <input type="hidden" name="tab" value="similar">
    <input type="hidden" name="alert_id" value="{{ sa.alert.id }}">
    <button type="submit" class="btn btn-sm">Link to case</button>
  </form>
  ```
  One-click, **no** native confirm (Q5). The hidden `tab=similar` is **required** so
  `_case_detail_redirect` returns to the Similar tab (it reads `request.POST["tab"]`).
- Truncation line when `similar_alerts_total > similar_alerts|length` (`.list-summary dim`).
- Empty states (Q8 — always render the card):
  (a) case has no attached alerts → "This case has no alerts to compare against.";
  (b) has alerts, no matches → "No unlinked alerts look similar right now."
  *(Choose the exact condition using `case.alerts.exists` — note the case's linked alerts are already
  in the `alerts` context var.)*

### B2 — AC-11 status-label fixes
Replace the non-existent `get_status_display` with `status.value` in:
- `_tab_similar.html:25` → `{{ sc.case.status.value }}`.
- `observable_detail.html:201` → `{{ link.case.status.value }}`.

- [ ] AC-1: card renders below Similar cases, even when empty.
- [ ] AC-3/AC-6: link form present for mutators, absent for viewers.
- [ ] AC-11: both status pills show the status label (not blank).

### B3 — CSS
Expect **none**. Reuse `card`, `data-table`/`data-table-tight`, `table-wrap`, `sr-only`, `sev*`,
`sev_class`/`sev_label`, `pill pill-*`, `btn btn-sm`, `list-summary dim`, `mono`, `linkish`, `empty`.
Any new class must go in `app.src.css` + `make css` and must **not** push the undefined-class backlog
past **42**.

---

## 3. Phase C — tests (`tests/conformance/test_ui_similar_alerts.py`)

See plan §8. Must cover: card-renders; only-unlinked-same-org-overlapping listed; linked alert absent;
**cross-org isolation** (list + POST→404); link POST writes `alert.case` + ledger + audit + redirects;
viewer has no button and POST is refused; already-linked → info, no duplicate ledger; truncation line;
**AC-8 tab-gating** (no query when not on the Similar tab — e.g. assert `similar_alerts` stays empty on
another tab or mock `alerts_similar_to_case`); AC-11 (status label present).

**§7.5 falsifiability (required):** the cross-org test must fail if `org_q("owner_org", user)` is
removed from the candidate query. Prove it (temporarily remove → fail → restore; `git diff` empty after).

Run only this module + `make check`; do **not** run the full suite.

---

## 4. Verification checklist (agent to run before reporting done)

- [ ] `make check` green (ruff incl. Markdown, mypy, `manage.py check`, `makemigrations --check`, `css-check`).
- [ ] Undefined-class backlog still **42**.
- [ ] Targeted pytest on the new module only.
- [ ] Falsifiability of the cross-org test proven; `git diff` restored exactly.
- [ ] Confirm no `get_status_display` remains on a `Case` in any template (`grep`).

## 5. Deliberately unchanged

- `candidate_cases_for_alert`, `cases_sharing_findings`, `_tab_alerts.html`, `alert_merge`/`alert_escalate`
  views, `AlertObservable` population.
- No API, no schema, no migration.
