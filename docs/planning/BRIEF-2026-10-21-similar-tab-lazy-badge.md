# Implementation Brief — Similar tab: lazy-load + combined-count badge

**Plan:** `docs/planning/PLAN-2026-10-21-similar-tab-lazy-badge.md` (authoritative; read it first).
**Date:** 2026-10-21
**Scope:** P1–P4 (P5 tests by `qa-tester`, P6 verification).
**Do NOT** change the fuzzy algorithm (`alerts_fuzzy_similar_to_case`) or the similar-alerts/cases
matching logic — those are frozen by prior verified plans.

---

## Ground truth (verified seams)

- Tabs are real links: `ui/templates/ui/case_detail.html:33-56`; panel `#panel-similar` at `:83-85`.
- Tab click is intercepted and only toggles `hidden` (no fetch): `ui/static/ui/tabs.js:84-89`
  (`activateTab` at `:42-70`).
- Similar data computed **only** when `active_tab == "similar"`: `ui/views.py:1079-1105`.
- `counts` dict → `tabs` badge: `ui/views.py:941-954`; badge markup `case_detail.html:53`
  (`{% if tab.count is not None %}<span class="count">{{ tab.count }}</span>{% endif %}`).
- `resolve_case(user, identifier, *, write=False)` → `identity/scoping.py:143`.
- `not_viewer` decorator: `ui/views.py:175`. `_base_context`: `ui/views.py:281`.
- Refresh-from-server pattern to copy: `ui/static/ui/live.js:147-200` (`fetch(location.href,
  {headers:{"X-Requested-With":"fetch"}})` → `DOMParser` → element swap).
- URLs (no trailing slash): `ui/urls.py:23-24`.
- **No HTMX** in this repo — do not introduce it.

## P1 — Template split (django-backend or frontend)

Split `ui/templates/ui/_tab_similar.html` (120 lines) into:
- `ui/templates/ui/_similar_cases_card.html` — lines 8-59 (the "Similar cases" `<section>`).
- `ui/templates/ui/_similar_alerts_card.html` — lines 61-120 (the "Similar alerts" `<section>`).
- `_tab_similar.html` becomes a wrapper that `{% include %}`s the two partials, in order, with **no
  extra markup**, preserving the exact current output.

**AC-P1.1** `?tab=similar` output for the two cards is unchanged (compare rendered HTML in a test).
**AC-P1.2** `make check` green; undefined-CSS-class backlog stays 42.

## P2 — Shared helper + fragment endpoint (django-backend)

1. Extract `ui/views.py:1079-1105` into a private helper:
   ```python
   def _similar_for_case(case, user) -> tuple[list[SimilarCase], int, list[SimilarAlert], int]:
       # returns similar_cases, similar_total, similar_alerts, similar_alerts_total
   ```
   Behaviour identical to today's inline block (build `Finding`s from `CaseObservable` excluding
   `ignore_similarity`, call `cases_sharing_findings(...)` and `alerts_fuzzy_similar_to_case(...)`).
2. `case_detail` calls the helper **unconditionally** and keeps the same context keys
   (`similar_cases`, `similar_total`, `similar_alerts`, `similar_alerts_total`).
3. New view `case_similar_fragment(request, case_id)`:
   - decorators: `@login_required` **only** (viewer allowed; NOT `@not_viewer`, NOT `@require_POST`).
   - `case = resolve_case(request.user, case_id, write=False)`; `None` → `raise Http404()`.
   - render the fragment template (the two cards) with the helper's four values + `case`, `can_mutate`.
   - root element carries: `data-similar-count="<sum>"`, `data-similar-cases="<total>"`,
     `data-similar-alerts="<total>"`.
4. URL: `path("cases/<str:case_id>/similar", views.case_similar_fragment, name="ui-case-similar")`
   in `ui/urls.py`.

**AC-P2.1** analyst GET → 200, correct cards.
**AC-P2.2** viewer GET → 200, no "Link to case" buttons.
**AC-P2.3** cross-org / unknown case → 404.
**AC-P2.4** `??tab=similar` full render still shows the same content (helper extraction is
behaviour-preserving).
**AC-P2.5** helper invoked once per page render (no duplicated card queries).

## P3 — Badge (server-side) (django-frontend)

In `case_detail`, after the helper call, set the `counts` entry for `similar` to the **sum**, but only
when `> 0` so the existing `{% if tab.count is not None %}` hides it at 0:
```python
combined = similar_total + similar_alerts_total
if combined > 0:
    counts["similar"] = combined  # leave unset (=> None) at 0
```
Reuse the existing `.count` chip markup — no new CSS.

**AC-P3.1** ≥1 similar → `#tab-similar` has `.count` with the sum.
**AC-P3.2** zero of both → no `.count` on `#tab-similar`.
**AC-P3.3** falsifiable (force off → test fails).

## P4 — Client lazy-load (django-frontend)

In `case_detail.html`, mark the Similar panel lazy **only when the server did not render it**:
```html
<section ... id="panel-similar"
  {% if active_tab != "similar" %}data-lazy-src="{% url 'ui-case-similar' case.number %}"{% else %}data-loaded{% endif %}>
```
In `ui/static/ui/tabs.js` `activateTab`: if the target panel has `data-lazy-src` and no `data-loaded`,
`fetch` it once (same-origin), `DOMParser` the reply, replace `#panel-similar`'s content with the
fragment's content, set `data-loaded`, clear the in-flight flag; on failure leave the panel as-is and
allow retry. Mirror `live.js`'s collapse/silent-catch conventions. Never move focus.

**AC-P4.1** first activation → exactly one fetch + panel populated.
**AC-P4.2** second activation → no re-fetch (`data-loaded`).
**AC-P4.3** server-rendered tab → no `data-lazy-src` emitted (no fetch).
**AC-P4.4** JS disabled → `?tab=similar` fully rendered server-side.
**AC-P4.5** falsifiable (remove guard → double-fetch test fails; remove fetch → populate test fails).

## Constraints
- Follow existing style: server-rendered, vanilla JS, no HTMX, no new inline handlers.
- Dark theme, keyboard-safe, no native `confirm()`.
- Keep `make check` green; undefined-class backlog stays 42; run `ruff format` on edited `.md`/`.py`.
- Tests: `qa-tester` owns P5 (`tests/conformance/test_ui_similar_tab_lazy.py`).
- Deviations must be recorded in the plan (§12 open questions already note the three soft choices).

## Verification
`verifier` produces `docs/verification/VERIFY-2026-10-21-similar-tab-lazy-badge.md`. Note the
security angle: the fragment now makes the fuzzy computation reachable by any authenticated user per
case (bounded by `MAX_FUZZY_COMPARISONS`; ties to FIND-03/R5).
