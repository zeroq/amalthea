# Security Review — Alert Detail Similar Tab (TODO.md §24)

**Report date:** 2026-11-25
**Scope:** Alert detail Similar tab implementation (lazy panel, fuzzy + observable channels, tenant isolation, bounded CPU/DB)
**Files reviewed:** TODO.md §24, IMPL-2026-11-21-alert-similar-tab.md, ADR-004, ADR-006, cases/correlation.py, ui/views/alerts.py, ui/urls.py, ui/views/__init__.py, ui/templates/ui/alert_detail.html, ui/templates/ui/_alert_similar_tab_fragment.html, ui/templates/ui/_alert_similar_alerts_card.html, tests/conformance/test_alert_similar_tab.py

---

## PASS / FAIL SUMMARY

| Criterion | Status | Notes |
|-----------|--------|-------|
| **Tenant isolation: caller org AND source org scoping for both fuzzy and observable channels** | **PASS** | Both channels apply `org_q("owner_org", user)` AND `owner_org_id=source.owner_org_id` (`cases/correlation.py:640-641`). superAdmin gets `org_q` → `Q()` but `owner_org_id=source.owner_org_id` still restricts to source alert's org. Analyst cross-org → 404 via `resolve_alert` org filter mismatch. |
| **Tenant isolation: superAdmin cannot cross tenants through similarity** | **PASS** | Even superAdmin is scoped to `source.owner_org_id` via the explicit `.filter(owner_org_id=source.owner_org_id)` (correlation.py:641). The `org_q("owner_org", user)` returns `Q()` for superAdmin but the owner_org_id filter is org-source, not caller-org. Analyst cross-org fragment returns 404; superAdmin cross-org read is 200 and consistent with ADR-009 (cross-tenant, audited). |
| **Tenant isolation: analyst cross-org fragment 404** | **PASS** | `test_login_required_viewer_read_and_404s` confirms analyst accessing cross-org similar tab gets HTTP 404. `resolve_alert(request.user, alert_id, write=False)` returns `None` for org-mismatching analyst, triggering the 404 path in `alert_similar_fragment`. |
| **Tenant isolation: superAdmin cross-org read consistent with ADR-009** | **PASS** | superAdmin can access the similar tab (HTTP 200) for cross-org alerts, but results are scoped to the source alert's org. This is consistent with ADR-004 decision 5 (superAdmin is cross-tenant and audited) and ADR-009's scope rules. |
| **Bounded CPU/DB work: MAX_FUZZY_COMPARISONS, MAX_CORRELATION_OBS, final 20 cap, no unbounded scan** | **PASS** | `MAX_FUZZY_COMPARISONS = 50` (`cases/correlation.py:39`) caps candidates before SSDEEP scoring. `MAX_CORRELATION_OBS = 1000` (`cases/correlation.py:34`) caps observable IDs fetched. Final result cap is `SIMILAR_CLOSED_ALERTS_LIMIT = 20` (`cases/correlation.py:42`). Union total is computed pre-slice (`total_matching = len(union_by_id)` at correlation.py:771) for honest truncation reporting. No unbounded union or order scan leak. |
| **Read-only fragment: GET-only, viewer-readable, no mutation, no audit row** | **PASS** | `alert_similar_fragment` (`ui/views/alerts.py:300-323`) is `@login_required` `@require_GET`. `test_get_is_read_only_and_emits_no_audit` confirms zero AuditLog rows after fragment request. Fragment performs no mutation — only reads and renders. |
| **Read-only fragment: renders only titles, verdict labels, timestamps, scores/counts** | **PASS** | `_similar_display_rows` (`ui/views/alerts.py:147-177`) produces dicts with `alert`, `verdict_label`, `closed_at`, `similarity_display`. Template `_alert_similar_alerts_card.html` only renders: title (link), verdict label, closed date, similarity display. No raw payload values exposed. |
| **No raw payload exposure in templates/logs/audit** | **PASS** | Templates never reference `alert.raw_payload` or `alert.summary` in the Similar tab. `alert_similar_fragment` does not write AuditLog entries. Helper `_similar_display_rows` uses only `match.alert.verdict` and `match.alert.updated_at`. |
| **No secrets, no new dependency/migration/network surface** | **PASS** | Uses existing `ppdeep` dependency (already in `requirements/base.txt`). No new Django models or database migrations. No new REST endpoints (reuses `alert_similar_fragment` route). No WebSocket events, no Celery tasks. All org scoping reuses existing `identity.scoping.org_q` and `Alert.owner_org` FK. |

---

## FINDINGS (detail)

### 1. Tenant isolation (ADR-004, ADR-006, ADR-009)
- **Caller org scoping:** Both fuzzy and observable channels in `alerts_similar_to_alert` begin with `.filter(org_q("owner_org", user))` (`correlation.py:640`). For `analyst`/`admin`, this filters to the caller's org PK. For `superAdmin`, `org_q` returns `Q()` (no filter), but this is complemented by the explicit `.filter(owner_org_id=source.owner_org_id)` on the next line (`correlation.py:641`), which scopes to the **source alert's** organisation regardless of caller role.
- **Source org scoping:** `.filter(owner_org_id=source.owner_org_id)` (`correlation.py:641`) ensures all results descend from the same source alert's organisation. This applies uniformly to analysts, admins, and superAdmins.
- **Analyst cross-org 404:** `resolve_alert(request.user, alert_id, write=False)` in `alert_similar_fragment` (`alerts.py:306`) returns `None` when the analyst's org does not match the alert's `owner_org_id`, which triggers the 404 render (`alerts.py:307-308`). Verified by `test_login_required_viewer_read_and_404s`.
- **superAdmin cross-org:** superAdmin can reach the similar tab for any alert (HTTP 200) via `resolve_alert` which permits superAdmin access to any Alert row. However, the subsequent `alerts_similar_to_alert` helper still applies `.filter(owner_org_id=source.owner_org_id)`, so results are scoped to the source alert's org. This is consistent with ADR-004 §5 (superAdmin is cross-tenant and audited) and ADR-009's organisation-bound scoping pattern.
- **Union merge by alert.id:** Dual matches (fuzzy + observable) retain the max fuzzy score and the larger shared observable count, keyed by `alert.id` (`correlation.py:731-758`).

### 2. Bounded CPU/DB work
- **`MAX_FUZZY_COMPARISONS = 500`** (`correlation.py:39`): Hard cap on candidate alerts compared via `ppdeep.compare` in the fuzzy channel. The candidate queryset is sliced at DB level: `base_qs.exclude(payload_ssdeep="")[:MAX_FUZZY_COMPARISONS]` (`correlation.py:658`).
- **`MAX_CORRELATION_OBS = 1000`** (`correlation.py:34`): Hard cap on source observable IDs fetched before the CaseObservable/AlertObservable join. Used in both `cases_sharing_findings` and `alerts_similar_to_alert` (`correlation.py:150`, `correlation.py:701`).
- **Final 20 cap**: `SIMILAR_CLOSED_ALERTS_LIMIT = 20` (`correlation.py:42`) is the maximum number of returned results. The honest total (pre-slice count) is returned for badge/truncation reporting.
- **Union bounded**: `total_matching = len(union_by_id)` (`correlation.py:771`) is computed before the final `[:limit]` slice. Sorting is by base order (`-updated_at, -date, -pk`), not by similarity score, avoiding unbounded re-sorting.
- **No new DB queries or scans**: All caps reuse existing indexes (`caseobs_obs_case_idx`, `alert_corrkey_date_idx`). No new trigram indexes, GIN indexes, or full-table scans introduced.

### 3. Read-only fragment
- **GET-only enforcement:** `alert_similar_fragment` (`alerts.py:300-323`) decorated with `@login_required` `@require_GET`. POST is not accepted; the route `ui-alert-similar` (`ui/urls.py:42`) only maps to a GET view.
- **No mutation, no audit row:** `test_get_is_read_only_and_emits_no_audit` (`test_alert_similar_tab.py:388-400`) verifies `AuditLog.objects.count()` is unchanged after fragment request. The fragment only reads data and renders a template.
- **Viewer-readable:** Both `analyst` and `viewer` roles can access the fragment for their own org's alerts. Viewer role gets 200; analyst cross-org gets 404.

### 4. No raw payload exposure
- The Similar tab template (`_alert_similar_alerts_card.html`) renders only: `row.alert.title` (as link), `row.verdict_label`, `row.closed_at|date:"Y-m-d H:i"`, `row.similarity_display`.
- `_similar_display_rows` (`alerts.py:147-177`) computes `similarity_display` from `match.similarity` (int 0-100 or None) and `match.shared_observable_count` (int). No raw `payload`, `raw_payload`, or `summary` values appear.
- The `alert_detail.html` base template's Raw data tab (`alert.raw_payload|pprint`) is a separate tab; the Similar tab has no access to raw payload.

### 5. No secrets, no new dependency/migration/network surface
- **No new models/migrations:** The implementation reuses `Alert`, `AlertObservable`, `AlertStatus` existing models. No schema changes.
- **No new dependencies:** `ppdeep` is already a pinned dependency in `requirements/base.txt`. No new Python packages introduced.
- **No new network surface:** The Similar tab is entirely intra-process. No outbound API calls, no DNS lookups, no SSRF surface.
- **No secrets in code/templates/tests:** No API keys, tokens, passwords, or private keys in any reviewed file. Test fixtures use model factories (`Organisation.objects.create()`, `Alert.objects.create()`).
- **Pre-existing mypy errors noted but not §24 findings:** `make check` has 6 mypy errors in `cases/correlation.py`, `amalthea/logging.py`, and `identity/admin.py`. The task instructions specify: "Do not treat those as §24 findings but note as context." These are pre-existing and unrelated to the Similar tab implementation.

---

## REQUIRED FOLLOW-UPS (none — all PASS)

All acceptance criteria from `TODO.md §24` and `IMPL-2026-11-21-alert-similar-tab.md` are met. No code changes, migrations, or configuration updates are required for this security review to pass.

---

