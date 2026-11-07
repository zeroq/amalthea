# Implementation Brief: Source detail Raw data tab

**Plan:** `docs/planning/PLAN-2026-11-06-source-detail-raw-data.md`  
**Decisions confirmed by user:**
1. Raw data = latest stored `Alert.raw_payload` for the ingestion source.
2. Single latest raw payload (not a list).
3. Auto-escaped pretty JSON in `<pre>`; existing admin/superAdmin source-detail gate.
4. No link to the originating alert (per user answer) — I will still show alert identity (id/source ref/type/date/org) without a link. Actually showing id without link is fine; I'll show identity fields as plain text.
5. Reuse existing `.tabs` / `.tab` / `.tab-on` strip under the heading.

**Status:** ready to implement. Nothing committed; tree still has uncommitted alert-observables/tag work — this feature adds separate uncommitted changes.

## Files to change

1. `ui/views/ingestion_sources.py` — `settings_source_detail` + `_source_detail_context`.
2. `ui/templates/ui/source_detail.html` — tab strip + conditional Mapping / Raw data panels.
3. `tests/conformance/test_source_detail_raw_data.py` — new focused tests.
4. `TODO.md` — add section for this feature / mark plan verified when green.

## View changes (`ui/views/ingestion_sources.py`)

`json` and `Alert` are already imported in this module (used at lines ~225 and ~231). No new imports needed.

### `settings_source_detail`
- Read `tab = request.GET.get("tab", "mapping")`.
- Validate: `active_tab = tab if tab in ("mapping", "raw-data") else "mapping"`.
- Compute raw data **only when** `active_tab == "raw-data"`:
  ```python
  latest_alert = (
      Alert.objects.filter(ingestion_source=source).order_by("-date", "-created_at").first()
  )
  latest_raw_payload = (
      json.dumps(latest_alert.raw_payload, indent=2, sort_keys=True) if latest_alert else None
  )
  ```
- Pass `active_tab`, `latest_alert`, `latest_raw_payload` into `_source_detail_context(...)`.

### `_source_detail_context`
Add keyword params `active_tab="mapping"`, `latest_alert=None`, `latest_raw_payload=None`.
Return dict entries:
- `"active_tab": active_tab`
- `"latest_alert": latest_alert`
- `"latest_raw_payload": latest_raw_payload`

Do **not** compute raw payload on every GET — only when the raw-data tab is requested, to avoid unnecessary JSON serialization.

## Template changes (`ui/templates/ui/source_detail.html`)

Keep the existing content; restructure `{% block content %}`:

1. Heading block (unchanged).
2. Tab strip (new, immediately after heading / before cross-org hint):
   ```html
   <nav class="tabs" aria-label="Source sections">
     {% url 'ui-settings-source-detail' source.id as source_url %}
     <a class="tab{% if active_tab == 'mapping' %} tab-on{% endif %}"
        href="?tab=mapping" aria-controls="panel-mapping"
       {% if active_tab == 'mapping' %}aria-current="page"{% endif %}>
       Mapping
     </a>
     <a class="tab{% if active_tab == 'raw-data' %} tab-on{% endif %}"
        href="?tab=raw-data" aria-controls="panel-raw-data"
       {% if active_tab == 'raw-data' %}aria-current="page"{% endif %}>
       Raw data
     </a>
   </nav>
   ```
3. Wrap existing mapping content in `{% if active_tab == "mapping" %} ... {% endif %}`.
4. New raw-data panel `{% elif active_tab == "raw-data" %}`:
   - If `latest_alert`: card "Raw data" with identity as plain text (alert id, type, source ref, date, organisation) and `<pre class="json">{{ latest_raw_payload }}</pre>`.
   - Else: empty-state card "No raw data yet" + hint that raw data comes from ingested alerts for this feed.
5. Keep the `apply_pending` `<dialog>` at the end as-is (only relevant on mapping tab renders; harmless elsewhere).

CSS: reuse `.tabs`, `.tab`, `.tab-on` (already compiled; `make css-check` must stay green — no new classes).

## Tests (`tests/conformance/test_source_detail_raw_data.py`)

Use existing fixtures/patterns from `test_settings_source_mapping.py` (`_mk_org`, `_mk_user`, `_mk_source`, `_ui_client`, `Organisation`, `User`, `IngestionSource`, `Alert`, `AlertStatus`, `reverse`, `Client`).

Tests:
1. `test_default_tab_shows_mapping_content` — GET source detail, 200, contains "Mapping rules" / title, no raw-data panel.
2. `test_raw_data_tab_shows_latest_payload` — create source + two alerts with distinct `raw_payload`, GET `?tab=raw-data`, 200, contains latest payload value, contains earlier? No — only latest value. Contains alert id and source_ref as plain text. Contains `<pre`.
3. `test_raw_data_tab_picks_most_recent_alert` — two alerts same source different dates; `?tab=raw-data` shows the later one's payload value and not the earlier one's distinctive value.
4. `test_raw_data_empty_state_when_no_alerts` — source with no alerts, `?tab=raw-data`, contains "No raw data yet".
5. `test_invalid_tab_falls_back_to_mapping` — `?tab=bogus`, shows mapping content, not raw-data panel.
6. `test_mapping_tab_unaffected_by_raw_data_param` — `?tab=mapping` shows mapping cards (Tokens, Advanced).
7. `test_non_admin_cannot_reach_source_detail` — analyst gets 404 (existing gate preserved).

Assert `raw_payload` is auto-escaped by default — no `|safe` used in template, so just confirm JSON characters appear escaped/readable and no XSS via a payload containing `<script>` (assert `&lt;script&gt;` present, `<script>` not present). Include in test 3 or a dedicated test.

## Acceptance criteria (per task)

| Task | AC |
|------|----|
| View: tab param validation + default | SRC-RAW-6 (invalid `?tab=` falls back to Mapping); default is Mapping. |
| View: raw payload query | SRC-RAW-3 / SRC-RAW-4 — latest stored `raw_payload`, pretty JSON, alert identity present. |
| Template: tab strip | SRC-RAW-1 / SRC-RAW-2 — Mapping default, `?tab=raw-data` shows raw panel. |
| Template: empty state | SRC-RAW-5 — no alerts → "No raw data yet". |
| Template: auto-escape | no `|safe`; `<script>` in payload renders escaped. |
| Gating unchanged | SRC-RAW-7 — non-admin 404; existing settings source tests still pass. |
| Checks | SRC-RAW-8 — focused tests + `make check` green. |

## Constraints / notes

- No new models, migrations, endpoints, or HTMX.
- `raw_payload` is `JSONField(blank=True, default=dict)` — never NULL, but may be `{}`.
- Alert model has no default ordering; always explicit `.order_by("-date", "-created_at")`.
- Cross-org superAdmin read auditing already happens in `settings_source_detail` via `maybe_record_cross_org_read` — preserve it.
- `apply_pending` confirm dialog only renders on `intent=apply`; keep it outside the tab condition or guard to mapping tab — simplest: leave it at end of block (it only exists when `apply_pending` is set, which is a mapping-tab render anyway).
- Do not link to alert detail (user answered "no"); show identity fields as plain text.
- Deviations from this brief must be recorded in `docs/planning/PLAN-2026-11-06-source-detail-raw-data.md`.

## Verification command

```
.venv/bin/pytest tests/conformance/test_source_detail_raw_data.py tests/conformance/test_settings_source_mapping.py -q
make check
```

## Handoff

Ready to implement. Proceeding with the code changes below, then running the verification command.
