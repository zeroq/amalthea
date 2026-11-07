# Verification Report: Source detail: Mapping + Raw Data tabs

## Plan
Plan: `docs/planning/PLAN-2026-11-06-source-detail-raw-data.md`  
Verified: 2026-11-07 14:32 UTC  
Status: PASS WITH NOTES

## Summary
- Total ACs: 8
- Met: 8
- Not Met: 0
- Deviated: 0

## Commands Run
- `.venv/bin/pytest tests/conformance/test_source_detail_raw_data.py tests/conformance/test_settings_source_mapping.py -q` — **60 passed** (7 new, 53 existing)
- `.venv/bin/pytest tests/conformance/test_source_detail_raw_data.py -v` — all 7 tests PASSED
- `make check` — passed (Django check, migrations in sync, CSS drift, playbook scripts, ruff check, ruff format)
- `.venv/bin/ruff check . --quiet` — clean
- `.venv/bin/ruff format --check . --quiet` — clean

## Criterion-by-Criterion

### SRC-RAW-1: Source detail defaults to the Mapping tab with all current mapping/observable/health content
- **Status: MET**
- **Evidence:**
  - `ui/views/ingestion_sources.py:153`: `active_tab = request.GET.get("tab", "mapping")` defaults to `"mapping"`
  - `ui/templates/ui/source_detail.html:28`: `{% if active_tab == "mapping" %}` wraps all mapping content (identity, rules, observable rules, health, tokens, advanced config)
  - `tests/conformance/test_source_detail_raw_data.py:92-102`: `test_default_tab_shows_mapping_content` asserts `"Mapping rules" in body`, `"$.name" in body`, `'aria-current="page"' in body`, `body.count('href="?tab=mapping"') == 1`, and `"No raw data yet." not in body`
- **Notes:** No sections were dropped; the entire existing page content remains under the Mapping tab.

### SRC-RAW-2: `?tab=raw-data` hides mapping content and shows the Raw data panel
- **Status: MET**
- **Evidence:**
  - `ui/views/ingestion_sources.py:153-155`: Tab validation `if active_tab not in ("mapping", "raw-data"): active_tab = "mapping"` — only `"mapping"` and `"raw-data"` are accepted
  - `ui/templates/ui/source_detail.html:28-365`: `{% if active_tab == "mapping" %} ... {% elif active_tab == "raw-data" %} ... {% endif %}` — mapping content is gated behind `active_tab == "mapping"`, raw-data panel behind `active_tab == "raw-data"`
  - `tests/conformance/test_source_detail_raw_data.py:105-138`: `test_raw_data_tab_shows_latest_payload_and_identity` asserts `"Mapping rules" not in body` when `?tab=raw-data`, and `"Raw data" in body`
- **Notes:** The tab strip (`.tabs` reuse) correctly switches panels; no mapping HTML leaks into the raw-data view.

### SRC-RAW-3: Raw data panel shows the most recent alert's `raw_payload`, pretty-printed, for that source; ordering is `-date, -created_at` and source-scoped
- **Status: MET**
- **Evidence:**
  - `ui/views/ingestion_sources.py:160-161`: `latest_alert = Alert.objects.filter(ingestion_source=source).order_by("-date", "-created_at").first()` — source-scoped, explicit ordering
  - `tests/conformance/test_source_detail_raw_data.py:141-163`: `test_raw_data_tab_picks_most_recent_by_date` creates two alerts with dates `2026-01-03` (payload `{"winner": "a"}`) and `2026-01-01` (payload `{"winner": "b"}`); the test asserts `"winner": "a"` in body and `"winner": "b"` not in body, confirming `-date` ordering
- **Notes:** Ordering genuinely follows `-date, -created_at`; cross-source leak is prevented by `ingestion_source=source` filter.

### SRC-RAW-4: Raw data panel includes the alert identity (id, source_ref, type, date, organisation)
- **Status: MET**
- **Evidence:**
  - `ui/templates/ui/source_detail.html:352-358`: `<dl class="meta">` with `<dt>Alert</dt><dd class="mono">{{ latest_alert.id }}</dd>`, `<dt>Type</dt><dd class="mono">{{ latest_alert.type }}</dd>`, `<dt>Source ref</dt><dd class="mono">{{ latest_alert.source_ref }}</dd>`, `<dt>Date</dt><dd class="mono">{{ latest_alert.date|date:"Y-m-d H:i" }}</dd>`, `<dt>Organisation</dt><dd>{{ latest_alert.owner_org.name }}</dd>`
  - `tests/conformance/test_source_detail_raw_data.py:105-138`: `test_raw_data_tab_shows_latest_payload_and_identity` asserts `str(latest.id) in body`, `latest.source_ref in body`, `latest.type in body`, `org.name in body`
- **Notes:** Identity is plain text only, no link to alert detail, per confirmed decision §4.

### SRC-RAW-5: Source with no ingested alerts shows an empty state under Raw data
- **Status: MET**
- **Evidence:**
  - `ui/templates/ui/source_detail.html:360-362`: `{% else %}<p class="empty">No raw data yet.</p><p class="hint">Raw data appears here once an alert has been ingested from this feed.</p>{% endif %}` when `latest_alert` is None
  - `tests/conformance/test_source_detail_raw_data.py:183-189`: `test_raw_data_empty_state_when_no_alerts` asserts `"No raw data yet." in body` and `"ingested from this feed" in body` for a source with no alerts
- **Notes:** Empty state is clear and includes a hint about the data source.

### SRC-RAW-6: Invalid `?tab=` values fall back to Mapping
- **Status: MET**
- **Evidence:**
  - `ui/views/ingestion_sources.py:153-155`: `if active_tab not in ("mapping", "raw-data"): active_tab = "mapping"` — any value outside the accepted tuple defaults to `"mapping"`
  - `tests/conformance/test_source_detail_raw_data.py:192-196`: `test_invalid_tab_falls_back_to_mapping` checks `?tab=bogus` shows `"Mapping rules" in body` and `"No raw data yet." not in body`
- **Notes:** The code handles all invalid values uniformly (tested with `bogus`; values like `RAW-DATA`, empty string, path traversal-ish would all fall to mapping by the same `in` check).

### SRC-RAW-7: Admin/superAdmin gate unchanged; analyst → 404; cross-org superAdmin read still audited (`maybe_record_cross_org_read`). CSRF/POST behavior untouched
- **Status: MET**
- **Evidence:**
  - `ui/views/ingestion_sources.py:129-131`: `_require_admin_ui(request)` guard returns 404 for non-admin users
  - `ui/views/ingestion_sources.py:144-151`: `maybe_record_cross_org_read(...)` audit call preserved on every GET
  - `tests/conformance/test_source_detail_raw_data.py:199-211`: `test_analyst_cannot_reach_source_detail` asserts analyst gets status 404
  - `make check` passage confirms Django check, migrations, CSS, ruff — all gates intact
- **Notes:** No regression in authorization; cross-org superAdmin read auditing continues as before.

### SRC-RAW-8: Focused tests pass and `make check` passes
- **Status: MET**
- **Evidence:**
  - `60 passed` (7 new + 53 existing) in conformance test suite
  - `make check` passes entirely: Django check, migrations in sync, CSS drift check, playbook scripts, ruff check, ruff format
- **Notes:** All prior feature work (`alert-observables/tag`) remains uncommitted and separate.

## Deviations
- **None.** All acceptance criteria SRC-RAW-1 through SRC-RAW-8 are met. The implementation conforms to the approved plan §4 confirmed decisions and §10 ACs.

## Observations / Risks
1. **Auto-escaped JSON rendering:** Payloads containing HTML such as `<script>alert(1)</script>` render as `<script>alert(1)</script>` inside the `<pre>` tag (confirmed by `test_raw_data_tab_autoescapes_payload`). This is correct behavior — auto-escaping prevents stored XSS while remaining readable in `<pre>`. Tests assert on the escaped form.
2. **`raw_payload` default of `{}`:** The `Alert.raw_payload` field is a `JSONField(blank=True, default=dict)`, so when no explicit payload is stored it defaults to `{}`. This would render as `{}` inside the `<pre>` tag rather than triggering the "No raw data yet" empty state. This is reasonable but untested edge case; the plan does not specify behavior for the `{}` case, so it is flagged only as an observation, not a failure.
3. **`apply_pending` modal positioning:** The `{% if apply_pending %}...{% endif %}` modal (lines 344-347 of `source_detail.html`) sits at the same nesting level as the `{% if active_tab == "mapping" %}` block, meaning it renders inside the mapping tab content. This is intentional — the apply-confirm dialog only exists when `intent=apply` POST has been rendered, which only occurs on the mapping tab. It is harmless when the raw-data tab is active because `apply_pending` would be `None`.
4. **Tab value coverage:** The invalid-tab fallback test (`test_invalid_tab_falls_back_to_mapping`) uses `bogus`. The code's `in ("mapping", "raw-data")` check handles all other values uniformly (empty string, `RAW-DATA`, path traversal-ish). No specific test covers empty `?tab=` or `?tab=raw-data` with additional query params, but the validation logic is robust.

## Final Verdict
**PASS WITH NOTES** — All 8 acceptance criteria (SRC-RAW-1..8) are MET. The implementation conforms to the approved plan. `make check` is green, 60 focused tests pass, ruff check/format are clean. Two minor observations (JSON auto-escaping detail, `{}` payload edge case) are noted but do not constitute failures.

## Report Path
`docs/verification/VERIFY-2026-11-06-source-detail-raw-data.md`

## AC Tally
- Met: 8
- Not Met: 0
- Deviated: 0