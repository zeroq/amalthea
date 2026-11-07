# PLAN 2026-11-06 — Source detail: Mapping + Raw Data tabs

**Status: IMPLEMENTED — `make check` green, 60 focused tests passing.**  
Decisions in §4 are confirmed by the user. Changes remain uncommitted alongside the previous alert-observables/tag work; nothing is committed unless explicitly requested. See §15 for the implementation result.

## 1. Summary

When an admin/superAdmin opens **Settings → Sources → <source>**, the page currently shows only the mapping configuration. Analysts need to see the actual payload field names so they can write correct JSONPath mapping rules. This plan adds a tab strip to the source detail page:

* **Mapping** (default): current mapping rules, observable rules, and health card — exactly the current page content.
* **Raw Data**: the most recent raw payload received for that source, shown so field names can be inspected.

No new endpoints, models, or migrations are introduced.

## 2. Goals

1. Add a server-rendered tab strip to the source detail page with **Mapping** and **Raw data** tabs.
2. Keep the existing mapping page content under the **Mapping** tab, defaulting to `?tab=mapping`.
3. Under **Raw data**, display the most recent `Alert.raw_payload` for that ingestion source, pretty-printed and auto-escaped, with plain alert identity (no link to the alert).
4. Show an honest empty state when the source has no ingested alerts/raw payload.
5. Preserve the existing admin/superAdmin source-detail gate and CSRF protection.
6. Add focused tests and keep `make check` green.

## 3. Non-goals

* No new ingestion log or “latest received webhook payload” separate from stored `Alert.raw_payload`.
* No editing, searching, or filtering of raw payloads in this wave.
* No JSON schema inference or mapping auto-generation.
* No API changes or public `GET /api/v1/sources/{id}/raw-data` endpoint.
* No HTMX; plain server-rendered tabs with `?tab=` navigation.

## 4. Confirmed decisions

| Area | Decision |
|------|----------|
| Raw data definition | Most recent `Alert` where `ingestion_source = source`, ordered by `-date, -created_at`. Replays do not overwrite `raw_payload`, so this is the latest stored alert row, not necessarily the latest HTTP request. |
| Single vs list | Show the single most recent raw payload. |
| Alert link | **No** link to the originating alert; identity (id, type, source ref, date, organisation) is shown as plain text only. |
| Authorization | Same gate as the existing source detail page: admin/superAdmin only; cross-org superAdmin reads are audited as today. |
| Rendering | Pretty-printed JSON in `<pre>` (via `json.dumps(..., indent=2, sort_keys=True)`), auto-escaped (no `|safe`), because payloads may contain HTML/JS/PII. |
| Tab styling | Reuse existing `.tabs` / `.tab` / `.tab-on` CSS from alert-detail and settings nav; no new CSS. |
| Uncommitted tree | Keep this feature’s changes separate from the uncommitted alert-observables/tag work; do not commit unless asked. |

## 5. Architecture

No new models or migrations. Reuse:

* `Alert.raw_payload` (JSONB) and `Alert.ingestion_source`.
* Existing `settings_source_detail` view in `ui/views/ingestion_sources.py`.
* Existing `ui/templates/ui/source_detail.html`.
* Existing `.tabs` CSS and URL name `ui-settings-source-detail`.

View addition (proposed):

```python
latest_alert = (
    Alert.objects.filter(ingestion_source=source)
    .select_related("owner_org")
    .order_by("-date", "-created_at")
    .first()
)
latest_raw_payload = (
    json.dumps(latest_alert.raw_payload, indent=2, sort_keys=True)
    if latest_alert and latest_alert.raw_payload is not None
    else None
)
```

Template addition (proposed):

* Tab strip immediately under the heading/messages:
  * `Mapping` → `?tab=mapping`
  * `Raw data` → `?tab=raw-data`
* Validate `tab`; invalid values default to `mapping`.
* If `tab == raw-data`, render the raw-data card instead of the mapping cards.
* Raw-data card: alert id/link, source ref, type, date, organisation, and `<pre class="json">{{ latest_raw_payload }}</pre>`.
* Empty state: “No raw data yet” with a hint that raw data comes from ingested alerts for this feed.

## 6. Data model

No changes. `Alert.raw_payload` already stores the ingested JSON.

## 7. API contracts

No new API. Same URL:

```
GET /settings/sources/<uuid:source_id>?tab=mapping      # default, current mapping page
GET /settings/sources/<uuid:source_id>?tab=raw-data     # newest raw payload
```

Query param only; no HTML form changes.

## 8. UI/UX

* Dark theme, server-rendered, keyboard-navigable tabs consistent with existing `.tabs` strip.
* `aria-current="page"` on active tab.
* Raw JSON is text in `<pre>`, not HTML, so auto-escaping prevents stored XSS while remaining readable.
* Alert identity is shown as plain text (no link), per the confirmed decision in §4.

## 9. Phases and tasks

Completed. The Implementation Brief is at `docs/planning/IMPL-2026-11-06-source-detail-raw-data.md`.

1. [x] Update `settings_source_detail` to compute `active_tab`, `latest_alert`, and `latest_raw_payload`.
2. [x] Update `source_detail.html` with the tab strip and conditional Mapping / Raw data panels.
3. [x] Add focused tests in `tests/conformance/test_source_detail_raw_data.py`:
   * default tab renders mapping content,
   * `?tab=raw-data` renders latest raw payload and alert identity,
   * latest-wins ordering across multiple alerts,
   * auto-escaping of payload content,
   * empty-state when no alerts exist,
   * invalid `?tab=` falls back to mapping,
   * analyst 404 gating.
4. [x] Run focused settings tests + `make check`.
5. [x] Update `TODO.md` and mark plan implemented.

## 10. Acceptance criteria

| ID | Criterion |
|----|-----------|
| SRC-RAW-1 | Source detail defaults to the Mapping tab with all current mapping/observable/health content. |
| SRC-RAW-2 | `?tab=raw-data` shows a Raw data tab panel instead of mapping content. |
| SRC-RAW-3 | Raw data panel shows the most recent alert’s `raw_payload`, pretty-printed, for that source. |
| SRC-RAW-4 | Raw data panel includes the alert identity (id, source ref, type, date, organisation). |
| SRC-RAW-5 | Source with no ingested alerts shows an empty state under Raw data. |
| SRC-RAW-6 | Invalid `?tab=` values fall back to Mapping. |
| SRC-RAW-7 | Existing admin/superAdmin gating and CSRF protection are unchanged. |
| SRC-RAW-8 | Focused tests pass and `make check` passes. |

## 11. Security

* No new endpoints or public surface.
* Same source-detail authorization (admin/superAdmin; cross-org superAdmin reads audited).
* Raw payloads may contain PII/secrets/HTML; rendering is auto-escaped plain text in `<pre>`, no `|safe`, no inline JS construction.
* No new logging of payload values beyond existing alert/raw_payload handling.

## 12. Testing

* Focused tests in `tests/conformance/test_source_detail_raw_data.py` (or extend `test_settings_source_mapping.py`).
* Existing `tests/conformance/test_settings_source_mapping.py` must remain green (53 tests currently).
* `make check` must pass (ruff, format, mypy, Django check, migrations, CSS).

## 13. Risks

* Replays do not update `raw_payload`; if “most recent raw data” must mean the latest HTTP webhook payload even when deduped to an existing alert, this plan would need a new ingestion log table — out of scope for this wave.
* `Alert.date` may not equal ingest time for all feeds; ordering by `-date, -created_at` is a reasonable default but should be confirmed.
* Raw JSON auto-escaping turns `"` into `&quot;` inside text nodes; this is still readable in `<pre>` but may differ from a raw `<code>` block.

## 14. Open questions — resolved

1. Raw data is the latest stored `Alert.raw_payload` for the source (no separate ingestion log). **Confirmed.**
2. Show a single latest payload (no list). **Confirmed.**
3. Auto-escaped pretty JSON in `<pre>` behind the existing admin/superAdmin gate. **Confirmed.**
4. No link to the originating alert; identity is plain text. **Confirmed.**
5. Reuse the existing tabs CSS under the heading. **Confirmed.**

## 15. Implementation result

Changes (uncommitted):

* `ui/views/ingestion_sources.py` — `settings_source_detail` reads `?tab=`, validates against `("mapping", "raw-data")` (invalid → `mapping`), and on the raw-data tab computes `latest_alert` (newest `Alert` for the source by `-date, -created_at`) and `latest_raw_payload` (`json.dumps(..., indent=2, sort_keys=True)`, `None` when empty). `_source_detail_context` accepts and forwards `active_tab`, `latest_alert`, `latest_raw_payload`.
* `ui/templates/ui/source_detail.html` — tab strip under the messages block reusing `.tabs`/`.tab`/`.tab-on` + `aria-current`; existing mapping content wrapped in `{% if active_tab == "mapping" %}`; `{% elif active_tab == "raw-data" %}` card renders identity `<dl class="meta">` (Alert id, Type, Source ref, Date, Organisation) and `<pre class="json">{{ latest_raw_payload }}</pre>`, with a "No raw data yet." empty state (no link, per §4).
* `tests/conformance/test_source_detail_raw_data.py` — 7 tests covering SRC-RAW-1..7.

Verification:

* `tests/conformance/test_source_detail_raw_data.py` + `tests/conformance/test_settings_source_mapping.py`: **60 passed** (7 new, 53 existing).
* `make check`: **passed** (Django check, migrations in sync, CSS drift, playbook scripts).
* Note: payload quotes render as `&quot;` in the auto-escaped `<pre>`; tests assert on the escaped form.
* **Independent verifier: PASS WITH NOTES — 8/8 ACs met, 0 not met, 0 deviated.**
  Report: `docs/verification/VERIFY-2026-11-06-source-detail-raw-data.md`.
  Observations (non-blocking): (a) `raw_payload == {}` renders `{}` in `<pre>` rather than the empty state — untested edge, plan does not specify; (b) the `apply_pending` modal lives in the mapping branch and is unreachable on the raw-data tab (POSTs always re-render mapping) — harmless.

All acceptance criteria SRC-RAW-1..8 are met.
