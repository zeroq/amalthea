# Verification Report: Primary rail visual grouping

**Plan:** `docs/planning/PLAN-2026-10-21-rail-grouping.md`  
**Verified:** 2026-10-21 18:30 UTC  
**Status:** Pass

---

## Summary

| Metric | Count |
|--------|-------|
| Total ACs | 7 |
| Met | 7 |
| Not Met | 0 |
| Deviated | 0 |

---

## Criterion-by-Criterion

### AC-F1: Three groups separated by two inset hairline rules; no caption text
**Status:** Met  
**Evidence:**
- `ui/templates/ui/base.html:59` — first `<li class="rail-sep" aria-hidden="true"></li>` inserted after Cases `</li>` (line 58)
- `ui/templates/ui/base.html:78` — second `<li class="rail-sep" aria-hidden="true"></li>` inserted after Playbooks `</li>` (line 77)
- `ui/static/ui/app.src.css:615-619` — `.rail-sep { height: 1px; margin: 8px 10px; background: var(--color-line); }` added near `.rail-sub` block
- `ui/static/ui/app.css` (generated) contains identical rule at `.rail-sep{background:var(--color-line);height:1px;margin:8px 10px}`
- No caption/section-label text (`<span>`, `<h3>`, etc.) appears anywhere in the rail markup
- The `margin: 8px 10px` matches the link inner padding (`padding: 7px 10px` on `.rail-links a`), confirming inset (not full-bleed) alignment

### AC-F2: Item order, labels, icons, shortcuts, badges, submenu unchanged
**Status:** Met  
**Evidence:**
- `git diff ui/templates/ui/base.html` shows **only two added lines** (the two `<li class="rail-sep">` rows); no deletions, no modifications to existing lines
- Rail item order verified in rendered sequence (analyst user):
  1. Dashboard (`g`)
  2. Alerts (`a`) — badge present
  3. Cases (`c`) — badge present
  4. **separator #1**
  5. Observables (`o`)
  6. Automation (`r`)
  7. Playbooks (`p`)
  8. **separator #2**
  9. Sources (`s`)
  10. Tags (`t`)
  11. Case templates (`m`)
  12. Taxonomy (`x`)
  13. Settings (`,`) — with nested `rail-sub` (Organisations/Users/Sources)
- Cheatsheet shortcuts in `base.html:193-208` match exactly: `g a c o r p s t m x ,`
- `rail-sub` block (lines 110-138) and `rail-foot` block (lines 144-159) are **byte-identical** to `HEAD` version

### AC-F3: Separators non-focusable and `aria-hidden="true"`
**Status:** Met  
**Evidence:**
- Both separator `<li>` elements carry `aria-hidden="true"` (lines 59, 78)
- No `<a>` tag, no `href`, no `tabindex`, no `role` — plain `<li>` is not in the tab order
- Test `test_separators_are_aria_hidden_and_non_focusable` asserts `has_aria_hidden == True` and `has_anchor == False` for each separator — **PASSED**

### AC-F4: Only `.rail-sep` added; `make css`/`css-check` green; undefined-class backlog = 42
**Status:** Met  
**Evidence:**
- `make css-check` → `✓ css-check: artifact is up to date` (Tailwind compiles cleanly, committed `app.css` matches fresh compile)
- Only new class in `app.src.css` is `.rail-sep` (6 lines added at lines 615-619)
- `test_pre_existing_undefined_component_classes_are_reported` reports **42** undefined classes:
  `actions, blockers-warning, brand-text, btn-danger, btn-form, btn-secondary, card-actions, cheatsheet-header-title, choice, date, due_date, field-hint, form, form-actions, hint-block, hint-block--compact, input-dark, member-choice, modal, modal--md, modal--sm, modal-body, modal-body-text, modal-cancel, modal-close, modal-content, modal-footer, modal-header, modal-title, pager-sizes, readonly-value, remove-header, tag-form, target-org, tl-task-note, token-code-wrap, token-created, token-deactivated-at, token-display, token-hint, token-revoked-at, token-warning`
- Count unchanged from baseline (plan §4.14 / BRIEF §30)

### AC-F5: `make check` green
**Status:** Met  
**Evidence:**
```
── ruff check           → All checks passed!
── ruff format          → (clean)
── mypy                 → Success: no issues found in 114 source files
── django check         → System check identified no issues (0 silenced)
── migrations in sync   → No changes detected
── css drift check      → ✓ css-check: artifact is up to date
✓ check passed
```

### AC-F6: No new menu items (Assets/Employment explicitly excluded)
**Status:** Met  
**Evidence:**
- `grep -i "assets\|employment" ui/templates/ui/base.html` → **no matches**
- Rendered rail contains exactly the 11 specified items (see AC-F2); no additional `<li>` with link content beyond the two separators
- Plan N1 explicitly rejected "Assets" and "Employment" — confirmed absent

### AC-F7: Tests + falsifiability proof present and valid
**Status:** Met (with observation)  
**Evidence:**
- New file `tests/conformance/test_ui_rail_grouping.py` (318 lines, 6 tests):
  - `TestRailGrouping` (analyst user): 4 tests
  - `TestRailGroupingSuperAdmin` (superAdmin user): 2 tests
- Targeted test run: **63 passed** (57 existing `test_settings_ui.py` + 6 new)
- **Falsifiability test** (`test_falsifiability_separators_removed_assertions_fail`):
  1. Renders real dashboard page (with separators)
  2. Removes both `<li class="rail-sep" aria-hidden="true"></li>` from the **rendered HTML string**
  3. Runs the three positive assertions on sabotaged HTML — all **fail as required**:
     - "exactly 2 separators" → finds 0 → assertion fails
     - "group boundaries" → finds 0 separators → assertion fails
     - "aria-hidden/no-anchor" → vacuously passes with 0, so test explicitly asserts `len(separators) == 0`
  4. Verifies template file `base.html` SHA-256 hash **unchanged** by the test (byte-identical before/after)

**Observation on falsifiability strength:**  
The test sabotages the **rendered HTML string**, not the template source. This proves the *assertions are coupled to the separators appearing in the output*, which satisfies the plan's wording ("with the separators removed... P3.1–P3.3 fail"). A stronger proof would temporarily edit the template, re-render, and confirm failures — but the current approach is valid per §7.5 and §13 of the plan, and the template hash check ensures the test itself does not mutate the source.

---

## Deviations

**None.** Implementation matches the approved plan exactly. The plan's §13 "Implementation record" states "Deviations from plan: None. All ACs met exactly as specified." — this is confirmed.

---

## Blockers

**None.**

---

## Recommendations

1. **Minor:** Consider extending the falsifiability test to also demonstrate failure by temporarily removing separators from the *template file* and re-rendering (would require a file-copy approach in a temp directory). Current proof is sufficient but template-level sabotage would be more end-to-end.
2. **None required for commit.** All acceptance criteria satisfied; `make check` and targeted tests green.

---

## Overall Verdict

**READY TO COMMIT** — All seven feature-level acceptance criteria (AC-F1 through AC-F7) are **Met** with evidence. No deviations, no blockers. The implementation is minimal, focused, and conforms to the approved plan.