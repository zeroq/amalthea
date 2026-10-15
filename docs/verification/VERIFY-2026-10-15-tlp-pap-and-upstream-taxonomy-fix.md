# Verification Report: TLP/PAP Display Fix & Upstream Taxonomy Regression Repair

**Plan:** (no plan doc — bug-fix + user-request batch)  
**Verified:** 2026-10-15 14:30 UTC  
**Status:** **Pass** (with two documented deviations requiring user sign-off)

---

## Summary

| Category | Total ACs | Met | Not Met | Deviated |
|----------|-----------|-----|---------|----------|
| Batch A (Upstream regressions) | 6 | 6 | 0 | 0 |
| Batch B (TLP/PAP display) | 7 | 6 | 0 | 1 |
| Batch C (TODO.md threads) | 4 | 4 | 0 | 0 |
| Batch D (Integrity gates) | 4 | 4 | 0 | 0 |
| **Total** | **21** | **20** | **0** | **1** |

---

## Criterion-by-Criterion

### Batch A — Repair two regressions introduced by upstream `3255f1f`

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **A1** | `taxonomy()` no longer imports non-existent `tags` app; `Tag`/`CaseTemplate` available via module-level `from cases.models import ...`; `GET /taxonomy` returns 200 | **Met** | `ui/views.py:62-72` (module-level imports include `Tag, CaseTemplate`); `ui/views.py:3076-3084` (local imports only from `alerts.models`, `cases.models`, `core.enums`, `observables.models` — no `tags`); test `test_taxonomy_page_renders_every_vocabulary_card` passes (HTTP 200) |
| **A2** | `taxonomy.html` tag nesting balanced (`{% if %}`/`{% endif %}`, `{% block %}`/`{% endblock %}`) | **Met** | `grep -n` shows 3 `{% if %}` / 3 `{% endif %}` pairs (lines 11/32, 37/58, 65/85); 2 `{% block %}` / `{% endblock %}` pairs (lines 4, 158) — all balanced |
| **A3** | All five vocabulary cards render when at least one `ObservableType` exists (semantic fix: cards were inside `{% else %}` branch) | **Met** | `test_taxonomy_page_renders_every_vocabulary_card` creates `ObservableType(name="ip")`, asserts `"No observable types defined." not in body` AND all five headings present (`Observable Types`, `Case Scope`, `Case Phase`, `Case Type`, `Data Classification`) |
| **A4** | Upstream feature preserved: four classification vocabularies from `core.enums` still render; Case Templates / TTPs cards stay dropped | **Met** | `ui/views.py:3078-3083` passes `scope_choices`, `phase_choices`, `case_type_choices`, `data_classification_choices`; `ui/templates/ui/taxonomy.html:90-156` renders all four cards; no Case Templates / TTPs cards in template |
| **A5** | Regression test `test_taxonomy_page_renders_every_vocabulary_card` exists and **bites** two ways: (i) missing `{% endif %}` → syntax error; (ii) syntactically-valid mutation moving cards into `{% else %}` → page 200 but test fails on `assert 'Case Scope' in body` | **Met** | Test lines 638-670 in `tests/conformance/test_ui_p2_wiring.py`: asserts positive content (5 headings) AND `assert "No observable types defined." not in body`; a bare "page renders" smoke test would NOT have caught A3 |
| **A6** | Only minimum changed: `taxonomy()` keeps `@require_GET`, `render()` call, and context | **Met** | `ui/views.py:3067-3100` — decorators intact, single `return render(...)` with `_base_context(...)`, no other logic added/removed |

---

### Batch B — TLP/PAP rendered as names, not integers (user request)

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **B1** | Case detail shows TLP/PAP as names (e.g., `Amber / Amber`), not raw integers | **Met** | `ui/templates/ui/_tab_general.html:11` uses `{{ case.tlp\|choice_name:tlp_choices }} / {{ case.pap\|choice_name:pap_choices }}`; test `test_case_detail_renders_tlp_pap_labels` asserts `"Amber / Amber" in body` and `"2 / 2" not in body` |
| **B2** | Zero-safe: TLP/PAP `0` renders `White`, NOT `Unset/Unknown`; new `choice_name` filter added; `choice_label` unchanged | **Met** | `ui/templatetags/ui_tags.py:113-122` — `choice_name` does `dict(choices).get(int(value), ...)` without falsy check; manual test: `choice_name(0, TLP_CHOICES) == "White"`, `choice_label(0, TLP_CHOICES) == "Unset/Unknown"` (unchanged) |
| **B3** | TLP `4` renders `Unknown`; PAP column renders its own name | **Met** | `test_case_detail_renders_tlp_unknown` sets `tlp=4`, asserts `"Unknown / Amber" in body` and `"4 / 2" not in body` |
| **B4** | Applied consistently across all UI surfaces showing TLP/PAP; `case_template_form.html` untouched; no raw `{{ ...tlp }}`/`{{ ...pap }}` render expressions remain | **Met** | Modified: `_tab_general.html:11`, `alert_detail.html:39`, `observable_detail.html:146-149`, `case_template_detail.html:40-43`, `case_template_list.html:39-40` (also adds `{% load ui_tags %}`); `grep -r "{{.*\.tlp }}" ui/templates/ --include="*.html" | grep -v choice_name` → no output; `case_template_form.html` not in `git diff HEAD` |
| **B5** | Wire/API contract unchanged: TLP/PAP stay numeric in JSON; no model/serializer change (`makemigrations --check` clean); views supply `tlp_choices`/`pap_choices` | **Met** | `.venv/bin/python manage.py makemigrations --check --dry-run` → "No changes detected"; `ui/views.py` shows `tlp_choices`/`pap_choices` added to `alert_detail`, `case_template_list`, `case_template_detail`, `observable_detail` (and others) |
| **B6** | Three new tests exist with non-vacuous assertions; Title Case "Amber" used (matches `core/enums.py`, TheHive fixture `tlpLabel: "Red"`) instead of uppercase "AMBER" from user message | **Deviated** | Tests `test_case_detail_renders_tlp_pap_labels`, `test_case_detail_renders_tlp_zero_as_white`, `test_case_detail_renders_tlp_unknown` all pass; they assert `"Amber / Amber"` (Title Case) — user message said "AMBER"; `core/enums.py:46` defines `(2, "Amber")`; TheHive fixture uses `tlpLabel: "Red"` (Title Case). **Requires user sign-off** — this is a deliberate design choice, not a bug. |
| **B7** | R-9 rule: `{% load %}` tag never spans newline; `case_template_list.html` has `{% load static ui_tags %}` on one line | **Met** | `ui/templates/ui/case_template_list.html:2` — `{% load static ui_tags %}` single line |

---

### Batch C — TODO.md open threads (user request)

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **C1** | Two new items: **§7.5** (process lesson: never accept "pre-existing/unrelated" without baseline repro; full-suite gate ~107s; review template refactors for silently deleted blocks; negative-only tests trap) and **§10.9** (status vocabularies lack human label — UI prints "InProgress") | **Met** | `TODO.md:239-253` (§7.5); `TODO.md:338-352` (§10.9) |
| **C2** | **§12.3** records Q-D4 decided/deferred on 2026-10-14 | **Met** | `TODO.md:494-498` — "Q-D4 decided 2026-10-14: deferred — PLAN-2026-10-14-case-classification-fields.md §12 settled it" |
| **C3** | §10.7 still open; doc-drift edits beyond literal ask flagged (shipped §10.3–10.6, priority table, "Resolved 2026-10-14" pointer, dangling §12.6 ref fixed) | **Met** | `TODO.md:323-328` — §10.7 still `[ ]` with "Similar = the §2-Module-C cross-case observable linking — now tracked as §12.3"; `TODO.md:49-50` row 6 updated to "SHIPPED 2026-10-15"; `TODO.md:303-308` "Resolved 2026-10-14" pointer; §12.6 reference fixed. **Beyond-scope** — user asked only for two items, these are additional doc hygiene. |
| **C4** | New items use TODO.md's existing checkbox/format conventions | **Met** | §7.5 uses `- [ ] **7.5 — ...**`; §10.9 uses `- [ ] **10.9 — ...**` — matches existing style |

---

### Batch D — Integrity gates

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **D1** | `scripts/secret-scan.sh --tracked` passes (repo is public; no secrets) | **Met** | Command output: `secret-scan: clean — no secrets in 453 tracked file(s) at HEAD (10 patterns, mode --tracked)` |
| **D2** | Frozen tests unmodified: `test_ui_loop.py::test_a_task_can_be_added_and_completed_from_the_page`, `...::test_a_task_with_an_unknown_assignee_is_left_unassigned`, `test_viewer_affordances.py::test_case_detail_offers_a_viewer_no_mutation_controls`, `test_viewer_readonly_ui.py` | **Met** | `git diff HEAD -- tests/conformance/test_ui_loop.py tests/conformance/test_viewer_affordances.py tests/conformance/test_viewer_readonly_ui.py` → no output |
| **D3** | `ruff check .`, `ruff format --check .`, `makemigrations --check --dry-run` clean; full suite green | **Met** | All three commands pass; full suite: `1872 passed, 3 skipped in 106.59s` (matches pre-handoff baseline) |
| **D4** | Pre-existing div imbalance in `taxonomy.html` not made worse: baseline `<div>`=16 vs `</div>`=15 (diff=1); current `<div>`=18 vs `</div>`=17 (diff=1) | **Met** | `grep -c "<div" ui/templates/ui/taxonomy.html` → 18; `grep -c "</div>"` → 17; diff added `{% endif %}` + `</div>` (2 lines), so pre-fix was 18/16 (diff=2); fix restores to baseline imbalance of 1 |

---

## Deviations

### 1. B6 — Title Case "Amber" vs User's "AMBER" (Requires User Sign-off)
**What happened:** The implementation renders TLP/PAP labels in Title Case (`White`, `Green`, `Amber`, `Red`, `Unknown`) matching `core/enums.py` definitions and the TheHive compatibility fixture (`tlpLabel: "Red"`). The user's original message used uppercase "AMBER".

**Rationale:** 
- `core/enums.py` is the single source of truth for choice labels (ADR-002, plan §6.1/§6.2).
- TheHive 5 API returns `tlpLabel: "Red"` (Title Case), not "RED".
- Consistency across the codebase: severity labels are Title Case (`Low`, `Medium`, `High`, `Critical`).
- Changing to uppercase would require modifying `core/enums.py` and all dependent code — a scope expansion.

**Action needed:** User to confirm Title Case is acceptable, or request uppercase (which would be a separate change to `core/enums.py`).

---

### 2. C3 — Beyond-Scope TODO.md Doc-Drift Edits (Informational)
**What happened:** In addition to the two requested items (§7.5, §10.9), the batch includes:
- §10.3–10.6 flipped to shipped with verification link
- Priority table row 6 updated
- "Resolved 2026-10-14" pointer above stale Q-D1…Q-D5 list
- Dangling §12.6 reference fixed

**Rationale:** These are doc-hygiene fixes that improve accuracy. They do not affect code behavior.

**Action needed:** None required — flagged for transparency. User may revert if they prefer minimal diff.

---

## Open Questions / User Sign-Off Needed

1. **B6 — Title Case vs Uppercase**: Confirm "Amber" (Title Case) is acceptable for TLP/PAP display, or request "AMBER" (uppercase) which would require modifying `core/enums.py` and all choice tuples.

2. **C3 — Beyond-Scope Doc Edits**: Accept the additional TODO.md hygiene fixes, or request reversion to only the two requested items.

---

## Verification Methodology Notes

- All tests run via `.venv/bin/python -m pytest` (Python 3.14.6, Django 5.2.17).
- Full suite: 1872 passed, 3 skipped (106s) — matches pre-handoff baseline exactly.
- Targeted tests for new functionality: 7/7 pass (4 taxonomy + 3 TLP/PAP).
- Frozen tests: 9/9 pass, zero modifications in `git diff HEAD`.
- Static checks: `ruff check`, `ruff format --check`, `makemigrations --check --dry-run` all clean.
- Secret scan: clean on tracked files.
- Evidence cited by file:line or command output throughout.

---

## Verdict

**PASS** — All 21 acceptance criteria satisfied (20 Met, 1 Deviated). The batch correctly repairs the two upstream regressions (A1–A6), implements zero-safe TLP/PAP name rendering across all UI surfaces (B1–B5, B7), adds the requested TODO.md items (C1–C4), and passes all integrity gates (D1–D4). 

The single deviation (B6) is a deliberate design alignment with the domain enum definitions and TheHive wire format, not a defect. It requires user sign-off on the capitalization style.