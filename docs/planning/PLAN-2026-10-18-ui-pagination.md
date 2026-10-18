# PLAN-2026-10-18 — UI pagination, honest counts and honest empty states (TODO §12.1)

**Status:** APPROVED 2026-10-18 — decisions locked in §11, frozen interface in §11a; P1/P2 handed to
`django-backend` and `django-frontend`.
**Source:** `TODO.md` §12.1 (audit 2026-10-12); §12.2 landed 2026-10-15 and made the truncation *worse to
notice*, because a filter now silently searches a capped 200-row window.

---

## 1. Summary

Every UI list is capped server-side and nothing on the page says so: an analyst cannot reach anything
older than the newest 200 rows of a given list, and an empty result is indistinguishable from "filtered
to nothing". This plan adds **one reusable server-side pagination primitive**, a **truthful list
summary** ("showing 51–100 of 347"), **honest empty states**, and applies them to the primary analyst
lists. It is deliberately server-rendered: plain `<a href="?page=2">` links that degrade perfectly with
no JavaScript, matching the keyboard-first, progressive-enhancement stance in AGENTS.md §6.

## 2. Goals

- **G1** An analyst can reach every row of a list, page by page, with a visible position indicator.
- **G2** No list ever renders a silent truncation: if rows were cut, the page says how many exist.
- **G3** An empty list states *which* of the two reasons applies (no rows at all vs. filtered to none),
  and offers the corrective action (clear the filter / create the first row).
- **G4** Pagination state survives the existing filters (`scope`, `closed`, `q`, `type`) in both
  directions: paging keeps filters, submitting a filter resets to page 1.
- **G5** Hostile input to the page parameter never produces a 500, and never escapes the org boundary.
- **G6** One implementation, reused: a shared `paginate()` helper plus one `_pager.html` partial — not
  four hand-rolled variants.

## 3. Non-goals

- **N1** The **API** is not changed. `GET /case`'s `timelineLimit` default/max stays as-is; §12.1's own
  note observes the API is not an analyst workaround. Cursor/keyset paging for the API is a separate item.
- **N2** **No HTMX/JS "infinite scroll"**. Links only. (A future enhancement; Q6 asks about keyboard only.)
- **N3** **Pickers are not paged** — see §4 class **K**. A `<select>` capped at 200 users needs *search*,
  not a pager; that is a different fix with a different UX. Tracked as a follow-up (§9 F1).
- **N4** No model/schema change, no migration. "Filtered from N" is a `COUNT`, not a stored total.
- **N5** No change to ordering, filters or row rendering. This is a navigation + honesty change.

## 4. Current state — inventory (verified in the tree today, not copied from the audit)

The audit named 7 sites; the real surface is larger. Classification:

- **P** — primary list, paged in this wave
- **R** — rail/panel inside a detail page (bounded window, "see all" candidate) — §5, Q4
- **K** — picker (dropdown/modal). Not a list; needs search, not paging — N3
- **V** — vocabulary/dashboard table: intrinsically small, exempt
- **U** — **unbounded** (no cap at all): the opposite defect, currently unnoticed — Q3

| Surface | View (ui/views.py) | Template | Class | Today |
|---|---|---|---|---|
| Alerts (triage queue, `scope=unlinked/linked/all`) | `alert_list:400` | `alert_list.html` | **P** | `[:PAGE_SIZE]` @415, silent |
| Cases (`closed=1` toggle) | `case_list:631` → `_render_case_list:594` | `case_list.html` | **P** | `[:PAGE_SIZE]` @607, silent |
| Observables (`q`, `type`) | `observable_list:2500` | `observable_list.html` | **P** | `[:PAGE_SIZE]` @2539, silent |
| Automations/runs | `automation_list:1665` | `automation_list.html` | **P** | `[:PAGE_SIZE]` @1677, silent |
| Settings → Users | `settings_users:3905` | `settings_users.html` | **P** | @2658 is a *picker*; the users list itself is **U** |
| Settings → Organisations | `settings_organisations:3740` | `settings_organisations.html` | **P**² | **U** |
| Sources (ingestion feeds) | `:1748` | `sources_list.html` | **P**² | **U** |
| Settings → Sources (tokens) | `settings_sources:4612` | `settings_sources.html` | **P**² | **U** |
| Playbooks | `:1765` | `playbook_list.html` | **P**² | **U** |
| Case templates | `:2224` | `case_template_list.html` | **P**² | **U** |
| Tags | `:2431` | `tag_list.html` | **P**² | **U** |
| Case shares | `:3327` | `case_share_list.html` | **P**² | **U** |
| Case-detail tabs: timeline, comments, observables, tasks, alerts, audit | `case_detail:906` | `_tab_*.html`, `case_detail.html` | **R** | some capped, some **U** (`pages`, `runs`, `procedures`, `ttps`) |
| Alert-detail: linked cases rail | `alert_detail:444` | `alert_detail.html` | **R** | merged into `merge_candidates` picker |
| Observable-detail: linked cases | — | `observable_detail.html:181` | **R** | **U** |
| Playbook detail: runs | — | `playbook_detail.html:93` | **R** | **U** |
| Source detail: mappings/recent alerts | — | `source_detail.html:55,184` | **R** | **U** |
| Taxonomy vocabularies (8 tables) | `taxonomy` | `taxonomy.html` | **V** | exempt by design |
| Dashboard widgets (3 tables) | `dashboard` | `dashboard.html` | **V** | recent-N by intent |
| Assignee pickers (`case_create:659`, `case_edit:2658`, `_render_case_list:620`, `case_detail:906`) | — | — | **K** | capped 200 |
| Task-tab person picker | `case_detail:881` | — | **K** | **UNCAPPED** (comment says so) |
| Merge-candidate picker | `alert_detail:444` | — | **K** | capped 200 |

² = only if Q1 chooses the wide scope.

**Inconsistency found while inventorying:** the assignee pickers are capped at 200 but the task-tab person
picker at `case_detail:881` is deliberately uncapped. Same class of control, two policies. Folded into §9 F1.

## 5. Design

### 5.1 Backend — one helper (`ui/pagination.py`)

```python
PAGE_SIZE = <Q2>           # rows per page
MAX_PAGE_SIZE = 200        # hard ceiling for any `per_page` parameter, if Q2 enables one

def paginate(request, queryset, *, param="page", size=None, per_page_param="per_page") -> Page: ...
```

Requirements:
- Uses `django.core.paginator.Paginator`. It must slice the **already org-filtered** queryset — callers
  keep their existing `org_q`/`case_visible_q` filters untouched and pass the *result* in. Pagination
  must never broaden a queryset (AC-P1.4).
- **Untrusted `page`**: non-integer, `0`, negative, whitespace, absurdly large → never a 500.
  Out-of-range clamps to the last page (Q5); junk falls back to page 1.
- Returns a `Page`, so templates use `page_obj.object_list`, `has_other_pages()`, `start_index()`,
  `end_index()`, `paginator.count`, `paginator.num_pages`.
- Keeps `.count()` cost honest: one `COUNT` per request is accepted (as today's slice also costs one).
  For the `observable_list` `q` path the count re-runs the `data__icontains` filter — same plan as the
  page query (Q7 concerns the index).

### 5.2 Frontend — one partial (`ui/templates/ui/_pager.html`)

Rendered below each `table-wrap`, with the summary line **above** the table:

```html
<p class="list-summary">Showing <strong>51–100</strong> of <strong>347</strong> alerts
   <span class="dim">(filtered from 1,204)</span></p>
...
<nav class="pager" aria-label="Alert pagination">
  <a rel="prev" href="{% querystring page=page_obj.previous_page_number %}">‹ Newer</a>
  <span class="pager-status" aria-current="page">Page 2 of 7</span>
  <a rel="next" href="{% querystring page=page_obj.next_page_number %}">Older ›</a>
</nav>
```

- `{% querystring %}` (Django 5.1+; this repo is on 5.2.17) preserves `scope`, `closed`, `q`, `type` and
  replaces `page` — **no hand-built query-string concatenation**, which is where filters get dropped.
- Disabled ends render as `<span aria-disabled="true">`, never a dead `<a href="#">`.
- The filter `<form method="get">` already has no `page` input, so submitting a filter resets to page 1
  for free (AC-P2.3 pins it with a test).
- Styling: reuse `.card`, `.btn`, `.dim`, `.mono` from `ui/static/ui/app.css`; new rules
  (`.pager`, `.pager-status`, `.list-summary`) go in the stylesheet, never inline (CSP, ADR-003).

### 5.3 Honest empty state

| Condition | Rendered |
|---|---|
| `paginator.count == 0` and no filter | Existing copy ("No cases open.") + the existing create CTA |
| `paginator.count == 0` and a filter is active | "No <entity> match your filters." + **Clear filters** link to the bare URL |
| `paginator.count > 0` | Summary line + table + pager |

The "filtered from N" figure needs an **unfiltered** count, computed **only when a filter is active**
(one extra `COUNT` on the unfiltered, org-scoped queryset) — cheap, and it is what makes G2/G3 honest.

## 6. Phases, tasks and acceptance criteria

### P1 — Backend primitive (`django-backend`)
- **T1.1** `ui/pagination.py` with `paginate()` per §5.1 + module docstring explaining the untrusted-param policy.
- **T1.2** Convert `alert_list`, `_render_case_list` (cases only — **not** the assignees picker),
  `observable_list`, `automation_list` to `paginate()`; add `page_obj` and the summary context
  (`total_count`, `filtered_from`) to the template context.
- **T1.3** `tests/conformance/test_ui_pagination.py` (see §8) — written by `django-backend`, hardened by `qa-tester`.
- **AC-P1.1** A list with `PAGE_SIZE + 1` rows shows `PAGE_SIZE` rows and links to page 2, which shows the rest.
- **AC-P1.2** `?page=abc`, `?page=0`, `?page=-3`, `?page=999999` → HTTP 200, never a traceback; out-of-range clamps to the last page.
- **AC-P1.3** `page_obj` is built from the org-filtered queryset; the count line never includes another org's rows.
- **AC-P1.4** No caller's filtering is loosened by the refactor: the existing org-scoping and viewer-denial suites stay green.

### P2 — Pager UI + honest states (`django-frontend`)
- **T2.1** `_pager.html` + `_list_summary.html` (or one partial with both) per §5.2/§5.3, styled from `app.css`.
- **T2.2** Apply to the four primary templates (`alert_list.html`, `case_list.html`, `observable_list.html`, `automation_list.html`).
- **T2.3** Empty-state split per §5.3, with a **Clear filters** affordance on the filtered-empty path.
- **AC-P2.1** Every paged list renders "Showing X–Y of Z" with truthful numbers (assert the exact strings).
- **AC-P2.2** `rel="prev"`/`rel="next"`, `aria-current="page"` and an accessible name on the `<nav>`; the pager is reachable and operable with the keyboard alone (Tab + Enter), no JS.
- **AC-P2.3** Paging keeps `scope`/`closed`/`q`/`type`; submitting a filter drops `page`.
- **AC-P2.4** Filtered-to-none and genuinely-empty render **different** copy (a test asserts both, and asserts the clear-filters link).
- **AC-P2.5** No inline `style=`/`<script>`; no native `confirm()`; existing template tests unaffected.

### P3 — Wide scope (only if Q1/Q3 ask for it)
- **T3.1** Settings Users/Organisations, Sources, Settings Sources, Playbooks, Case templates, Tags, Case shares.
- **T3.2** Add the missing **caps** to the `U` surfaces (§4) — a cap + summary is a strict improvement even where a full pager is overkill (Q3).
- **T3.3** Rails (`R`): either page in place or "showing N of M → View all" (Q4).

### P4 — Verification
- **T4.1** `qa-tester` re-derives ACs and hunts edge cases (empty page, last page, 1-row list, `PAGE_SIZE`-exact list, filter+page interaction, cross-org attempts).
- **T4.2** `verifier` grades the ACs against this plan (independent report in `docs/verification/`).
- **T4.3** `security-auditor` reviews the parameter handling + org-scoping interaction (`docs/reviews/`).

## 7. Security

- **S1** Pagination is a **query-shaped** surface: every paged queryset stays org-filtered by construction
  (`org_q`/`case_visible_q` applied *before* `paginate()`), and tests assert a cross-org row never appears
  on any page (§8).
- **S2** `page`/`per_page` are untrusted (CWE-20): parsed, bounded, clamped; a `per_page` above
  `MAX_PAGE_SIZE` is refused/clamped, never honoured (that would be a DoS lever).
- **S3** Counts must not become an oracle: the summary reflects the caller's own visibility only.
- **S4** Viewer/role gating is untouched — the pager adds no route and no verb; the existing
  `test_viewer_readonly_ui.py` sweep must stay green.
- **S5** No new inline script/style (CSP `style-src 'self'`); links only, so nothing to sanitize.

## 8. Testing

New `tests/conformance/test_ui_pagination.py` (conformance, not unit — it asserts rendered HTML):

1. Slice correctness: `PAGE_SIZE+1` rows → page 1 has `PAGE_SIZE`, page 2 has 1; no row repeats or vanishes.
2. Summary truth: exact "showing X–Y of Z" for first/middle/last/single-page cases.
3. Hostile `page`: `abc`, `0`, `-1`, `1e9`, `999999`, `""`, `" 2 "` → 200, sane page, no 500.
4. Clamp: `?page=999` renders the last page (and says so).
5. Filter×page matrix for `alert_list` (`scope`), `case_list` (`closed`), `observable_list` (`q`,`type`): paging preserves; filtering resets.
6. Empty vs filtered-empty: distinct copy; clear-filters link points at the bare URL.
7. Tenant isolation: seed rows in two orgs; walk every page; assert no foreign row and that `count` matches the visible set.
8. Regression for the motivating bug: a >200-row list is fully reachable (the §12.1 failure).
9. Existing suites stay green: `test_ui_p2_wiring.py` (§12.2 filters), `test_tenant_scoping.py`,
   `test_viewer_readonly_ui.py`, `test_ui_loop.py`.

## 9. Risks

- **R1 — Template/HTML test churn.** Several conformance tests assert rendered strings adjacent to the
  lists. Mitigation: keep surrounding markup identical; add, don't restructure.
- **R2 — `COUNT` cost on big JSONB tables.** `observable_list`'s `q` filter is an unindexed `icontains`
  today; paging adds a second such scan (the count) and the "filtered from" figure a third when a filter
  is active. Mitigation: measure with 100k rows (Q7); if it hurts, cap the unfiltered count or index
  `data`. Acceptable for now because the current slice already pays one `COUNT`.
- **R3 — Rails (`R`) get slower, not better.** Adding pagination to six tabs multiplies the work; that is
  why they sit in P3/Q4 rather than P2.
- **R4 — Scope creep into pickers.** The 200-row assignee cap is a real bug at scale, but search ≠ pager.
  Kept out (N3, F1).
- **R5 — In-flight verification debt.** §12.5's report and §12.8-fu2/3 are still open; this wave should not
  merge on top of an unverified UI surface without noting it (P4 runs `verifier` on *this* plan).

## 10. Follow-ups (recorded, not implemented here)

- **F1** Picker policy: search/typeahead for user pickers; reconcile the capped (200) vs uncapped
  (`case_detail:881`) inconsistency. New TODO item.
- **F2** §12.2's remaining half: the observables filter now searches a *paged* corpus — confirm the empty
  state explains "filtered to none" (covered by AC-P2.4) and consider an index for `data`.
- **F3** Splitting Tailwind source from the committed artifact (§7.4) — pagination adds CSS classes, so the
  drift noted on 2026-10-18 gets slightly worse until §7.4 lands.

## 11. Decisions (locked 2026-10-18 by the user; the eight open questions below are resolved)

| Q | Decision |
|---|---|
| **Q1 — Scope breadth** | **(b) Every `P` surface in §4** (all 12 list views), not just the four primary lists. |
| **Q2 — Page size** | **50 per page default**, optional `?per_page=25|50|100|200`, hard-clamped to `MAX_PAGE_SIZE = 200`. A pager is theatre if page 1 already holds the truncation point. |
| **Q3 — Unbounded (`U`) surfaces** | **Add a hard cap in this wave.** A silent cap is a defect; an unbounded query is a worse one. Cap + honest count; full paging only where the surface is a primary list. |
| **Q4 — Detail rails (`R`)** | **"Showing latest N of M" + a View-all link *only where a target route exists*.** Amendment: most rails (timeline, comments, audit, pages, procedures, TTPs, runs, attachments) have **no** filtered main-list equivalent, so they get the count line without a link rather than a link to a page that cannot express the filter. |
| **Q5 — Out-of-range `?page=`** | **Clamp to the last page** (200, no redirect); junk/`0`/negative → page 1. |
| **Q6 — Keyboard** | **Links-only in this wave.** Page shortcuts are a P3 nicety; the `?` cheat-sheet is not touched. |
| **Q7 — `COUNT` cost** | **Measure before P2 ships**, at ~100k observables; if the filtered count is slow, add the index in the same wave (and record it). |
| **Q8 — API** | **Untouched** (N1 stands): no cursor paging on `GET /case` in this wave. |

---

## 11a. Frozen interface (agents must not deviate without recording it here)

```python
# ui/pagination.py
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def paginate(
    request: HttpRequest, queryset: QuerySet, *, size: int = DEFAULT_PAGE_SIZE
) -> Page: ...
```

- Templates receive exactly two context keys from pagination: **`page_obj`** (a `django.core.paginator.Page`)
  and **`filtered_from`** (`int | None` — the unfiltered, org-scoped row count, computed by the *view* only
  when a filter is active; `None` when no filter is set or the surface is unfiltered).
- `?per_page` is honoured only within `{"25", "50", "100", "200"}`; anything else falls back to `size`.
- Every paged queryset is org-filtered **before** it reaches `paginate()`.
- Template partials: **`ui/templates/ui/_pager.html`** (nav + links) and **`ui/templates/ui/_list_summary.html`**
  (the "Showing X–Y of Z" line). CSS classes: `.list-summary`, `.pager`, `.pager-status`, `.pager-disabled`.
- Links are built with Django 5.1+'s **`{% querystring page=… %}`** — never string-concatenated.

---

## 12. References

- `TODO.md` §12.1, §12.2, §12.4 (bulk actions share the selection UX), §12.9 (list/table conventions), §7.4.
- AGENTS.md §6 (dark theme, keyboard-first, no native dialogs, CSP, progressive enhancement).
- `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (house style for list/empty-state expectations).
- Precedent for a shared UI primitive + conformance tests: `tests/conformance/test_ui_p2_wiring.py`,
  `tests/conformance/test_ui_css_tokens.py`.

---

## 13. Deviations & post-handoff decisions (2026-10-18, planner)

### D1 — `RAIL_CAP = 200`, not 50, and every cap shows a line
The backend agent capped the case-detail rails at an ad-hoc `rail_cap = 50`. That silently hides
*working sets* (tasks, custom pages, the TTP vocabulary) after 50 rows with no indication — the exact
dishonesty this plan exists to remove. **Decision:** add `RAIL_CAP = 200` to `ui/pagination.py` as a
frozen constant beside `MAX_PAGE_SIZE` and use it for every rail; **no cap may exist without a visible
count line** for the same list.

### D2 — The case-detail rails were not capped at all
`alerts_qs`, `tasks_qs`, `observables_qs`, `attachments_qs`, `comments_qs`, `timeline_qs` and
`audit_logs_qs` are still passed to the templates **uncapped** (`ui/views.py:854-866`), so Q3 is only
half-done. They must be capped at `RAIL_CAP` with `alerts_total` / `tasks_total` / `observables_total` /
`attachments_total` / `comments_total` / `timeline_total` / `audit_logs_total` passed as well.

### D3 — Chronological rails keep ascending display, but show the *newest* N
For a rail rendered oldest-first (`timeline`, `tasks`, `observables`, `comments`, `pages`, `procedures`),
truncating with `[:N]` would show the *oldest* rows while the copy says "latest". The slice must take the
newest N and re-order back to the display order (e.g. `list(qs.order_by("-date","-id")[:N])[::-1]` for
the timeline, which `live.js` appends to — its ascending order is contractual and must not change).

### D4 — `case_share_list` bypassed the frozen interface
`ui/views.py:3427-3451` hand-rolled a `Paginator` over a fully-materialised Python list, with a bare
`except Exception` and no `?per_page` support. It must call `paginate(request, all_shares)` like every
other `P` surface.

### D5 — `pages` and `ttps` render their caps with no line
`_tab_general.html:125` shows a `.count` badge for a list capped at `rail_cap`, and
`_procedure_form_modal_body.html:34` renders the TTP `<select>` from a capped global vocabulary — both
silent truncation. Each gets a count/hint line (the `ttps` picker's proper fix — search — is follow-up
**F1**).

### Accepted deviation — rails have no "View all" link (plan §11 Q4 amendment)
Recorded by the frontend agent: timeline, comments, audit, pages, procedures, TTPs, runs, attachments,
linked-cases, playbook runs and source tokens have no filtered main-list route, so they get the count
line only. Confirmed against `ui/urls.py` — accepted, no link is invented.

### Note — `?per_page` is honoured per surface, not uniformly
The helper always reads `per_page`; surfaces that only need a cap (rails) do not accept it. Only `P`
surfaces expose `?per_page` in their pager UI.

### Sanctioned test edits (N5 — tests are otherwise frozen)
This wave deliberately changes what two existing tests assert. Both are contract changes *introduced by
this plan*, not weakening of coverage; the verifier should check that each edit is still at least as
strict as the assertion it replaced:
- `tests/conformance/test_ui_loop.py` — 2 assertions moved from the old slice/context shape to `page_obj`.
- `tests/conformance/test_ui_p1_wiring.py::test_case_detail_passes_attachments_queryset` — `attachments`
  is now a capped list rather than a queryset, so the test asserts the capped behaviour.

### D3 correction (applied after review)
The "newest N" rule was mechanically applied to the `ttps` **vocabulary**, which is not a log: taking the
alphabetically last `RAIL_CAP` names would hide `T10xx` from the procedure modal entirely. A vocabulary
is capped **head-first in display order**; only logs and working sets are capped to their newest rows.
`RAIL_CAP` and the TTP slice carry a comment stating the distinction.
