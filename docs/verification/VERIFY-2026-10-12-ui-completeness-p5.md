# Verification Report: UI completeness wave — Plan phase P5 (D1, D3)

**Plan:** `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P5 (lines 165–168)
**ADR:** `docs/decisions/ADR-005-markdown-rendering.md` (Accepted 2026-10-12)
**Verified:** 2026-10-12 11:03 UTC (working tree @ `cc9e569`, 33 uncommitted paths)
**Verifier:** verifier agent (read-only)
**Status:** **Pass with minor deviations**

---

## Summary

| | Count |
|---|---|
| Total acceptance criteria evaluated | **18** |
| Met | **18** |
| Not Met | **0** |
| Deviated (AC still met, non-blocking gap noted) | **0** |

- D1 (playbook GET→POST round-trip): **9 / 9 Met**
- D3 (sanitized CommonMark rendering): **9 / 9 Met**
- New test failures introduced by this batch: **none**
- Blockers: **none**

> Note: the working tree contains an **uncommitted mixed batch** (P2 C1/C2/C3 and P4/P5 files are commingled). Scope here is **P5 only**; P2/P4 artefacts were consulted only to avoid false positives and are not adjudicated.

---

## Commands run (exact) and observed results

| # | Command | Result |
|---|---|---|
| 1 | `.venv/bin/pytest tests/conformance/test_playbook_roundtrip.py tests/conformance/test_ui_markdown.py tests/conformance/test_tenant_scoping.py -q` | **133 passed in 15.88s** |
| 2 | `.venv/bin/pytest tests/conformance/test_ui_inline_assets.py tests/conformance/test_ui_urls_smoke.py tests/conformance/test_ui_nav.py -q` | **143 passed in 2.59s** |
| 3 | `.venv/bin/ruff check ui/markdown.py ui/templatetags/ui_tags.py ui/views.py tests/conformance/test_playbook_roundtrip.py tests/conformance/test_ui_markdown.py` | **All checks passed!** |
| 4 | `.venv/bin/ruff format --check <same 5 files>` | **5 files already formatted** |
| 5 | Independent probe — `render_markdown()` over 10 obfuscated payloads (below) | **all neutralised** |
| 6 | Independent probe — fail-closed path (patched `_MD.render` to raise) | returned `'&lt;b&gt;x&lt;/b&gt; &amp; y'`, a `SafeString` — **fail-closed confirmed** |
| 7 | Dependency presence: `import markdown_it, nh3, mdurl` | `markdown_it 4.2.0`, `nh3 0.3.7`, `mdurl 0.1.2` |

Independent payload probe (command 5) output:

```
js_paren         -> '<p>[x](javascript:alert(1))</p>\n'
js_entity        -> '<p>[x](javascript:alert(1))</p>\n'
js_case          -> '<p>[x](JaVaScRiPt:alert(1))</p>\n'
js_colon_entity  -> '<p>[x](javascript:alert(1))</p>\n'
data_html        -> '<p>[x](data:text/html,&lt;script&gt;alert(1)&lt;/script&gt;)</p>\n'
vbscript         -> '<p>[x](vbscript:msgbox(1))</p>\n'
onerror_attr     -> '<p>&lt;p onerror=alert(1)&gt;hi&lt;/p&gt;</p>\n'
style_tag        -> '<p>&lt;style&gt;body{display:none}&lt;/style&gt;</p>\n'
form             -> '<p>&lt;form action=/x&gt;&lt;input&gt;&lt;/form&gt;</p>\n'
svg              -> '<p>&lt;svg/onload=alert(1)&gt;</p>\n'
srcdoc           -> '<p>&lt;iframe srcdoc="&lt;script&gt;"&gt;&lt;/iframe&gt;</p>\n'
img_onerr        -> '<p>&lt;img src=x onerror=alert(1)&gt;</p>\n'
```

Every candidate becomes inert literal text / escaped entities; no `<a>`, `<script>`, `<img>`, `<iframe>`, `<style>`, `<form>`, `<svg>` element survives.

---

## Orchestrator-declared fixes — confirmation

| ID | Claimed fix | Confirmed? | Evidence |
|---|---|---|---|
| (a) | `--color-surface-elevated` → `--color-bg-inset` in `.md` CSS; `.md{white-space:normal}` | **Yes** | `ui/static/ui/app.css`: `.md{font:var(--font-sans);color:var(--color-text);white-space:normal;line-height:1.6}`; `.md code{…background:color-mix(…var(--color-bg-inset)…)}`; `.md pre{background:var(--color-bg-inset);…}`. `rg 'color-surface-elevated' app.css` → **none**. |
| (b) | 7 wrong assertions in `test_ui_markdown.py` + `NameError` on undefined `out` fixed | **Yes** | File runs clean (command 1). Every test defines `out` before use; no `NameError`. |
| (c) | Stored-XSS: `{{ config.body\|safe }}` in `<textarea>` → autoescaping + regression test | **Yes** | `ui/templates/ui/playbook_form.html:107` now `{% if config.body %}{{ config.body }}{% endif %}` (no `\|safe`); regression `tests/conformance/test_playbook_roundtrip.py:193-223` asserts `&lt;/textarea&gt;` present and `</textarea><img`/`onerror="alert(1)"` absent. |
| (d) | `ui-playbook-edit` added to `_UI_GLOBAL_READ_ROUTES` | **Yes** | `tests/conformance/test_tenant_scoping.py:781`. |

---

## D1 — Playbook GET→POST round-trip (plan line 167)

| AC | Criterion | Status | Evidence (file:line) |
|---|---|---|---|
| D1.1 | Edit page **pre-selects the stored action** | Met | `ui/views.py:846-861` (GET passes `config`); `ui/templates/ui/playbook_form.html:52` `{% if config.action == a %}selected{% endif %}`; tests `test_playbook_roundtrip.py:162` (http), `:363` (python) |
| D1.2 | Round-trips **url** | Met | `playbook_form.html:60` `value="{{ config.url\|default:'' }}"`; parser `views.py:107-111`; test `:165` |
| D1.3 | Round-trips **method** | Met | `playbook_form.html:65-69`; parser `views.py:113-116`; test `:168` |
| D1.4 | Round-trips **timeout** | Met | `playbook_form.html:74`; parser `views.py:118-126`; test `:171`; no-op assertion `:275` |
| D1.5 | Round-trips **body** | Met | `playbook_form.html:107` (autoescaped); parser `views.py:143-145`; tests `:185`, `:193-223` (breakout), `:276` |
| D1.6 | Round-trips **action_path** (python) | Met | `playbook_form.html:114-119`; parser `views.py:147-156`; test `:366`, `:406-407` |
| D1.7 | Round-trips **headers incl. order** | Met | `playbook_form.html:81-102` iterates `config.headers.items` (insertion order) + pad rows; parser `views.py:128-141` builds dict in `header_key_<i>` index order; tests `:174-181`, `:287-325` (order `["First","Second","Third"]`), `:278` |
| D1.8 | **Unchanged save is a no-op** | Met | `test_playbook_roundtrip.py:225-285` (http), `:372-409` (python) assert `config` identical after GET→POST |
| D1.9 | A test asserts action config survives a **GET→POST cycle** | Met | `test_http_playbook_post_unchanged_is_noop` + `test_http_playbook_header_order_preserved` |

### D1 supporting conformance (plan §9 / prompt questions)

| Item | Status | Evidence |
|---|---|---|
| POST role gate (read-only → uniform 404) | Met | `ui/views.py:864-865`; test `:430-458` (`test_readonly_user_post_edit_denied` asserts 404); analyst allowed `:460-488` |
| Non-existent id → 404 (UUID **and** name) | Met | `ui/views.py:838-844`; tests `:494-506` |
| Route registered | Met | `ui/urls.py:136-138`; detail page link `playbook_detail.html:8` |
| Create path unchanged (regression) | Met | `:509-583` |
| **CSRF enforced** | Met (functional) / **test gap** | Form carries `{% csrf_token %}` (`playbook_form.html:15`); view is not `@csrf_exempt`, so global `CsrfViewMiddleware` enforces. **No test uses `Client(enforce_csrf_checks=True)` for `ui-playbook-edit`** (see Gap G1). |
| HTTP-method restriction | **Deviation (minor)** | `playbook_edit` is decorated only `@login_required` (`views.py:829`), unlike siblings `playbook_delete`/`playbook_run` which use `@require_POST`. Non-GET/POST verbs fall through to the update branch. See Gap G2. |

---

## D3 — Sanitized CommonMark rendering (plan line 168)

| AC | Criterion | Status | Evidence (file:line) |
|---|---|---|---|
| D3.1 | **Case description** renders as sanitized CommonMark | Met | `ui/templates/ui/case_detail.html:59` `{{ case.description\|markdown }}`; tests `test_ui_markdown.py:286-305` |
| D3.2 | **Comment/ledger bodies** render as sanitized CommonMark | Met | `ui/templates/ui/_timeline_entry.html:5-6` (only `event.kind == "comment"`); `ui/views.py:475` `case_comment` writes `append_timeline_event(..., kind="comment")` → HTMX partial uses same template; tests `:199-218` |
| D3.3 | **Server-side**, strict allow-list | Met | `ui/markdown.py:22` `MarkdownIt("commonmark", {"html": False})`; `:25-54` explicit tag/attr/url allow-lists; `:75-81` `nh3.clean(...)`; filter `ui_tags.py:78-94` |
| D3.4 | **No raw HTML** | Met | allow-list `ui/markdown.py:25-47`; tests `:27-55` (`<script>`, `<img>`, `<iframe>`, `<div>`); independent probe (style/form/svg/srcdoc) |
| D3.5 | **No `javascript:`** | Met | test `:57-69`; independent probe covers `javascript:` entities/case/whitespace, `data:`, `vbscript:` — none become links |
| D3.6 | **Plain-text fallback preserved** | Met | `_timeline_entry.html:8` non-comment kinds stay `<pre>`; tests `:220-265` (automation-run/status-change/alert-imported); empty description renders nothing `:307-320`; `render_markdown("")/None → ""` `:103-105` |
| D3.7 | Escaping test with **`<script>`/`<img onerror>`** | Met | `:27-33`, `:35-42` (plus filter-level `:163-176`) |
| D3.8 | Runtime dependency decision (Q1 → ADR-005) | Met | `requirements/base.txt:460,464,566` pins `markdown-it-py==4.2.0`, `mdurl==0.1.2`, `nh3==0.3.7`; `scripts/lock.sh` gains declared `BASE_ALLOW` (`git diff` hunk at `scripts/lock.sh:111-124`); dev closure movement allowed via `DEV_MOVED_TO_BASE` |
| D3.9 | Fail-closed on rendering error | Met | `ui/markdown.py:82-84` returns `escape(text)`; independent probe patched `_MD.render` to raise → escaped `SafeString` returned |

### D3 supporting conformance

| Item | Status | Evidence |
|---|---|---|
| CSP unaffected (no inline asset) | Met | `test_ui_inline_assets.py` green (command 2, 143 passed); rendering is server-side only |
| Empty input ≠ empty `<p>` | Met | `test_ui_markdown.py:103-105`, `:307-320` |
| Safe output is not double-escaped | Met | `ui_tags.py:91-94` (`mark_safe` after `nh3`); test `:158-161` (`<strong>bold</strong>`) |
| `markdown` filter handles `None`/empty | Met | `ui_tags.py:91` `str(value or "")`; `:178-181` |
| Extensive mixed-batch test impact | Met | `test_tenant_scoping.py` green with `ui-playbook-edit` allow-listed |

---

## Deviations

No acceptance criterion deviates from the plan. The following are **non-blocking quality/coverage gaps** in the implementation or its test net (AC still Met).

### G1 — No CSRF-enforcement test for `ui-playbook-edit` POST (coverage)
`tests/conformance/test_playbook_roundtrip.py` extracts a CSRF token and posts it, but its `Client` uses the default `enforce_csrf_checks=False`, so a missing/ignored token would not fail. The P2 sibling has the enforcing pattern (`test_ui_p2_wiring.py:84-87`, `:312-315`). CSRF *is* enforced at runtime by `CsrfViewMiddleware`. **Recommendation:** add a `Client(enforce_csrf_checks=True)` case for `ui-playbook-edit` (and ideally `ui-playbook-create`) for parity.

### G2 — `playbook_edit` lacks HTTP-method restriction (robustness)
`ui/views.py:829` decorates with `@login_required` only. A `PUT`/`DELETE`/`PATCH` from an analyst falls into the POST-update body (empty `request.POST` → validation error → 302 re-render); from a read-only user it hits the role gate → 404. Unlike `playbook_delete`/`playbook_run` (`@require_POST`). **Recommendation:** add `@require_http_methods(["GET", "POST"])`.

### G3 — Legacy lossy update branch remains in `playbook_detail` (dead code)
`ui/views.py:738-826` still contains the old detail-page POST updater that only clears headers (`clear_headers`, `:793-796`) and cannot round-trip header rows. After this batch, `playbook_detail.html` no longer posts to it (only `ui-playbook-run`/`ui-playbook-delete`), so it is unreachable from the UI — but it is a second, lossy code path. **Recommendation:** delete the `playbook_detail` POST branch or route it through `_parse_playbook_config`.

### G4 — `_parse_playbook_config` rebuilds config, discarding unrecognised keys
The parser constructs a fresh dict from POST fields, so any future/extra `config` keys not in the AC list (`url/method/timeoutSeconds/body/headers/action_path`) are dropped on save. Within the AC's field set this is lossless and the no-op test proves it; it is a forward-compat note, not a defect.

### G5 — Fail-closed test is tautological (coverage)
`test_ui_markdown.py:138-142` (`test_fail_closed_on_exception`) renders normal text and asserts it survives; it does not force an exception, so the `except` branch is untested by the suite. Independence probe #6 confirms the branch works. **Recommendation:** monkeypatch `ui.markdown._MD.render` to raise and assert `escape(text)`.

### G6 — `nh3` `url_schemes` denylist only exercised transitively (coverage)
`javascript:`/`data:` are rejected by `markdown-it`'s link validator before `nh3` sees them, so the test does not independently prove the `nh3` URL-scheme control. Defence-in-depth is by construction; probe #5 confirms behaviour. **Recommendation:** a unit test calling `nh3.clean` with an `<a href="javascript:…">` fragment, or asserting the filter/allow-list directly.

### G7 — Non-str filter input untested (coverage)
`ui_tags.markdown` coerces via `str(value or "")` (probe: `markdown(5) → '<p>5</p>'`, `markdown(['a','b']) → "<p>['a', 'b']</p>"`). `None`/`""` are tested; other types are not. Low risk (fields are CharFields).

---

## Blockers

None.

---

## Recommendations (non-binding)

1. Add the CSRF-enforcing test (G1) and the `@require_http_methods` guard (G2) to close the P5 mutation-route parity gap.
2. Delete or refactor the dead lossy `playbook_detail` POST updater (G3).
3. Strengthen the fail-closed test (G5) and add a direct `nh3` URL-scheme test (G6).
4. Extend `test_ui_urls_smoke.py`'s live-GET sweep to parameterised routes (it currently only sweeps no-arg / `case_id` routes, so `ui-playbook-edit` is not in the live-GET pass; it is covered by `test_playbook_roundtrip.py`).

---

## Verdict

**Approved with minor deviations.** All 18 P5 acceptance criteria are **Met**; the named gate command passes **133/133** and the CSP/UI conformance neighbours pass **143/143**; the ADR-005 XSS boundary holds under independent obfuscated-payload probes; and all four orchestrator-declared fixes are confirmed in the tree. The deviations (G1–G7) are test-coverage/robustness follow-ups only — none is a functional or security defect that blocks P5.
