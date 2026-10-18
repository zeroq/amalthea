# Security Review: UI Pagination Wave (PLAN-2026-10-18) — Revision

**Date:** 2026-10-18 (re-review)  
**Reviewer:** Security Auditor  
**Scope:** `ui/pagination.py`, `ui/views.py` (all paginated list views), `ui/templates/ui/_pager.html`, `ui/templates/ui/_list_summary.html`, and all templates consuming them (§4 P/U/R surfaces in PLAN).  
**Tree hashes reviewed:**  
- Committed: `5c58d9d88ef73576ccacc934619060776645aa65`  
- Working tree: as above + 30 modified files (see `git diff --name-only HEAD`) — no new commits since initial review.  
- Full suite: **2571 passed, 3 skipped**.

---

## Revision Summary

**M1 (Medium) — CLOSED.** The `observable_list` and `_render_case_list` views now correctly compute `unfiltered_count` from the org/visibility-scoped base queryset *before* applying filters, and set `filtered_from` only when a filter is active **and** actually narrows the set (mirroring `alert_list`). Verified by code inspection and conformance test `test_filtered_from_count_present_when_filtered`.

**L1 (`per_page` whitespace strictness) — ACCEPTED AS INTENTIONAL.** The exact-string allow-list (`ALLOWED_PER_PAGE = {"25","50","100","200"}`) rejects `" 50"`, `"50 "`, `"050"`. This is a deliberate strictness (not a bypass); the fallback to `DEFAULT_PAGE_SIZE=50` is safe. Documented; no code change.

**L2 (`case_share_list` materialises full list) — BOUNDED BY DESIGN, ACCEPTED.** The view loads all shares for a case into a Python list before paginating. However:
- Only the **case owner** (org owner or superAdmin) can access `case_share_list` (enforced by `resolve_case(..., write=True)` + `is_case_owner` at `ui/views.py:3437-3441`).
- Each share must be with a **distinct organisation** (`available_orgs` excludes already-shared orgs at line 3447-3449).
- Organisations are created only by **superAdmin** (`settings_organisations` view gated by `_require_superadmin_ui`).

Thus the share list is bounded by the total number of organisations in the system — a superAdmin-controlled quantity. An attacker who compromises an analyst account cannot create unbounded shares because they cannot reach the view for cases they don't own, and even a case owner cannot exceed the organisation count. The materialisation is a minor memory concern only at extreme scale (thousands of orgs); no code change required.

**New `per_page` selector in `_pager.html` — VERIFIED SAFE.** The control offers 25/50/100/200 links built with `{% querystring %}`; resets `page=1`; preserves all existing filters; URL-encodes correctly; no inline script/style; `aria-current` used correctly; `MAX_PAGE_SIZE=200` cannot be exceeded via this UI.

---

## Updated Findings

### Closed (was Medium)

#### ~~M1: `observable_list` `filtered_from` reports filtered count, not unfiltered count~~ **FIXED**
**Location:** `ui/views.py:2661-2679` (`observable_list`), `ui/views.py:622-638` (`_render_case_list`)  
**Fixed code (observable_list):**
```python
base_queryset = (
    Observable.objects.select_related("data_type", "owner_org")
    .prefetch_related("case_observables__case")
    .filter(org_q("owner_org", request.user))
)
unfiltered_count = base_queryset.count()  # ← captured BEFORE filters

queryset = base_queryset
if query:
    queryset = queryset.filter(data__icontains=query)
if type_filter:
    queryset = queryset.filter(data_type_id=type_filter)

has_filter = bool(query or type_filter)
page_obj = paginate(request, queryset.order_by("-created_at"))
filtered_from = (
    unfiltered_count if has_filter and unfiltered_count > page_obj.paginator.count else None
)
```
**Verifier test:** `test_filtered_from_count_present_when_filtered` passes — with 10 unlinked + 1 linked alert, default `scope=unlinked` yields `filtered_from=11`, `scope=all` yields `filtered_from=None`, `scope=linked` yields `filtered_from=11` with `page_obj.paginator.count=1`. Same pattern now in `_render_case_list` (lines 625, 636-638).

---

### Accepted (was Low)

#### L1: `per_page` allow-list rejects whitespace/padding — intentional strictness
**Location:** `ui/pagination.py:67,108-110`  
**Behaviour:** `per_page_raw in ALLOWED_PER_PAGE` is exact string match. `" 50"` → fallback to 50.  
**Assessment:** Not a bypass; the fallback is the safe default. The `page` parameter uses `int()` which strips whitespace, but `per_page` is a user-facing selector (now in `_pager.html`) that only emits exact values. Documented; no change.

#### L2: `case_share_list` materialises full share list before paginating — bounded by organisation count
**Location:** `ui/views.py:3470-3486`  
**Code:**
```python
shares_qs = case.shares.select_related("organisation").order_by("created_at")
# ...
all_shares = []
for share in shares_qs:
    all_shares.append({"share": share, "revoke_url": ...})
page_obj = paginate(request, all_shares)  # Paginator accepts sequence
```
**Assessment:** The list is loaded fully, but:
1. Access requires `is_case_owner(request.user, case)` (`ui/views.py:3440`) — only owner org or superAdmin.
2. Shares are unique per organisation (`available_orgs` excludes already-shared at line 3447-3449).
3. Organisations are superAdmin-created (`settings_organisations` gated by `_require_superadmin_ui`).

Maximum shares = number of organisations − 1 (owner org excluded). SuperAdmin controls this ceiling. No attacker-unbounded path exists. Accepted as a minor memory concern at extreme scale only; no code change.

---

## New Feature Verification: `per_page` Selector in `_pager.html`

**Template:** `ui/templates/ui/_pager.html:27-38`  
```html
{% if page_obj.paginator.count > 25 %}
  <div class="pager-sizes" role="group" aria-label="Results per page">
    {% with current=page_obj.paginator.per_page %}
      {% if current == 25 %}<span class="btn btn-sm" aria-current="true">25</span>
      {% else %}<a class="btn btn-sm" href="{% querystring per_page=25 page=1 %}">25</a>{% endif %}
      ... (50, 100, 200)
    {% endwith %}
  </div>
{% endif %}
```

**Verified:**
| Property | Result |
|----------|--------|
| Only offers 25/50/100/200 | ✅ Hard-coded in template |
| Resets to `page=1` | ✅ `{% querystring per_page=X page=1 %}` |
| Preserves existing filters | ✅ `{% querystring %}` merges, preserves `q`, `scope`, `type`, `closed` |
| URL-encodes special chars | ✅ Tested: `"`→`%22`, `&`→`%26`, `#`→`%23`, newline→`%0A`, `:`→`%3A`, `<`→`%3C`, `>`→`%3E`, `/`→`%2F` |
| No inline `style=`/`on*=`/`<script>` | ✅ Only static classes and `href` attributes |
| `aria-current="true"` on active size | ✅ `<span ... aria-current="true">` for current, `<a>` for others |
| `role="group"` + `aria-label` | ✅ Accessible per-page control group |
| Cannot exceed `MAX_PAGE_SIZE=200` | ✅ Template only emits 200 max; `pagination.py` allow-list rejects anything else |

**Hostile filter value test** (all produce safe, encoded URLs):
- `q=</a><script>alert(1)</script>` → `?q=%3C%2Fa%3E%3Cscript%3Ealert%281%29%3C%2Fscript%3E&per_page=100&page=1`
- `q=a"b&c#d` → `?q=a%22b%26c%23d&per_page=100&page=1`
- `q=%0a` → `?q=%250a&per_page=100&page=1` (double-encoded, safe)

---

## Re-verified: S2 (Authorization) & S4 (Output Safety)

| Check | Evidence |
|-------|----------|
| **S2: Org-scoping before pagination** | All P views: `alert_list` (line 408), `case_list` (623), `observable_list` (2664), `automation_list` (1768), `sources_list` (1831), `playbook_list` (1879 — global by design), `case_template_list` (2348 — global), `tag_list` (2559 — global), `settings_organisations` (3939 — superAdmin), `settings_users` (4137 — superAdmin), `settings_sources` (4345 — admin/org-scoped). Rails: case-detail (`case_detail:920-975`) uses `RAIL_CAP` on already-scoped querysets. |
| **S2: Cross-org isolation tests pass** | `test_cross_org_isolation_alert_list`, `test_cross_org_isolation_case_list`, `test_case_share_list_pagination` (outsider gets 404) — all 48 pagination tests pass. |
| **S2: `filtered_from` never leaks cross-org** | `unfiltered_count` always from `org_q`/`case_visible_q` base queryset. Verified: `alert_list:411`, `observable_list:2666`, `_render_case_list:625`. |
| **S4: No `|safe` in new partials** | `_pager.html`, `_list_summary.html` use only integer interpolation (`page_obj.number`, `paginator.count`, `start_index()`, `end_index()`) and static strings (`entity_name`). |
| **S4: `{% querystring %}` encodes output** | Verified above — all special chars encoded. |
| **S4: `entity_name` static** | Templates pass literals: `entity_name="Alert"`, `"Case"`, `"Observable"`, `"Run"`, `"Share"`, `"Token"`, `"Source"`, `"Playbook"`, `"Case template"`, `"Tag"`, `"User"`, `"Organisation"`. |
| **S4: Empty-state copy server-escaped** | Templates use `{{ ... }}` (auto-escaped). No `|safe` on `filtered_from` or counts. |
| **S4: Rails count lines safe** | `_tab_*.html` render `{{ alerts_total }}`, `{{ tasks_total }}`, etc. — integers only. |

---

## Open Items (Unchanged)

### O1: `observable_list` `data__icontains` COUNT performance unmeasured (Plan §11 Q7)
**Location:** `ui/views.py:2662-2663`, `2670` (`queryset.filter(data__icontains=query)`)  
**Status:** No measurement evidence in tree (no benchmark, no GIN/trigram index on `Observable.data`).  
**Risk:** With 100k+ observables, two `icontains` scans per request (page + count) plus third for `filtered_from` when filter active could be slow.  
**Deferred to:** Postgres wave (indexing/migration). **Explicitly not resolved in this wave.**

### O2: Rails have no "View all" link — accepted deviation
**Location:** Plan §11 Q4 amendment, §13 "Accepted deviation"  
**Status:** Documented and accepted. Rails (timeline, comments, audit, pages, procedures, TTPs, runs, attachments, linked-cases, playbook runs, source tokens) show "Showing latest N of M" with no link to a filtered main list (no such route exists).

---

## Verified Safe (Updated)

| Check | Evidence |
|-------|----------|
| **S1: `page` never 500s** | `ui/pagination.py:118-139`: `int()` in try/except → fallback to 1; `page_number <= 0` → 1; `EmptyPage` → clamp to last page. Tested: `abc`, `0`, `-3`, `1e9`, `999999`, `""`, `" 2 "`, `999999999999...` (101 digits) all HTTP 200. |
| **S1: `per_page` allow-list exact match** | `ui/pagination.py:67,108-110`: `ALLOWED_PER_PAGE = {"25","50","100","200"}`; `in` check is exact string equality. Tested: `"25"`✓, `" 50"`✗, `"25 "`✗, `"050"`✗, `"201"`✗, `"999999"`✗, `"abc"`✗ all fall back to 50. |
| **S1: `MAX_PAGE_SIZE=200` hard ceiling** | `ui/pagination.py:53,110,112`: `min(int(per_page_raw), MAX_PAGE_SIZE)` and `min(size, MAX_PAGE_SIZE)`. No code path can exceed 200. |
| **S2: Org-scoping preserved** | Every converted view applies org/visibility filter **before** `paginate()`. `case_share_list` gated by `resolve_case(..., write=True)` + `is_case_owner`. |
| **S2: Cross-org isolation** | 3 dedicated tests pass; `page_obj.paginator.count` matches visible set only. |
| **S3: COUNT query cost = 1 per request** | `Paginator.count` executes one `COUNT`; matches previous slice cost. No N+1. |
| **S3: `RAIL_CAP=200` with count lines** | `ui/views.py:920-975` (case_detail rails), `_tab_*.html` templates render `{% if *_total is not None and *_total > *_list|length %}` count line. |
| **S4: No `|safe`, no inline script/style** | Partials use only integer interpolation and static strings. `{% querystring %}` URL-encodes. |
| **S4: `entity_name` static** | Templates pass literals, never user input. |
| **S5: Counts never leak cross-org** | `page_obj.paginator.count` from org-filtered queryset. `filtered_from` from unfiltered org-scoped count. |
| **S5: No param echo in errors** | No 500s; invalid params silently fall back/clamp. |

---

## Test Coverage

All 48 tests in `tests/conformance/test_ui_pagination.py` pass (11.98s). Full suite: **2571 passed, 3 skipped**.

Coverage includes: slice correctness, summary truth, hostile `page` (9 values), clamp, filter×page matrix, empty vs filtered-empty, cross-org isolation, >200-row regression, `per_page` allow-list (4 allowed, 6 rejected), `filtered_from` logic (alert + case), rails caps, `case_share_list` pagination + per_page + clamp + hostile + cross-org.

---

## Verdict

**PASS — ALL MATERIAL FINDINGS ADDRESSED.** M1 fixed; L1/L2 accepted with evidence; new `per_page` selector verified safe; S2/S4 re-confirmed across parallel agent changes. O1 (unmeasured `icontains` COUNT) remains an explicit open item for the Postgres wave.

--- 

*End of Report*
