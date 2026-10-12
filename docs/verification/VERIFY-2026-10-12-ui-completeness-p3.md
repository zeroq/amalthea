# Verification Report: UI completeness wave — Plan phase P3 (A5, Procedures / TTP UI)

**Plan:** `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P3 (lines 154–168, incl. the P3 status note)
**Verified:** 2026-10-12 12:30 UTC — working tree @ `8f38292`, 11 uncommitted paths (P3 only)
**Verifier:** verifier agent (read-only)
**Status:** **Pass** — all plan ACs Met; one documented interpretation deviation; documentation count/figures stale.

---

## Summary

| | Count |
|---|---|
| Total acceptance criteria evaluated | **5** |
| Met | **5** |
| Not Met | **0** |
| Deviated (AC still met, non-blocking note) | **0** (one interpretation deviation, documented as `W616P3-1`) |

- Plan bullet 1 (attach + list ATT&CK ID + description): **3 / 3 Met**
- Plan bullet 2 (no-JS; invalid id → message not 500): **2 / 2 Met (one interpretation deviation)**
- Phase-status assertions (5 defects fixed during the phase): **all confirmed**
- New test failures introduced by this batch: **none**
- Blockers: **none**

> Scope: working tree contains only the P3 change set (see `git status`: `ui/views.py`, `ui/urls.py`, `ui/templates/ui/case_detail.html`, `ui/static/ui/app.css`, the new `_procedure_entry.html`, the new test files, and the doc edits). The prior P2/P5 work is already committed (`3251dd2`, `8f38292`) and was touched only to avoid false positives.

---

## Commands run (exact) and observed results

| # | Command | Result |
|---|---|---|
| 1 | `.venv/bin/pytest tests/conformance/test_ui_procedures.py tests/conformance/test_ui_css_tokens.py -q` | **23 passed in 5.35s** (21 + 2) |
| 2 | `.venv/bin/pytest tests/conformance/test_ui_nav.py tests/conformance/test_ui_icons.py tests/conformance/test_ui_table_markup.py tests/conformance/test_ui_inline_assets.py tests/conformance/test_tenant_scoping.py tests/conformance/test_ui_urls_smoke.py -q` | **244 passed in 16.69s** |
| 3 | `.venv/bin/pytest -q` (full gate test run) | **1537 passed, 3 skipped in 96.05s** |
| 4 | `.venv/bin/ruff check ui/views.py ui/urls.py tests/conformance/test_ui_procedures.py tests/conformance/test_ui_css_tokens.py` | **All checks passed!** |
| 5 | `.venv/bin/ruff format --check <same 4 files>` | **4 files already formatted** |
| 6 | `.venv/bin/mypy` | **Success: no issues found in 105 source files** |
| 7 | `make css-check` | **Tailwind input compiles** (compile-check only) |
| 8 | Independent savepoint probe (see Adjudication 3) | **`WITHOUT-SAVEPOINT results: ['IntegrityError', 'T3-TransactionManagementError']`**; with the savepoint the later item is created |
| 9 | `pytest --collect-only -q` | **1540 tests collected** (matches 1537 passed + 3 skipped) |

The full gate (`make check`) per the plan status note was reported green at **1537 passed / 3 skipped**; commands 3/4/5/6/7 independently reproduce each constituent of that gate.

---

## Acceptance criteria — criterion by criterion

### AC1. Case detail can attach a procedure/TTP (new partial + form)
**Status: Met**
Evidence:
- New partial `ui/templates/ui/_procedure_entry.html` (28 lines) and the attach card in `ui/templates/ui/case_detail.html:118-167` (a real `<form method="post">` carrying `{% csrf_token %}`; fields `patternId`/`patternName`/`tactic`/`occurDate`/`description`/`ttpId`).
- Routes registered once each: `ui/urls.py:74-83` (`ui-case-procedure-create`, `ui-case-procedures-create`).
- Backed by `ui/views.py::case_procedure_create` (`:2044-2091`), which resolves the case through `resolve_case(..., write=True)` (uniform 404), validates via `compat.procedures.parse_procedure` with the canonical `_resolve_ttp_value` imported from `cases.views`, and persists through `cases.procedures.create_procedure` in `transaction.atomic()`.
- Tests: `tests/conformance/test_ui_procedures.py:140-176` (card + form), `:598-611` (real form + CSRF), `:226-276` (valid create, with and without a linked TTP).

### AC2. List shows attached procedures with ATT&CK ID
**Status: Met**
Evidence:
- `ui/templates/ui/_procedure_entry.html:5-11` renders `procedure.ttp.ttp_code` when the FK is set, else `procedure.pattern_id`; `ui/views.py:440` supplies `procedures=case.procedures.select_related("ttp").order_by("occur_date", "id")`.
- Test `test_case_detail_renders_procedures_card` (`:140-176`) asserts the ATT&CK ID is present; `test_case_detail_procedure_shows_ttp_code_when_linked` (`:178-205`) proves the FK precedence branch specifically (distinct values `T9001` vs `T1566.001`, chip extracted by regex).
- Empty state covered by `:208-218`.

### AC3. List shows the procedure description
**Status: Met**
Evidence:
- `ui/templates/ui/_procedure_entry.html:15-17` renders `{{ procedure.description|markdown }}` (the existing sanitized CommonMark filter; `{% load ui_tags %}` at line 1), consistent with P5/D3 and ADR-005.
- Tests: `:614-634` (Markdown renders), `:637-665` (XSS payload neutralised).

### AC4. Works without JS
**Status: Met**
Evidence:
- The attach control is a plain HTML form (`case_detail.html:128`) with a CSRF token and a visible submit label; no HTMX/JS is required and none is added. The view is a classic POST→302→GET (redirect to the case detail) with Django messages.
- Tests: `test_case_detail_procedure_form_is_real_form_with_csrf` (`:598-611`); the mutation tests exercise the plain client POST path (`:226-422`). `tests/conformance/test_ui_inline_assets.py` remains green (command 2 — no inline asset reintroduced).

### AC5. Invalid ATT&CK id → message, not 500
**Status: Met — with one documented interpretation deviation (`W616P3-1`)**
Evidence:
- No failure path returns a 500. `ui/views.py::case_procedure_create` rejects a missing/blank `patternId` early (`:2057-2060`, flash + redirect); `parse_procedure` returns a `(field, message)` tuple for a malformed `occurDate` and an unknown `ttpId` (`compat/procedures.py:35-45`), which the view turns into a flash + redirect (`:2072-2076`).
- Tests genuinely exercise all three rejection paths: unknown `ttpId` (`:279-305`), malformed `occurDate` (`:308-335`), missing `patternId` (`:338-360`); each asserts `302` + no row created + the message text on the followed page.
- **Interpretation deviation:** the literal wording "invalid ATT&CK id" (i.e. a syntactically odd, non-empty `patternId`) is *not* rejected — `pattern_id` is deliberately a free-text `CharField` in the model and in `compat.procedures.parse_procedure` (TheHive parity), so the UI does not invent a stricter validation than the API. This is documented in the plan status note and `docs/spec/deviations.md` `W616P3-1`. The criterion's intent — an invalid reference (`ttpId` FK) or malformed field produces a message, never a 500 — is satisfied. See Adjudication 1.

---

## Phase-status assertions (defects the status note claims were fixed)

| Assertion | Confirmed? | Evidence |
|---|---|---|
| **CSS token bug** — `.procedures`/`.form-grid` referenced 7 undefined `--color-*` tokens; now mapped to real theme tokens; `outline:none` removed | **Yes** | `ui/static/ui/app.css`: none of `--color-border`, `--color-fg`, `--color-fg-muted`, `--color-bg-elevated`, `--color-primary`, `--color-primary-focus`, `--color-danger`, `--color-surface-elevated` are used *or* defined; the new rules use `--color-line`, `--color-text`, `--color-bg-raised`, `--color-text-dim` (all defined). `outline:none` no longer appears; the global `:focus-visible{outline:2px solid var(--color-accent)}` is intact. Guarded by `test_ui_css_tokens.py`. |
| **Bulk-create per-item savepoint** — one bad row no longer poisons later items | **Yes** | `ui/views.py:2182-2190` wraps each `create_procedure` in its own `with transaction.atomic()` inside the batch `with transaction.atomic()`; regression test `test_procedures_bulk_create_survives_a_db_level_failure_in_one_item` (`:516-567`) and independent probe (Adjudication 3). |
| **Duplicated `_resolve_ttp_value`** — UI imported the canonical resolver instead of copying it | **Yes** | `ui/views.py:63` imports `_resolve_ttp_value` from `cases.views`; used at `:2074` and `:2175`; no local definition remains in `ui/views.py`. |
| **Message hygiene** — exception text no longer flashes to the analyst | **Yes** | Single path (`:2083-2087`) and bulk path (`:2190-2197`, `:2200-2204`) log the detail via `logger.exception(...)` and flash a generic message. Test asserts `procedure_exactly_one_parent` never appears in the body (`:567`). |
| **Test hardening** — ttp-code precedence + XSS tests rewritten to genuinely assert | **Yes** | `:178-205` and `:637-665` (Adjudication 2). |

---

## Adjudications (the four items requested)

### 1. Is "invalid ATT&CK id → message, not 500" met, given `pattern_id` is free text?
**Yes — Met (with a documented interpretation deviation).**
There is no input to either procedure view that yields a 500: the two parsers (`parse_timestamp`, `parse_procedure`) are total and return errors, and both views always end in a redirect with a flash message. The produced errors cover the three observable failure modes — unknown `ttpId` (FK), malformed `occurDate` (timestamp), and missing `patternId` (required) — each with a test. A syntactically odd but non-empty `patternId` is accepted by design (TheHive parity: `pattern_id` is a plain string in the model and the API), so trading narrower validation for API/UI consistency is the correct call, not a defect. The plan's own status note pre-declares this reading, and `deviations.md::W616P3-1` records it. The AC is satisfied in intent; the only literal reading that fails ("reject a malformed ATT&CK id string") is the one deliberately not implemented.

### 2. Do the tests genuinely constrain behaviour, or are any tautological/vacuous?
**Genuinely constraining; the two rewritten tests are no longer tautological.**
- **TTP-code precedence (`:178-205`)** — *not* tautological. The linked TTP code (`T9001`) and the procedure's `pattern_id` (`T1566.001`) are deliberately different, and the assertion extracts the actual chip text with `re.search(r'class="procedure-attack-id">\s*([^<]*?)\s*</span>')` and requires `== "T9001"`. Rendering `pattern_id` (or empty) fails.
- **XSS (`:637-665`)** — *not* vacuous. It asserts the entry rendered, that the live forms are absent (`<script>alert(1)</script>`, `<img src=x`), the escaped forms are present (`&lt;script&gt;`, `&lt;img src=x`), **and** that no live `on*=` attribute survives (`re.search(r"<[^>]*\bon[a-z]+\s*=", body) is None`). That is a meaningful assertion about output.
- Minor (non-blocking) weakness: the *count* assertions in `:152` and `:217` use unanchored `<span class="count">N</span>` even though `case_detail.html` has five `.count` spans (observables/procedures/runs/tasks/pages). They are not scoped to the procedures card, so they could pass for the wrong card; the content assertions (ATT&CK ID, description, "No procedures attached yet") still constrain the AC. See Gap G1.

### 3. Is the bulk-create per-item savepoint fix real and covered (a test fails without it)?
**Yes.** `ui/views.py:2182-2190` places each `create_procedure` in its own `with transaction.atomic()` (a savepoint) inside the batch transaction, with a comment stating the exact failure mode. Coverage: `test_procedures_bulk_create_survives_a_db_level_failure_in_one_item` (`:516-567`) monkeypatches `cases.procedures.create_procedure` so the 2nd call inserts an orphan `Procedure` (violating the `procedure_exactly_one_parent` CHECK) and asserts items 1 and 3 survive. Independent probe confirmed the mechanism the fix protects against: inside a single atomic block, after catching an `IntegrityError` from an insert, the very next insert raises **`TransactionManagementError`** (`WITHOUT-SAVEPOINT results: ['IntegrityError', 'T3-TransactionManagementError']`), whereas wrapping the failing insert in its own `transaction.atomic()` lets the later insert succeed. Therefore the regression test's third-item assertion can only pass with the savepoint, i.e. it fails without the fix.

### 4. Does the new `test_ui_css_tokens.py` catch the bug class it claims to?
**Yes, for the class it names.**
- `test_every_color_token_used_is_defined` computes `used - defined` over `--color-*` tokens using `var(--color-…)` vs `--color-…:`. The original defect names (`--color-border`, `--color-fg`, `--color-fg-muted`, `--color-bg-elevated`, `--color-primary`, `--color-primary-focus`, `--color-danger`, plus the D3 `--color-surface-elevated`) are all absent from both sets today and would have been reported as `missing` before the fix. The file is the compiled artifact, i.e. exactly where an undefined `var()` silently drops a declaration, so the guard runs at the right layer.
- `test_no_rule_removes_the_focus_outline` catches the `outline:none` WCAG 2.4.7 regression removed in this phase.
- Boundary, by design: the check is scoped to `--color-*` only (documented in the test docstring); it does not cover other custom-property families (`--font-*`, `--radius-*`, `--tw-*`) whose misuse would be a different bug class. It would also not distinguish a token "defined" in a stray comment/declaration outside `:root` (none exist in this minified artifact). Within its stated scope (colour tokens + focus outline), it genuinely catches the bug class.

---

## Deviations

No acceptance criterion deviates from the plan. The implementation deviates from the *literal* plan text in four places, **all pre-declared** in `docs/spec/deviations.md` "Wave 6.16-P3" and the plan status note:

### D1 — `W616P3-1`: `patternId` enforced as required (interpretation of AC5)
Documented. `pattern_id` remains free text; only blank is rejected. Intent of AC5 (no 500) is preserved. See Adjudication 1.

### D2 — `W616P3-2`: bulk endpoint rewritten from a JSON API proxy to a UI POST
Documented. `ui-case-procedures-create` now parses form fields (indexed `patternId_0…` or list-style) and reports per-item counts via messages. Justified: the previous `@require_GET`/API-proxy behaviour could not serve a browser form. Covered by `:428-590`.

### D3 — `W616P3-3`: duplicate route definitions removed
Documented and independently confirmed. `HEAD:ui/urls.py` defined `ui-case-template-list`, `ui-case-procedure-create` and `ui-case-procedures-create` **twice** (lines ~76–101 and ~120–130); the working tree removes the second block, leaving exactly one definition each (`ui/urls.py:74-86`). No route was lost — `reverse(...)` for all three still resolves (tests pass).

### D4 — `W616P3-4`: `case_procedure_create` changed `@require_GET` → `@require_POST`
Documented. A write route was GET-decorated and delegated to the DRF API; it now implements the POST directly, mirroring the P2 mutation pattern. `test_procedure_create_post_only_no_get` (`:414-421`) asserts `405`.

### D5 — Doc figures stale (documentation-only, NOT covered by a deviations entry)
`COMPLETED.md`, `TODO.md` and `docs/spec/deviations.md` record the P3 test file as **"20 cases"** and the gate as **"1533 passed / 3 skipped"**. Actual: `test_ui_procedures.py` collects **21** tests (pytest command 1/9; the plan status note also says 21), and the full suite is **1537 passed / 3 skipped** (command 3). The plan note is correct; the three doc edits under-count by one test and under-report the gate by four passes. Non-blocking, but the recorded evidence disagrees with the tree.

---

## Gaps (non-blocking; AC still Met)

### G1 — Procedure-card count assertions are not scoped to the card
`test_ui_procedures.py:152` and `:217` assert an unanchored `<span class="count">1</span>` / `>0<`; `case_detail.html` has five `.count` spans, so the assertion could match a different card. Recommendation: scope the assertion to the `id="case-procedures"` list or parse the `h-procs` heading.

### G2 — Authz matrix thinner than plan §10 specifies
Cross-org (`:363-377`, `:570-584`), anonymous (`:380-394`) and CSRF (`:397-411`) are covered. A same-org **read-only / read-share** caller (expected uniform 404 because both views call `resolve_case(..., write=True)`) and a superAdmin case are not. Low risk: the scoping helper is the shared, already-verified one; the views add no bespoke org filter.

### G3 — `ui-case-procedures-create` remains an orphan route
The bulk endpoint is now a UI POST but has no form entry point; it is reachable only by typing the URL, so plan Goal **G1** ("no orphan routes") is not fully met for it. This is **explicitly accepted** in the plan status note and `deviations.md` ("Residual risk (accepted, Low)"). The single-attach flow required by P3 is reachable.

### G4 — Broad `except Exception` around `create_procedure`
Both views log-and-genericise any exception, which also swallows programming errors (they land in the logger, not the analyst). This is the intended message-hygiene pattern and matches the phase's stated fix; noted only as a diagnostic trade-off.

---

## Blockers

None.

---

## Recommendations (non-binding)

1. Reconcile the documentation figures (`COMPLETED.md`, `TODO.md`, `deviations.md`): 21 cases, gate 1537 passed / 3 skipped (D5).
2. Scope the procedures-card count assertions to the card (G1).
3. Add the read-only/read-share 404 case (and a superAdmin case) to complete the §10 authz matrix (G2).
4. Either give `ui-case-procedures-create` a UI entry point (or delete it) to close the last orphan route (G3 / TODO §7.5).

---

## Verdict

**Approved.** All five P3 acceptance criteria are **Met**; the two plan bullets are implemented exactly as scoped, the four criteria-specific questions resolve in the implementation's favour (the single interpretation deviation, `W616P3-1`, is deliberate, documented and correct for TheHive parity), the five named in-phase defects are all confirmed fixed, and the two rewritten tests now genuinely constrain behaviour. The full suite reproduces **1537 passed / 3 skipped**, with ruff, format, mypy (105 files) and the CSS compile check all clean. The only findings are non-blocking documentation drift and test-coverage nits.
