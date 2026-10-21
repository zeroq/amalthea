# Plan — Similar tab: lazy-load on click + combined-count badge

**Date:** 2026-10-21
**Status:** Approved (decisions locked 2026-10-21)
**Author:** planner
**Supersedes/relates to:** `PLAN-2026-10-20-similar-alerts-card.md` (exact-signal card; card basis
superseded), `PLAN-2026-10-20-alert-fuzzy-similarity.md` (fuzzy basis — **unchanged** by this plan).

---

## 1. Summary

On the case-detail page the **Similar** tab's two cards are empty until the page is *manually
refreshed*. Clicking the tab shows an empty panel; only a real navigation to `?tab=similar`
repopulates it. This plan fixes that and adds an **attention badge** on the **Similar** tab label
showing the **combined** number of similar cases + similar alerts (only when > 0).

## 2. Root cause (diagnosed, with evidence)

1. Case-detail tabs are real links: `ui/templates/ui/case_detail.html:33-56`
   (`<a href="?tab={{ tab.slug }}">`).
2. `ui/static/ui/tabs.js:84-89` intercepts the click with `event.preventDefault()` and **only toggles
   `hidden`** on already-rendered panels; it never fetches.
3. The server computes `similar_cases` / `similar_alerts` **only when `active_tab == "similar"`**:
   `ui/views.py:1084-1105`; otherwise they default to `[]` / `0` (`ui/views.py:1079-1083`).
4. Therefore: a load at `?tab=general` renders an **empty** `#panel-similar`; clicking the tab reveals
   that empty panel; a manual navigation to `?tab=similar` repopulates it on the server.

**Additional fact:** there is **no HTMX in this repository** despite AGENTS.md §6 mentioning it. The
established "refresh a panel from the server" pattern is `ui/static/ui/live.js`'s
`refreshPages()`/`refreshTasks()`: `fetch(location.href, {headers: {"X-Requested-With": "fetch"}})`
→ `DOMParser.parseFromString(...)` → swap the element by id (`ui/static/ui/live.js:147-190`).

## 3. Goals / Non-goals

### Goals
- **G1** Clicking the **Similar** tab loads its two cards on first activation, **without** a manual
  refresh. Progressive enhancement: works with JS disabled (the real `?tab=similar` link still renders
  everything server-side).
- **G2** The **Similar** tab label carries a **badge** with the **combined** count
  (`similar_total + similar_alerts_total`) when that sum is **> 0**; no badge when 0.
- **G3** The default (`general`) case-detail load stays fast: the expensive similar computation runs
  **once per case-detail load** (for the badge) and is **not** repeated when the tab is opened.

### Non-goals
- No HTMX adoption or whole-tab-bar refactor.
- No change to the **contents** of the two cards, to `cases_sharing_findings`, or to
  `alerts_fuzzy_similar_to_case` (the fuzzy behaviour is frozen by the prior, verified plan).
- No change to pagination/caps of other tabs.
- No auto-refresh / live updates of the Similar tab (out of scope; `live.js` frames are unrelated).

## 4. Locked decisions (2026-10-21)

| # | Decision | Choice |
|---|----------|--------|
| D1 | **Badge count** | **Combined exact count, computed on every case-detail load** (option a). Badge rendered only when the sum > 0. Accepted the per-view cost of the fuzzy computation as the price of an accurate badge. |
| D2 | **Fragment transport** | Dedicated **fragment endpoint** `GET cases/<number>/similar` returning the two cards as HTML (option i), swapped in by `tabs.js` via the `live.js` DOMParser pattern. |
| D3 | **Viewers** | Viewers **may** fetch the fragment (read-only). Endpoint is `@login_required` only (**not** `@not_viewer`). |
| D4 | **Badge style** | Reuse the existing `.count` chip used by Alerts/Tasks/etc. (`case_detail.html:53`). |
| D5 | **Plan form** | New plan document (this file). |

## 5. Architecture

### 5.1 New endpoint (D2, D3)
- **URL:** `cases/<str:case_id>/similar` → name `ui-case-similar`.
  Placement: alongside `ui-case-alert-link` in `ui/urls.py` (no trailing slash; consistent with the
  file). Note the literal-prefix rule (`ui/urls.py:21`): a literal must precede `cases/<str:case_id>`;
  this path has the same `cases/<str:case_id>/...` shape so it is unaffected.
- **View:** `ui/views.py::case_similar_fragment(request, case_id)`.
  - Decorators: `@login_required` only (D3). **No** `@not_viewer`, **no** `@require_POST` (it is a GET).
  - `case = resolve_case(request.user, case_id, write=False)`; `if case is None: raise Http404()`.
  - Compute the two lists via the **shared helper** (see 5.2) and render
    `ui/_tab_similar.html` (the fragment) — **not** the whole page.
  - Response status 200; content type HTML. A failed/forbidden case → 404 (consistent with the rest of
    the UI, e.g. `case_alert_link`).

### 5.2 Shared query helper (DRY; G3, R3)
Extract the current inline block (`ui/views.py:1084-1105`) into a single helper so the full view and the
fragment endpoint cannot drift:

```python
def _similar_for_case(
    case: Case, user: User
) -> tuple[list[SimilarCase], int, list[SimilarAlert], int]:
    """Return (similar_cases, similar_total, similar_alerts, similar_alerts_total) for a case."""
```

- Lives in `ui/views.py` (nearest to both callers) **or** `cases/correlation.py` if we prefer domain
  placement — planner recommends `ui/views.py` (it composes `observables.extractor.Finding` + two
  domain helpers; no new domain logic). **Implementer to confirm** and keep it private (`_`).
- `case_detail` calls it **unconditionally** (D1) and stores the four values in the context.
- `case_similar_fragment` calls the **same** helper and renders only the fragment.

### 5.3 Badge wiring (G2, D1, D4)
- In `case_detail`, after the helper call, add to the `counts` dict (`ui/views.py:943-951`):
  `"similar": similar_total + similar_alerts_total` — but only when the sum > 0, so the existing
  `{% if tab.count is not None %}` guard (`case_detail.html:53`) hides it at 0.
  Implementation note: set `tab["count"]` only when `sum > 0` (i.e. leave it `None` at 0). Do **not**
  emit `0`.
- The badge must be **label + number**, not colour-only (AGENTS.md §6). `.count` is already textual.

### 5.4 Template split (R3)
Split `ui/templates/ui/_tab_similar.html` into two card partials with **no visual change**:
- `ui/templates/ui/_similar_cases_card.html` (the `<section class="card">` for similar cases)
- `ui/templates/ui/_similar_alerts_card.html` (the similar-alerts card)
- `_tab_similar.html` becomes an include-only wrapper of the two (unchanged when embedded in the page).
This lets `case_similar_fragment` render just the wrapper (or the two partials) and keeps the full-page
and fragment markup identical.

### 5.5 Client (G1, R2)
In `ui/static/ui/tabs.js`:
- Mark the Similar panel as lazy in `case_detail.html`:
  `<section ... id="panel-similar" data-lazy-src="{% url 'ui-case-similar' case.number %}">`
  (only when the server did **not** already render it — see below).
- On `activateTab(tab)`, if the target panel has `data-lazy-src` and **no** `data-loaded`, fetch the
  URL once, `DOMParser` the reply, replace `#panel-similar`'s inner HTML (or the panel node), set
  `data-loaded`, then reveal.
  - **Collapse concurrent requests** and guard re-entry exactly like `live.js` (`pagesRefresh` flag).
  - On failure: leave the panel as-is (its server-rendered empty state) and clear the flag so a retry
    is possible — never show an error banner (matches `live.js`'s silent-catch convention).
  - Does **not** move focus (WAI-ARIA APG; `tabs.js:63-64`).
- **Server already rendered it:** when `active_tab == "similar"` the server renders the cards *and*
  the panel carries `data-loaded` (or no `data-lazy-src`), so the client never re-fetches on a deep
  link/refresh. Concretely: emit `data-lazy-src` **only** when `active_tab != "similar"`.
- Update the **badge** from the fragment: the fragment's root carries `data-similar-count="<sum>"`
  (and/or `data-cases` / `data-alerts`); `tabs.js` copies it onto `#tab-similar .count` (create the
  `.count` span if absent, remove it if the re-fetched sum is 0). This keeps badge and panel coherent
  after a link/merge changes the numbers.
  - _Simplification option for the implementer:_ since D1 already computes the count on load and this
    plan does not implement live updates, the badge can be rendered **server-side only** and left
    untouched by the client. Planner **recommends** the server-only badge for MVP (smaller diff, no
    client state), with the `data-similar-count` attribute present so a future live pass can use it.
    Implementer to confirm; record the choice in the plan addendum if deviating.

## 6. Data / API contract

- **Method/path:** `GET cases/<number>/similar`.
- **Auth:** any authenticated, org-visible user (viewer included). Cross-org / unknown / invisible →
  **404**.
- **Response:** HTML fragment — the two `<section class="card">` cards identical to the page's
  `#panel-similar` content. Root wrapper carries:
  - `data-similar-count="<similar_total + similar_alerts_total>"`,
  - `data-similar-cases="<similar_total>"`, `data-similar-alerts="<similar_alerts_total>"`.
- **No JSON API surface added**, no CSRF (GET, read-only), no mutation.

## 7. UI / UX

- **Badge:** `.count` chip after the "Similar" label, same as Alerts/Tasks counts. Present only when
  combined > 0. Accessible name via the existing tab link text + the visible number.
- **Loading:** the panel may briefly show nothing while the fetch is in flight; acceptable (fast,
  same-origin). Optional: a `aria-busy="true"` on the panel during fetch (nice-to-have).
- **Empty states:** unchanged (the cards' existing "no artifacts"/"no alerts to compare"/"none
  similar" copy is reused verbatim — the lazy fragment renders the same partial).
- **Keyboard:** unchanged; click + 1–9 + arrows already work. Focus never moves on activation.

## 8. Phases & tasks (each with Acceptance Criteria)

### P1 — Template split (no visual change)
- Split `_tab_similar.html` → `_similar_cases_card.html` + `_similar_alerts_card.html`; wrapper keeps
  the same output.
- **AC-P1.1** Rendering `?tab=similar` produces byte-identical visible markup to before (diff the two
  card sections in a render test).
- **AC-P1.2** `make check` green; undefined-class backlog stays 42.

### P2 — Shared helper + fragment endpoint
- Extract `_similar_for_case(case, user)`; call it from `case_detail`.
- Add `case_similar_fragment` view + `ui-case-similar` URL + template fragment.
- **AC-P2.1** `GET /cases/<n>/similar` (analyst) returns 200 and the two cards with correct data.
- **AC-P2.2** Viewer fetches the fragment successfully (read-only) — 200, **no** Link buttons.
- **AC-P2.3** Cross-org case id and unknown case id → **404**.
- **AC-P2.4** `case_detail` still renders the same content when `active_tab == "similar"` (helper
  extraction is behaviour-preserving).
- **AC-P2.5** No new query issued on `?tab=general` beyond the one helper call (assert the helper is
  invoked once per page render; the two card queries are not duplicated).

### P3 — Badge (server-side)
- Set `tab["count"]` for `similar` only when combined > 0.
- **AC-P3.1** With ≥1 similar case or alert, `#tab-similar` renders the `.count` with the **sum**.
- **AC-P3.2** With zero of both, `#tab-similar` renders **no** `.count`.
- **AC-P3.3** Falsifiable: force the badge condition off (or return 0) and the badge test fails.

### P4 — Client lazy-load
- `data-lazy-src` on `#panel-similar` only when `active_tab != "similar"`; `tabs.js` fetch+swap once,
  re-entry guard, silent failure.
- **AC-P4.1** Simulating first activation of a lazy panel triggers exactly **one** fetch and replaces
  the panel content.
- **AC-P4.2** A second activation does **not** re-fetch (`data-loaded`).
- **AC-P4.3** When the server already rendered the tab, **no** `data-lazy-src` is emitted (no fetch).
- **AC-P4.4** With JS disabled, `?tab=similar` renders fully (server-side) — unchanged.
- **AC-P4.5** Falsifiability: removing the re-entry guard causes the double-fetch test to fail;
  removing the fetch causes the populate test to fail.

### P5 — Tests
- New `tests/conformance/test_ui_similar_tab_lazy.py` covering AC-P1..P4 and the authz matrix
  (viewer allowed, cross-org 404, unknown 404).
- **AC-P5.1** All new tests pass; existing `test_ui_similar_alerts.py` /
  `test_alert_fuzzy_similarity.py` still pass (no regression).

### P6 — Verification & docs
- `verifier` report → `docs/verification/VERIFY-2026-10-21-similar-tab-lazy-badge.md` (AC-by-AC).
- Update `COMPLETED.md` + `TODO.md` (close the "empty until refresh" gap).
- **AC-P6.1** Verifier marks every AC; docs updated.

## 9. Security

- Endpoint enforces visibility via `resolve_case` (org scoping + share/visibility rules) → 404 for
  non-visible; **viewer read-only** (no mutating control in the fragment).
- GET, read-only, same-origin fragment → no CSRF, no XSS sink (server-rendered markup, swapped
  detached via `DOMParser`, never `innerHTML` of remote text).
- **CPU/DoS:** the fragment now makes the fuzzy computation reachable by any authenticated user per
  case. Bounded by `MAX_FUZZY_COMPARISONS = 500`; this is the same work the page already does on
  `?tab=similar`. **Ties to security FIND-03 / plan risk R5** (perf on Postgres, unmeasured) — note it
  in the verification; no new cap introduced here.
- Badge computation is on the **page** load path, not a separate expensive endpoint, so it does not
  add a new amplification surface.

## 10. Testing

- Unit/render (Django test client) for: fragment authz matrix, combined badge value, badge absence at
  0, helper called once, template split equivalence.
- JS behaviour: asserted via the rendered attributes (`data-lazy-src`, `data-loaded`) and the
  `tabs.js` contract; if a JS test harness is not available, pin the **server contract** (attributes)
  and document the JS as verified by manual browser check in the verification report.
- Falsifiability (§7.5): P3 badge guard and P4 re-entry guard each proven by temporary sabotage +
  restore (`git diff` empty).

## 11. Risks

| ID | Risk | Mitigation |
|----|------|------------|
| R1 | Badge computation adds the 500-compare work to **every** case-detail load (D1). | Accepted by decision; bounded cap; measure on Postgres later (FIND-03/R5). Keep the helper single-call. |
| R2 | Double/racy fetch in `tabs.js`. | `data-loaded` guard + re-entry flag mirroring `live.js`; falsifiable test. |
| R3 | Fragment drifts from the full render. | One shared template partial + one shared helper (P1/P2). |
| R4 | Regression with JS disabled. | Real `?tab=similar` link path untouched (AC-P4.4). |
| R5 | Badge/panel incoherence after a future live update. | `data-similar-count` shipped now so a later live pass can sync; MVP badge is server-rendered. |

## 12. Open questions (to confirm during implementation)

1. Helper placement: `ui/views.py` (planner recommendation) vs `cases/correlation.py`.
2. Badge updated client-side from `data-similar-count` or server-only for MVP (planner recommends
   server-only; attribute shipped for later).
3. Loading placeholder: `aria-busy` during fetch — nice-to-have, not required by any AC.

## 13. Handoff

- **Backend** (`django-backend`): P1 (template split), P2 (helper + endpoint + URL + authz).
- **Frontend** (`django-frontend`): P3 (badge context/render), P4 (`tabs.js` lazy-load + attributes).
- **QA** (`qa-tester`): P5.
- **Verifier**: P6.
- Demo target: open any case with a similar case or alert → badge visible on the Similar tab; click
  the tab → cards fill **without** a manual refresh; click again → no re-fetch; JS off → refresh
  `?tab=similar` still shows everything.

## 14. Implementation record & deviations

1. **P1/P2 (backend).** Delivered: `_similar_for_case` helper (`ui/views.py:907`), fragment view
   `case_similar_fragment` (`ui/views.py`), URL `ui-case-similar` (`ui/urls.py:44`), split partials
   `_similar_cases_card.html` / `_similar_alerts_card.html` + include-only `_tab_similar.html`, and
   fragment wrapper `_similar_tab_fragment.html` (root `<div data-similar-count|data-similar-cases|
   data-similar-alerts>`). `case_detail` calls the helper **unconditionally** (D1).
2. **Deviation — fragment empty-state source.** The fragment passes `alerts = case.alerts.all()`
   (an unevaluated QuerySet), whereas `case_detail` passes a `RAIL_CAP`-capped list. Truthiness for
   `{% if alerts %}` is equivalent (QuerySet defines `__bool__`); recorded for transparency. Not a
   behavioural difference; may be aligned to a capped list later for symmetry.
3. **Deviation — fragment view adds `@require_GET`.** The brief said `@login_required` only; the
   implementer also added `@require_GET` (safe, correct for a read endpoint; non-GET now 405). Accepted.
4. **Bug found & fixed during review (P3).** The badge was added to the `counts` dict at
   `ui/views.py:~1114` **after** the `counts`→`tabs` application loop (`~980`), so it never reached the
   tab and **no badge rendered** (violating AC-P3.1). Fixed by setting `tab["count"]` directly on the
   `similar` tab entry after the helper call. Verified: badge `1` with a matching unlinked alert,
   absent for a case with neither. Falsifiable test required in P5.
5. **Superseded behaviour change — tab-gating (D1).** The existing
   `test_alert_fuzzy_similarity.py::TestTabGating::test_fuzzy_helper_called_once_on_similar_and_never_on_general`
   encodes the OLD contract (helper **not** called on the general tab). D1 intentionally changes this
   (the helper runs on every case-detail load for the badge). **This test must be updated in P5**, not
   treated as a regression.
6. **P4 (frontend).** `case_detail.html` emits `data-lazy-src` only when `active_tab != "similar"` (and
   `data-loaded` when it is); `tabs.js` fetches the fragment once on first activation, swaps the panel's
   `innerHTML`, sets `data-loaded`, and collapses concurrent requests with a `similarRefresh` guard
   (silent failure, retry allowed, focus unchanged). Badge is **server-side only** for MVP (D1) as
   recommended; `data-similar-count` is shipped for a future live pass.
   - **Minor known edge (not fixed):** the collapse `return` in `tabs.js` exits before `pushState`, so a
     rapid second activation during an in-flight fetch skips the URL update (visual state is correct).
     Low impact; noted for a future pass.
7. **Config change folded in (threshold 80 → 60) + test recalibration.** Beyond the UI work, the
   operator lowered `ALERT_FUZZY_SIMILARITY_THRESHOLD` (default) from `80` to `60` in
   `amalthea/settings/base.py` after manual evaluation ("working fine"). This is a deliberate product
   decision on the FIND-05 trade-off (recorded in `docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md`
   and Q3/AC-A4 of `PLAN-2026-10-20-alert-fuzzy-similarity.md`). It surfaced three conformance failures
   because the fixtures were calibrated to 80:
   - `TestThresholdBoundary::test_default_and_explicit_threshold_include_exclude` — hard-coded `== 80`.
   - `TestThresholdGate::test_below_threshold_alert_does_not_leak` — asserted `score < threshold`
     (the `_variant_payload()` scored 68, which is **above** 60).
   - `TestEmptyStates::test_has_attached_alerts_but_no_matches` — the variant became a match, so the
     "no matches" empty state no longer appeared.
   Fix (Option 1, user-approved): re-crafted `_variant_payload()` to a genuinely different event scoring
   **46** (margin > 10 under 60), and updated the hard-coded default (`== 80` → `== 60`) plus two
   docstrings/module comments. Test *intent* is unchanged: near-dup included, genuinely-different
   excluded at the shipped default. **Falsifiability (§7.5):** sabotaging the variant to equal the base
   payload made all three tests fail (they were green only when the fixture was genuinely dissimilar);
   file restored byte-identical. Targeted run: **39 passed**; `make check` green.
