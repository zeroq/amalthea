# Security Review: raw-data click-to-map (TODO §17)

**Date:** 2026-11-07
**Verdict:** PASS WITH NOTES
**Scope:** uncommitted "raw-data click-to-map" feature (backend helpers + prefill view + template tag + modal + external JS)
**Related:** `docs/verification/VERIFY-2026-11-07-raw-click-map.md` (verifier PASS WITH NOTES, 10/10 ACs)

## Summary
Clicking a JSON scalar on the source detail **Raw data** tab opens a modal and performs a **GET**
prefill (`?tab=mapping&map_target=…&map_path=…[&map_observable_type=…]`). The GET performs no writes;
the existing CSRF-protected **Save mapping / Save observable rules** POST remains the only writer.
No Critical or High findings.

## Findings

### Medium (accepted)
- **M1 — GET form without a CSRF token.** `ui/templates/ui/_json_map_modal.html` deliberately omits
  `{% csrf_token %}` because a token on a GET form would leak into the URL/history. The view only builds
  in-memory editor rows via `_prefill_row()` (`ui/views/ingestion_sources.py`); there is no
  `source.save()`, audit row, or Celery task. The writer remains the CSRF-protected Save POST.
  **Accepted.**
- **M2 — JSONPath key names in URLs/history/logs.** `map_path=$.iocs.ip` exposes payload *key names*
  (never values) in browser history and server logs. Residual risk is low: the full payload is already
  readable on the same admin-gated page. **Accepted;** revisit only if payload keys are ever considered
  sensitive on their own.

### Low
- **L1 — Reflected `map_path`.** Input is gated by `is_valid_jsonpath()` (`$` prefix, ≤ `MAX_PATH_LEN`
  = 200, `jsonpath_ng` parse) and rendered with Django autoescaping. `test_get_mapping_tab_reflected_script_escaped`
  covers the `<script>` case. No issue.
- **L2 — `data-json-path` attribute injection.** Keys are escaped in `jsonpath_for()` (backslash/quote)
  and Django escapes attribute values; the JS reads via `getAttribute()` and writes with `textContent`.
  No issue.

### Info
- CSP-compliant: only the external `ui/static/ui/json-map.js`; no inline `<script>`, `eval`,
  `new Function`, or data-driven `style=`.
- Input/DoS bounds: path length 200, `jsonpath_ng` parse guard, tree caps 1000 nodes / depth 12 /
  200-char scalars; no unbounded recursion or quadratic behaviour observed.
- Authorization unchanged: admin/superAdmin gate; analysts receive 404 (`test_get_mapping_tab_analyst_404`).
- No hardcoded secrets in the new or modified files.

## Verdict table
| Aspect | Status |
|---|---|
| XSS / autoescaping | Safe |
| CSP | Compliant |
| CSRF / state change | Accepted risk (read-only GET; CSRF-protected POST is the only writer) |
| Input validation / DoS | Bounded and validated |
| Authorization / IDOR | No bypass |
| Sensitive data leakage | Low residual (key names only) |
| Open redirect / URL injection | Safe (`action="{{ request.path }}"`, JS builds no URLs) |
| Secrets | None |

No code changes required.
