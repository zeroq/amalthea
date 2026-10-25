# Verification Report: PLAN-2026-10-22 — Case Template defaults and source-alert payload
Plan: docs/planning/PLAN-2026-10-22-case-template-defaults.md
Verified: 2026-10-22 18:45 UTC
Status: Pass

## Summary
- Total ACs: 6
- Met: 6
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC1 — Authoring defaults
**Status: Met**

**Evidence:**
- `ui/views.py:2958-2970` — `case_template_create` GET pre-fills `form_tasks` and `form_custom_fields` with deep copies of `DEFAULT_TEMPLATE_TASKS` and `DEFAULT_TEMPLATE_CUSTOM_FIELDS`
- `ui/views.py:2988-2996` — Empty POST submission substitutes defaults: `if not data["tasks"]: data["tasks"] = copy.deepcopy(DEFAULT_TEMPLATE_TASKS)` (same for custom_fields)
- `cases/views.py:2174-2209` — `_create_case_template` (API) does **not** inject defaults; omitted/empty `tasks` and `custom_fields` stay as provided
- `ui/templates/ui/case_template_form.html:85-95` — Help text documents token with `&#123;&#123; alert.raw_payload &#125;&#125;`
- `tests/conformance/test_case_template_defaults.py:287-334` — Tests `test_ui_create_form_prefills_default_task_and_custom_field`, `test_ui_create_empty_submission_keeps_defaults`, `test_api_create_template_does_not_inject_defaults` all pass

### AC2 — Token rendering
**Status: Met**

**Evidence:**
- `cases/template_defaults.py:118-189` — `render_source_alert_payload` replaces `{{ alert.raw_payload }}` with earliest same-org alert payload
- `cases/template_defaults.py:59-69` — `earliest_alert_for_case` orders by `date`, `created_at`, `id` and filters `owner_org_id=case.owner_org_id`
- `cases/views.py:2106-2110` — `_apply_template` calls `render_source_alert_payload(str(spec.get("description") or ""), case)` for each task
- `cases/template_defaults.py:144-151` — Missing alert returns readable fallback without sentinel/marker
- `cases/template_defaults.py:139-140` — No token returns description byte-for-byte unchanged
- `tests/conformance/test_case_template_defaults.py:96-236` — Tests `test_no_token_returns_description_byte_for_byte`, `test_no_alert_falls_back_without_sentinel`, `test_earliest_alert_ties_break_on_created_at`, `test_earliest_alert_ignores_cross_org_alerts` all pass

### AC3 — Bounded excerpt
**Status: Met**

**Evidence:**
- `cases/template_defaults.py:31` — `MAX_SOURCE_ALERT_PAYLOAD_BYTES = 64 * 1024` (64 KiB)
- `cases/template_defaults.py:88-98` — `_truncate_utf8_bytes` truncates at UTF-8 byte boundary without splitting multi-byte chars (`errors="ignore"`)
- `cases/template_defaults.py:101-115` — `_build_dynamic_fence` creates fence longer than any backtick run in payload, guaranteeing closed fence
- `cases/template_defaults.py:176-178` — Truncation marker appended **outside** fence only when truncated
- `cases/template_defaults.py:183-187` — Sentinel appended once at end on own line with preceding blank line (prevents P0 regression)
- `cases/template_defaults.py:22` — Sentinel is Markdown link-reference `[//]: # (amalthea:source-alert-payload)` — invisible in rendered Markdown
- `cases/template_defaults.py:72-85` — `has_source_payload_sentinel` requires exact standalone line match (own-line detection)
- `tests/conformance/test_case_template_defaults.py:184-215` — Tests `test_large_payload_is_capped_fenced_and_marked`, `test_multibyte_truncation_never_splits_a_character`, `test_empty_payload_renders_a_closed_fence` pass
- `tests/conformance/test_case_template_defaults.py:129-153` — Tests `test_trailing_prose_does_not_render_the_sentinel_visibly` (P0 regression), `test_token_followed_by_newline_and_prose_keeps_sentinel_last` pass
- `tests/conformance/test_case_template_defaults.py:167-182` — Tests `test_sentinel_detection_requires_an_own_line`, `test_existing_sentinel_is_not_duplicated` pass

### AC4 — Full payload access
**Status: Met**

**Evidence:**
- `ui/views.py:1923-1972` — `task_detail` shows trigger only when `show_source_payload = bool(sentinel present AND alert exists AND resolve_alert permits)`
- `ui/views.py:1976-2027` — `task_source_payload` fragment decorated with `@never_cache`, calls `resolve_alert` (sharee gets 404)
- `ui/templates/ui/task_detail.html:79-98` — Trigger anchor with HTMX `hx-get` to modal; plain link fallback
- `ui/templates/ui/_task_source_payload.html` — Modal body renders `payload_json` in escaped `<pre>`
- `ui/templates/ui/_task_source_payload_loading.html` — Loading state with fallback link
- `cases/template_defaults.py:192-243` — `format_full_payload` implements 8 MiB cap: compact first, pretty fallback, oversize message
- `tests/conformance/test_case_template_defaults.py:381-562` — Tests `test_task_detail_shows_exactly_one_trigger_when_rendered`, `test_task_detail_hides_trigger_without_sentinel`, `test_task_edit_removes_sentinel_and_hides_trigger`, `test_task_source_payload_returns_tail_truncated_from_the_excerpt`, `test_task_source_payload_is_never_cached`, `test_task_source_payload_hides_raw_alert_from_case_sharee`, `test_task_source_payload_allows_owner_and_superadmin`, `test_task_source_payload_anonymous_and_post_guards`, `test_format_full_payload_compacts_when_pretty_exceeds_cap`, `test_format_full_payload_refuses_oversize_compact`, `test_format_full_payload_skips_pretty_when_estimate_exceeds_cap` (M2 regression) all pass

### AC5 — Safety edges
**Status: Met**

**Evidence:**
- `cases/views.py:2051-2058` — `_resolve_case_template` uses `_as_uuid`; non-UUID returns `None` → 404
- `ui/views.py:2872-2878` — `case_apply_template` calls `_resolve_case_template`; `None` → `Http404`
- `tests/conformance/test_case_template_defaults.py:361-373` — `test_ui_apply_template_non_uuid_is_404_not_500` passes
- `tests/conformance/test_case_template_defaults.py:586-597` — `test_payload_script_is_escaped_in_markdown` verifies `<script>` escaped in rendered Markdown
- `ui/views.py:2005-2006` — `task_source_payload` has `@require_GET`; POST returns 405 (tested in `test_task_source_payload_anonymous_and_post_guards`)
- `ui/views.py:1985-1993` — Anonymous access to fragment redirects (302) via `@login_required`
- `tests/conformance/test_case_template_defaults.py:565-583` — `test_task_source_payload_anonymous_and_post_guards` passes

### AC6 — Quality gates
**Status: Met**

**Evidence:**
- `python3 manage.py makemigrations --dry-run --check` → "No changes detected" (no migration generated)
- `.venv/bin/ruff check .` → "All checks passed!"
- `make check` → ruff format passes, mypy reports "no issues found in 124 source files", and the rest of the gate passes
- `python3 manage.py check` → "System check identified no issues (0 silenced)"
- `scripts/tailwindcss` compile → CSS artifact up to date (no drift)
- `bash scripts/secret-scan.sh --staged` → "clean — no secrets"
- `python3 -m pytest tests/conformance/test_case_template_defaults.py tests/conformance/test_tenant_scoping.py` → 131 passed in ~18s
- All 31 tests in `test_case_template_defaults.py` pass (includes 2 new M1/M2 regression tests)
- All 100 tests in `test_tenant_scoping.py` pass (includes new `ui-task-source-payload` route in param map)

## Post-Audit Hardening Verification

### M1 — Cross-Org Read Audit on UI Endpoints (Remediated)
**Files:** `ui/views.py:1930-1946` (`task_detail`), `ui/views.py:2009-2025` (`task_source_payload`)

**Verification:**
- Both views now call `maybe_record_cross_org_read` with `target_type="Task"`, `target_org_field="owner_org"`, matching the API task-detail pattern (`cases/views.py:284-292`, `cases/views.py:477-485`)
- Same-org reads by the case owner do **not** create audit rows (verified by test)
- Regression coverage: `test_superadmin_task_reads_write_cross_org_audit_log` asserts:
  - SuperAdmin cross-org `task_detail` → 1 audit row
  - SuperAdmin cross-org `task_source_payload` → 2nd audit row (separate read event)
  - Owner same-org `task_detail` + `task_source_payload` → 0 additional rows

**Conclusion:** M1 fix matches intent; tenant isolation unchanged; audit gap closed.

### M2 — Pretty-Print Memory Allocation Before Size Check (Remediated)
**File:** `cases/template_defaults.py:221-225` (`format_full_payload`)

**Verification:**
- Compact JSON is serialized first; `compact_bytes = len(compact_json.encode("utf-8"))`
- Before pretty-printing, a conservative `compact_bytes * 2 > MAX_FULL_PAYLOAD_BYTES` estimate is checked
- If estimate exceeds cap, the **complete compact JSON is returned** — no pretty allocation, no data loss
- Only when estimate passes is pretty JSON allocated; a final exact check falls back to compact if needed
- Regression coverage: `test_format_full_payload_skips_pretty_when_estimate_exceeds_cap` verifies the 2× heuristic triggers before pretty allocation, returning compact JSON

**Conclusion:** M2 fix matches intent; no memory bloat; payload completeness preserved (compact JSON is complete, just less readable).

## Test Coverage Summary

| Test File | Tests | Status |
|-----------|-------|--------|
| `tests/conformance/test_case_template_defaults.py` | 31 | All pass |
| `tests/conformance/test_tenant_scoping.py` | 100 | All pass |
| **Targeted total** | **131** | **All pass** |
| **Full suite (per lead)** | 3099 passed, 3 skipped | Cited |

## Deviations

None. The implementation fully conforms to the approved plan. The minor deviations noted in the plan's "Implementation record" section (sentinel as Markdown link-reference, dynamic fence length, full payload as raw JSON in modal, 8 MiB cap, sharee/same-org guards, `never_cache`) are all **intentional and documented** in the plan itself, not unintentional drift.

## Residual Risks (Accepted)

1. **Sentinel stored in user-editable Markdown** — As noted in the plan's risks, own-line detection limits but cannot eliminate intentional spoofing. The modal independently verifies alert access via `resolve_alert`, so spoofing only yields a confusing UI element, not a data leak. **Accepted tradeoff.**

2. **L1 — No rate limiting on `task_source_payload`** — Endpoint is authenticated, GET-only, `@never_cache`, and 8 MiB display-capped. Per-user rate limiting is left as deployment/edge-control work. **Accepted for MVP.**

No other residual risks. M1 and M2 are remediated with regression tests.

## Commands/Results

| Command | Result |
|---------|--------|
| `python3 manage.py makemigrations --dry-run --check` | No changes detected |
| `python3 manage.py check` | System check identified no issues (0 silenced) |
| `.venv/bin/ruff check .` | All checks passed! |
| `make check` | ruff, format, mypy, Django check, migrations, CSS drift, playbook scripts all pass |
| `scripts/tailwindcss -i ui/static/ui/app.src.css -o /tmp/app.css --minify && cmp -s /tmp/app.css ui/static/ui/app.css` | CSS OK |
| `bash scripts/secret-scan.sh --staged` | clean — no secrets in 0 file(s) |
| `.venv/bin/python -m pytest tests/conformance/test_case_template_defaults.py tests/conformance/test_tenant_scoping.py -q` | 131 passed in ~18s |

---

**Report path:** `docs/planning/VERIFY-2026-10-22-case-template-defaults.md`  
**ACs not met:** None