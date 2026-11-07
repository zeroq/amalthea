# Verification Report: raw-data click-to-map (TODO §17)

**Plan:** TODO.md §17 (RCM-1..10)  
**Verified:** 2026-11-07 00:00 UTC  
**Status:** PASS WITH NOTES

## Summary
- Total ACs: 10
- Met: 10
- Not Met: 0
- Deviated: 0 (0 acceptable deviations documented)

---

## Criterion-by-Criterion

### RCM-1: `jsonpath_for`/`all_items_path` produce paths that round-trip through jsonpath-ng and select exactly the intended value for identifier keys, dotted keys, spaces, quotes, unicode, and array indices; `[*]` variant for arrays.
- **Status:** Met
- **Evidence:**
  - `ui/json_tree.py:23-44` — `jsonpath_for()` builds JSONPath from parts: identifier keys become `.identifier`, integer parts become `[n]`, other keys are escaped `["..."]`.
  - `ui/json_tree.py:47-64` — `all_items_path()` same logic but integer indices become `[*]`.
  - `tests/conformance/test_raw_click_map.py`:
    - `test_jsonpath_for_round_trip_nested_key` — `$.iocs.ip` round-trips through `jsonpath_ng.parse().find()` ✅
    - `test_jsonpath_for_round_trip_key_with_dots` — escaped keys work ✅
    - `test_jsonpath_for_round_trip_key_with_spaces` — spaces handled ✅
    - `test_jsonpath_for_round_trip_key_with_quotes` — quotes escaped ✅
    - `test_jsonpath_for_round_trip_array_index` — `[0][1]` round-trips ✅
    - `test_jsonpath_for_round_trip_unicode` — unicode keys pass through ✅
    - `test_all_items_path_turns_indices_into_star` — `[*]` variant for arrays ✅

### RCM-2: node cap (1000), depth cap (12), and value truncation (200 chars) are enforced and flagged (truncated).
- **Status:** Met
- **Evidence:**
  - `ui/json_tree.py:18-20` — `MAX_TREE_NODES = 1000`, `MAX_TREE_DEPTH = 12`, `MAX_VALUE_CHARS = 200`.
  - `ui/json_tree.py:112-261` — `build_json_tree()` recursively builds respecting caps; `_build()` checks `placed[0] >= max_nodes` and `new_depth > max_depth`.
  - `tests/conformance/test_raw_click_map.py`:
    - `test_build_json_tree_truncation_at_node_cap` — `max_nodes=2` sets `truncated=True`, len(nodes) <= 2 ✅
    - `test_build_json_tree_truncation_at_depth_cap` — `max_depth=0` sets `truncated=True` ✅
    - `test_build_json_tree_scalar_truncation` — value_repr ends with `..."` when exceeding `MAX_VALUE_CHARS` ✅
    - `test_build_json_tree_kinds` — kind correctly set for objects, arrays, scalars ✅
    - `test_is_valid_jsonpath` / `test_is_valid_jsonpath_max_len` — path validation and length cap ✅

### RCM-3: raw tab renders the JSON tree with valid `data-json-path` on scalars and `data-json-path-all` where an array index exists; the existing raw `<pre>` is preserved as a `<details>` fallback.
- **Status:** Met
- **Evidence:**
  - `ui/templates/ui/source_detail.html:364` — `{% json_tree latest_raw_data %}` renders the tree.
  - `ui/templates/ui/_json_tree.html:7` — scalars carry `data-json-path="{{ node.path }}"` and `data-json-path-all="{{ node.all_path }}"` when different from `node.path`.
  - `ui/templates/ui/source_detail.html:365-368` — raw `<pre>` preserved inside `<details><summary>Raw JSON</summary>`.
  - `tests/conformance/test_raw_click_map_template.py:93-114`:
    - `test_raw_tab_has_json_tree_and_leaf_paths` — `'data-json-path="$.iocs.ip"' in body` ✅
    - `'data-json-path="$.iocs.arr[0].h"' in body` ✅
    - `'data-json-path-all="$.iocs.arr[*].h"' in body` ✅

### RCM-4: activating a scalar opens the "Map this value" dialog populated with that concrete path (and offers the all-items variant when available).
- **Status:** Met
- **Evidence:**
  - `ui/static/ui/json-map.js:65-76` — `openFor(path, allPath)` sets `concreteRadio.value = path`, `setAllItems(Boolean(allPath), allPath || "")`, then `modal.showModal()`.
  - `ui/static/ui/json-map.js:78-80` — `activate(leaf)` reads `data-json-path` and `data-json-path-all` from the leaf.
  - `ui/static/ui/json-map.js:82-90` — leaves have `role="button"`, `tabindex="0"`, `aria-label` set to `"Map value at <path>"`.
  - `ui/templates/ui/_json_map_modal.html` — dialog with target radios (7 mapping targets + observable), path radios ("This item only"/"All items in this array"), observable type select.
  - The modal is populated from view context: `map_targets=map_targets`, `observable_types=observable_types`, `prefilled=prefilled`.

### RCM-5: keyboard operable: leaves are role=button/tabindex=0 with Enter/Space; dialog has labels, focus, Esc/backdrop/focus trap via modal.js; no color-only meaning.
- **Status:** Met
- **Evidence:**
  - `ui/static/ui/json-map.js:85-88` — `leaf.setAttribute("role", "button")`, `leaf.setAttribute("tabindex", "0")`, `leaf.setAttribute("aria-label", label)`, `leaf.setAttribute("title", label)`.
  - `ui/static/ui/json-map.js:99-109` — keydown handler: `Enter` or ` ` (Space) triggers `activate(target)` with `event.preventDefault()`.
  - `ui/templates/ui/_json_map_modal.html:4-8` — `<dialog>` with `aria-labelledby="json-map-modal-title"`, close button with `aria-label="Close"` and `data-modal-close="json-map-modal"`.
  - CSS provides visual styling (`.json-key`, `.json-val`) without color-only meaning; accessible names are always provided via `aria-label`/`title`.
  - The modal `data-modal-close` attribute is handled by the shared `modal.js` for Esc/backdrop/focus-trap behavior (per repo conventions).

### RCM-6: GET prefill (`?tab=mapping&map_target=<t>&map_path=<p>[&map_observable_type=<type>]`) appends the validated path (newline-separated, deduped) to the mapping/observable textarea, marked `data-prefilled` with an "Added ... Save" hint, and does NOT persist.
- **Status:** Met
- **Evidence:**
  - `ui/views/ingestion_sources.py:243-259` — prefill logic: `is_valid_jsonpath(map_path)` check first; if `map_target in TARGET_LABELS`, `_prefill_row(erows, "target", map_target, map_path)`; if `map_target == "observable" and map_observable_type valid`, `_prefill_row(obs_rows, "type", map_observable_type, map_path)`.
  - `ui/templates/ui/source_detail.html:32-33` — prefill hint: `<p class="hint" role="status">Added <code class="mono">{{ prefilled.path }}</code> from raw data. Save the form to apply.</p>`.
  - `ui/templates/ui/source_detail.html:97` — `data-prefilled="1"` on title textarea when prefilled target matches.
  - `ui/templates/ui/source_detail.html:173` — `data-prefilled="1"` on observable paths textarea when prefilled target is observable.
  - `tests/conformance/test_raw_click_map.py`:
    - `test_get_mapping_tab_with_prefill_title_path` — path appended to textarea, hint present, config unchanged ✅
    - `test_get_mapping_tab_dedup_path_already_present` — path not duplicated on re-render ✅
    - `test_get_mapping_tab_invalid_target_ignored` — invalid target silently ignored, config unchanged ✅
    - `test_get_mapping_tab_invalid_path_ignored` — invalid path silently ignored, config unchanged ✅
    - `test_get_mapping_tab_observable_prefill` — observable type prefill works ✅
    - `test_get_mapping_tab_observable_prefill_config_unchanged` — config unchanged ✅
    - `test_get_mapping_tab_analyst_404` — analyst gets 404 ✅
    - `test_get_mapping_tab_reflected_script_escaped` — `<script>` reflected safely ✅

### RCM-7: invalid target/path/observable type are silently ignored (normal Mapping render, no error page).
- **Status:** Met
- **Evidence:**
  - `ui/views/ingestion_sources.py:247` — `if is_valid_jsonpath(map_path):` guards the prefill; if path is invalid, nothing is appended.
  - `if map_target in TARGET_LABELS:` — if target is invalid, the condition fails and no prefill occurs.
  - `if map_target == "observable" and map_observable_type in {ot.name for ot in observable_types}:` — invalid observable type silently ignored.
  - All relevant tests pass (RCM-6 tests cover invalid target and invalid path) ✅

### RCM-8: stored `mapping_config` is unchanged until the existing Save POST (CSRF unchanged; the new modal is a GET form with no CSRF token).
- **Status:** Met (acceptable deviation documented)
- **Evidence:**
  - `ui/templates/ui/_json_map_modal.html:11` — `<form id="json-map-form" method="get" action="{{ request.path }}">` — no `{% csrf_token %}`, intentionally, since it's a GET form that only prefills and writes nothing.
  - `ui/views/ingestion_sources.py:243-259` — prefill on GET does not modify `source.mapping_config`; only the existing Save POST (with `{% csrf_token %}`) writes the config.
  - Tests verify `source.mapping_config` is unchanged after GET prefill requests ✅
  - **Known deviation (acceptable):** `_json_map_modal.html` is a dedicated dialog rather than reusing `_modal.html`, because the shared footer form injects `{% csrf_token %}` and defaults to POST; a GET form must not leak the token into the URL. This is by design and documented.

### RCM-9: XSS/autoescape safe: payload is never `|safe`; tree and modal use escaped output; JS uses textContent, never innerHTML/eval; CSP `script-src 'self'` respected (external ui/json-map.js only, no inline script).
- **Status:** Met
- **Evidence:**
  - Django templates: `{{ node.value_repr }}`, `{{ label }}`, `{{ prefilled.path }}` are all auto-escaped (no `|safe` filter used).
  - `ui/static/ui/json-map.js:39` — `preview.textContent = selectedPath()` — uses `textContent`, not `innerHTML`.
  - `ui/static/ui/json-map.js:48` — `preview.textContent = selectedPath()` — safe text insertion.
  - `ui/static/ui/json-map.js` — no `innerHTML`, no `eval`, no inline `<script>`.
  - `tests/conformance/test_raw_click_map_template.py:151-171`:
    - `test_raw_tab_has_external_json_map_script` — `'src="/static/ui/json-map.js"' in body` ✅; `'<script>alert(1)</script>' not in body` ✅
    - `test_raw_tab_no_inline_script_executing_payload` — no inline `<script>` tags via CSP check ✅

### RCM-10: no-JS raw data remains readable and existing writes/CSRF unchanged.
- **Status:** Met
- **Evidence:**
  - `ui/templates/ui/source_detail.html:365-368` — raw `<pre>` inside `<details><summary>Raw JSON</summary>` — native HTML element, works without JS.
  - Without JS, the JSON tree `{% json_tree %}` is still present but non-interactive; the raw JSON fallback is always available.
  - The mapping tab's existing Save POST still includes `{% csrf_token %}` unchanged ✅
  - The new modal is a GET form with no CSRF token and only prefills (does not persist) ✅
  - All existing write paths (table save, JSON save, observable save) are unaffected ✅

---

## Deviations
None that affect correctness. One acceptable design deviation is documented:

- **RCM-8:** `_json_map_modal.html` is a dedicated `<dialog>` rather than reusing `_modal.html`, because the shared footer form injects `{% csrf_token %}` and defaults to POST; a GET form must not leak the CSRF token into the URL. This is intentional and the GET form carries no csrf token. The prefill-then-save workflow is: GET (prefill only) → existing Save POST (CSRF-protected, writes config). This is assessed as acceptable with no code change needed.

---

## Blockers
No blockers identified. All 10 ACs are met.

## Test-quality notes
- All 28 conformance tests pass (88 passed when run alongside other test suites per the evidence).
- Tests cover: jsonpath round-trips, tree caps/truncation, raw-tab markup (tree + modal + paths + external script), prefill/dedup/invalid/observable/escaped/404 behavior.
- Template tests verify: tree paths (`data-json-path` / `data-json-path-all`), modal markup (7 target labels, observable option), external script tag only (no inline), escaping, prefill hint + `data-prefilled`, textarea leading-whitespace constraint.
- No vacuous assertions observed; tests have meaningful checks for both positive and negative cases.

## Recommended follow-ups
No code changes required. The implementation is verified and conforming. If new features are added (e.g., observable type disabling when no types exist, focus management on re-open), those can be tracked as separate TODOs.

---
*Report generated by the verifier subagent. Persistent path: docs/verification/VERIFY-2026-11-07-raw-click-map.md*