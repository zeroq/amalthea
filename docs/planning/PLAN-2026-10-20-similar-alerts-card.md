# PLAN — 2026-10-20 — Case Details → Similar tab: "Similar Alerts" card + link action

**Status:** APPROVED (2026-10-20) — §9 decisions locked to the recommended options (see §9.1); ready for handoff.
**Author:** planner
**Scope:** UI + view + one new POST route. No schema change, no API change.

---

## 1. Summary

The Case Details **Similar** tab currently renders one card — **"Similar cases"** (cases sharing
observables with this case, via `cases.correlation.cases_sharing_findings`).

Request: **add a second card below it — "Similar alerts"** — listing **unlinked alerts** (`alert.case
IS NULL`) that are *similar to the alerts already part of this case*, each with a **button to link that
alert to the current case**.

"Similar" here means: an unlinked alert whose signals (extracted observables and/or
`correlation_key`) overlap the signals of the **alerts already attached to this case**.

---

## 2. Goals

- G1 — A new **"Similar alerts"** card on the Similar tab, below the existing "Similar cases" card.
- G2 — It lists **unlinked alerts** (`case_id IS NULL`) that overlap this case's alerts.
- G3 — Each row has a **link control** that attaches the alert to the current case (one POST).
- G4 — **Tenant-safe**: only alerts in the caller's org (`org_q("owner_org", …)`) are ever listed or
  linkable; the target case is the one being viewed (already scoped by `resolve_case`).
- G5 — Reuses the existing linking service (`merge_alert_into_case` + `record_alert_bundled`), so the
  ledger/audit behaviour is identical to the alert-detail "Merge" path.
- G6 — Accessible + keyboard-driven; consistent with the existing cards (AGENTS.md §6).
- G7 — No new undefined CSS classes (AC-9 backlog stays 42).
- G8 — Viewers are **read-only**: they may see the card but must not get a working link control.

## 3. Non-goals

- No auto-linking. Linking is an explicit analyst action (one click per alert).
- No change to `AlertObservable` population or ingest.
- No change to the "Similar cases" card's behaviour (except a possible adjacent status-label bug, Q7).
- No pagination beyond the app's standard cap.
- No API change, no schema/migration, no new model.

---

## 4. Current behaviour (grounded in code)

- **Similar tab** `ui/templates/ui/_tab_similar.html` — one `<section class="card">` "Similar cases".
  Context: `similar_cases` (list[`SimilarCase`]) + `similar_total`. `case` also in context.
- **View** `ui/views.py:_case_detail` (~1047-1068) builds `similar_cases` **only when
  `active_tab == "similar"`** (deliberate: no query when the tab isn't rendered). It builds `Finding`s
  from the case's `CaseObservable`s (excluding `ignore_similarity`) and calls
  `cases_sharing_findings(findings, user=…, exclude_case=case)`.
- **Correlation** `cases/correlation.py`:
  - `cases_sharing_findings(...)` — one grouped `CaseObservable` query (case-side).
  - `candidate_cases_for_alert(alert, user=…)` — the **alert→case** direction: dry-run extraction over
    `alert.title` + `json_leaves(alert.raw_payload)` **plus** a synthetic finding from
    `alert.correlation_key`, matched to visible open cases. This is the algorithm to invert.
  - `SimilarCase(case, shared_count, shared)` dataclass.
- **Alert model** `alerts/models.py:Alert` — `case` FK nullable (`related_name="alerts"`), `owner_org`
  FK, `correlation_key` (indexed `alert_corrkey_date_idx`), `status` FK (`AlertStatus.value`/`.stage`),
  `severity`, `date`, `source`, `type`, `source_ref`, `title`.
- **Alert→observable link** `alerts/models.py:AlertObservable` (`related_name="alert_observables"`,
  indexed `(observable, alert)`). **Populated only by the API `POST /alert/{id}/observable`** (manual
  add) — **NOT** on ingest. So it is a *supplementary* signal, not the primary one.
- **Linking service** `alerts/escalation.py:merge_alert_into_case(alert, case, *, actor)` — sets
  `alert.case`, status→`Imported`, extracts observables into the case, appends a ledger entry. Paired
  with `cases/correlation.py:record_alert_bundled(case, alert, actor=…, reason=…, source="merge")`
  (ledger `correlation-bundle` + audit `case.alert_bundled`). The pair is wrapped in
  `transaction.atomic` in the existing `alert_merge` view (`ui/views.py:524`), which is decorated
  `@not_viewer @login_required @require_POST`.
- **Visibility helpers** `identity/scoping.py`:
  - `org_q(field, user)` — own-org (or all for superAdmin). Used for alerts (`org_q("owner_org", …)`).
  - `resolve_case(user, id, write=bool)` / `resolve_alert(user, id, write=bool)` — scoped resolves.
- **Caps** `ui/pagination.py` — `RAIL_CAP = 200`, `PICKER_PAGE_SIZE = 200`.

---

## 5. Proposed design

### 5.1 Matching algorithm (the load-bearing decision)

For the viewed case, compute the **union of its attached alerts' signals**, then rank unlinked
same-org alerts by overlap. Two signal sources, in priority order:

1. **`correlation_key`** (fast, indexed): collect the non-empty `correlation_key`s of the case's alerts;
   match unlinked alerts with an equal, non-empty `correlation_key`.
2. **Payload-extracted findings** (slower, robust): reuse `candidate_cases_for_alert`'s dry-run
   extraction (`find_observables(alert.title, json_leaves(alert.raw_payload))` + the synthetic
   `correlation_key` finding) for each candidate alert, and intersect with the case's findings
   (built the same way the Similar-cases tab builds them).

**Bounded cost:** the candidate set is restricted to `case__isnull=True`, `org_q("owner_org", user)`,
and a hard cap (reuse `MAX_CORRELATION_OBS`-style bounding / `RAIL_CAP` rows), and extraction runs on
**candidate alerts only** (those passing the cheap `correlation_key` pre-filter first, then a bounded
payload pass). This mirrors the existing `candidate_*` guardrails (attacker-controlled payloads bounded
by `WEBHOOK_MAX_BODY_SIZE` / `WEBHOOK_MAX_DEPTH`, `RecursionError`/`MemoryError` swallowed).

**New helper** in `cases/correlation.py` (DRY, next to its inverse):

```python
@dataclass(frozen=True)
class SimilarAlert:
    alert: Alert
    shared_count: int
    shared: list[tuple[str, str]]  # ordered (type_name, value)


def alerts_similar_to_case(
    case: Case, *, user: User, limit: int | None = None
) -> tuple[list[SimilarAlert], int]:
    """Unlinked, same-org alerts overlapping the signals of `case`'s attached alerts."""
```

- Signals from the case's alerts: (a) non-empty `correlation_key`s; (b) findings extracted from each
  attached alert's `title`/`summary`/`description`/`raw_payload` (**bounded to the case's alert count**
  — see Q3).
- Candidates: `Alert.objects.filter(org_q("owner_org", user), case__isnull=True)`.
- Rank: `shared_count` desc, then `-date`.
- Return `(rows[:limit], total_matching)` for honest truncation (same contract as
  `cases_sharing_findings`).

### 5.2 View (`ui/views.py`)

- Inside the existing `if active_tab == "similar":` block, also compute `similar_alerts`:
  `similar_alerts, similar_alerts_total = alerts_similar_to_case(case, user=request.user)`.
- Pass `similar_alerts` / `similar_alerts_total` into context.
- **New POST view** `case_alert_link(request, case_id)` (name TBD, Q6):
  `@not_viewer @login_required @require_POST`; `resolve_case(write=True)` → 404 if not visible;
  `resolve_alert(write=True)` (or org-scoped `get_object_or_404`) → 404 if not in org;
  guard `alert.case_id is None` (reject if already linked — Q5) and `alert.owner_org == case.owner_org`;
  then `transaction.atomic` around `merge_alert_into_case(alert, case, actor=request.user)` +
  `record_alert_bundled(..., source="merge")`. Redirect back to the Similar tab
  (`_case_detail_redirect(request, case.number, tab="similar")` — verify that helper supports `tab`).

### 5.3 Template (`ui/templates/ui/_tab_similar.html`)

Add a second `<section class="card">` **below** the existing one:

- `<h2 id="h-similar-alerts">Similar alerts</h2>`.
- Empty states: (a) case has no attached alerts → "This case has no alerts to compare against.";
  (b) has alerts but no matches → "No unlinked alerts look similar right now."
- Rows mirror the alerts table used elsewhere (`_tab_alerts.html`): source, title (link to
  `ui-alert-detail`), severity, status, received — plus a **Link** button.
- **Link control:** a small `<form method="post" action="{% url 'ui-case-alert-link' case.number %}">`
  with `{% csrf_token %}` + hidden `alert_id`, button `class="btn btn-sm"` labelled "Link to case".
  Gated by `{% if can_mutate %}` (viewers see rows, no button). **No native `confirm()`** — if a
  confirm step is wanted, reuse the `_modal.html` dialog (Q5).
- Truncation line: "Showing N of M similar alerts" (existing `.list-summary dim` pattern).

### 5.4 CSS

Expect **none**: reuse `card`, `data-table`/`data-table-tight`, `table-wrap`, `sr-only`, `sev*`,
`sev_class`/`sev_label`, `pill pill-*`, `btn btn-sm`, `list-summary dim`, `mono`, `linkish`, `empty`.
If a wrapper is needed (e.g. aligning the button column), prefer an existing utility; any new class
goes in `app.src.css` + `make css` and must not push the undefined-class backlog past 42.

### 5.5 Why not just match `AlertObservable`?

`AlertObservable` is only written by the manual API add-observable endpoint, so most ingested alerts
have **zero** alert-observables. Matching on it alone would make the card almost always empty. We treat
it as an **additional** signal where present (cheap join, `(observable, alert)` index) but rely on
`correlation_key` + payload extraction for real coverage.

---

## 6. Security & tenancy

- **G4**: candidate alerts filtered by `org_q("owner_org", user)`; the POST resolves the alert with the
  same org scope (uniform 404, no existence oracle). Cross-org alert or closed case → refused
  (`merge_alert_into_case` + `record_alert_bundled` both guard; the view also pre-checks).
- **Viewer**: `@not_viewer` on the POST (403/redirect); template omits the button for viewers.
- **Idempotence/replay**: `record_alert_bundled` refuses a duplicate bundle; the view rejects an
  already-linked alert. (Confirm desired UX — Q5.)
- **Untrusted payload**: extraction is bounded and exception-swallowing, exactly as
  `candidate_cases_for_alert`.
- No user data interpolated unsafely; titles auto-escaped.

## 7. Performance

- Tab-gated (only computed when `active_tab == "similar"`), like the existing card.
- Candidate set bounded + capped; cheap `correlation_key` pre-filter before any payload extraction.
- One grouped query per signal source; no per-row N+1.

## 8. Testing

New conformance module `tests/conformance/test_ui_similar_alerts.py`:

1. Card renders below Similar cases; lists only **unlinked** same-org alerts that overlap.
2. A linked alert is **not** listed (and never linkable).
3. **Cross-org isolation**: an alert in another org with the same correlation_key is never listed nor
   linkable (POST → 404).
4. Link POST attaches the alert: `alert.case == case`, ledger `correlation-bundle` + audit
   `case.alert_bundled` written; redirect back to the Similar tab.
5. Viewer: sees the card but **no** link button; POST as viewer is refused.
6. Already-linked alert POST → refused, no duplicate ledger row.
7. Honest truncation line when capped.
8. `make check` green; undefined-class backlog 42.

Falsifiability (§7.5): test 3 must fail if the org filter is removed.

## 9. Decisions (locked 2026-10-20 — user approved all recommendations)

### 9.1 Locked answers

- **Q1 — "similar" = overlap with the case's attached alerts.** An unlinked alert is a candidate when
  its signals overlap the signals of **any alert already attached to this case** (not the case's
  observables directly).
- **Q2 — use BOTH signals.** `correlation_key` (cheap, indexed pre-filter) **and** payload-extracted
  findings (robust fallback). `AlertObservable` rows, where present, are an additional cheap signal.
- **Q3 — comparison basis = all attached alerts, extraction capped.** Build the case's alert-signal set
  from every alert currently attached to the case; cap the *payload-extraction pass* at a module
  constant (reuse the `MAX_CORRELATION_OBS`-style bound) and document it.
- **Q4 — strictly unlinked only.** Candidates are `case_id IS NULL`. Alerts on another case are out of
  scope (moving an alert between cases is a separate, destructive action).
- **Q5 — one-click link, no modal; already-linked → info message.** The POST is low-risk and reversible
  via the existing detach path, so no `_modal.html` confirmation. If the alert is already linked
  (race/double-click), show an info message and refresh — no error, no duplicate ledger row.
- **Q6 — route shape:** `POST /cases/<number>/link-alert`, view name `ui-case-alert-link`, with
  `alert_id` in the POST body. Consistent with the other `cases/<n>/…` POST routes.
- **Q7 — fix the two blank status pills.** `_tab_similar.html:25` and `observable_detail.html:201` must
  render `{{ …status.value }}` instead of the non-existent `get_status_display`.
- **Q8 — placement:** a second card **below** "Similar cases", heading **"Similar alerts"**, always
  rendered (empty state when no matches).

## 10. Acceptance Criteria (locked 2026-10-20)

- AC-1 A "Similar alerts" card renders on the Similar tab, below "Similar cases" (even when empty).
- AC-2 It lists unlinked, same-org alerts overlapping this case's attached alerts (per Q1/Q2),
  ranked (`shared_count` desc, then `-date`), capped, with an honest truncation line when capped.
- AC-3 Each row offers a one-click **Link to case** control that attaches the alert to the current case.
- AC-4 Linking writes `alert.case`, the ledger `correlation-bundle` event, and the audit
  `case.alert_bundled`, in one transaction, then redirects to the Similar tab.
- AC-5 Only same-org alerts are listed/linkable; cross-org POST → 404 (test proves it).
- AC-6 Viewers see the card but cannot link (no button; POST refused).
- AC-7 Already-linked alerts are not listed; a POST for one shows an info message, writes **no**
  duplicate ledger row.
- AC-8 The card is computed only when the Similar tab is active (tab-gated; no query otherwise).
- AC-9 `make check` green; undefined-class backlog unchanged (42).
- AC-10 No API/schema/migration change.
- AC-11 `_tab_similar.html:25` and `observable_detail.html:201` render `status.value` (blank-pill bug fixed).

## 11. Phases & handoff

**Implementation Brief:** `docs/planning/BRIEF-2026-10-20-similar-alerts-card.md`

- **Phase A (backend, `django-backend`)**: `alerts_similar_to_case` in `cases/correlation.py`; the
  `_case_detail` context additions; the new POST view + URL (`ui-case-alert-link`).
- **Phase B (frontend, `django-frontend`)**: the "Similar alerts" card + link form in
  `_tab_similar.html`; the AC-11 status-label fixes in `_tab_similar.html` + `observable_detail.html`.
- **Phase C (QA, `qa-tester`)**: `tests/conformance/test_ui_similar_alerts.py` (incl. cross-org +
  viewer + idempotence + falsifiability + AC-11).
- **Phase D (planner)**: COMPLETED/TODO note.

Iterate/approve before handoff; deviations recorded in §11.1.

### 11.1 Deviations

1. **Viewer POST is refused with 404, not 403.** `@not_viewer` (project convention) returns 404 for
   uniform non-disclosure. AC-6 asserts "refused + alert stays unlinked" rather than a specific code.
2. **Truncation line untested at >`RAIL_CAP`.** Logic exists (mirrors `cases_sharing_findings`), but no
   test creates >200 matches — consistent with existing correlation coverage.
3. **`total_matching` is bounded by the candidate cap** (`MAX_CORRELATION_OBS = 1000`), so the
   "showing N of M" `M` reflects matches among the bounded candidate window. Consistent with the
   plan's bounded-cost requirement and existing helpers.
4. **`case_corr_keys` synthetic findings are added twice** (into `case_findings` at build time and
   again into `case_signal_set`); harmless idempotent `set.add`, no behavioural impact.
