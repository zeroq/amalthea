# PLAN — 2026-10-21 — Primary rail visual grouping

**Status:** APPROVED (user sign-off on mockup, 2026-10-21)
**Owner:** planner
**Implementer:** django-frontend
**Verifier:** verifier
**Feature size:** XS (markup + one CSS rule + tests)

---

## 1. Summary

The primary navigation rail (`ui/templates/ui/base.html`) is an 11-item flat list. This change
regroups the items into three logical sections separated by **inset hairline rules** (Option A:
separators only, no caption text). Nothing is reordered, renamed, or re-routed; keyboard shortcuts
are unchanged. The change is purely presentational.

Final groups:

1. **Triage:** Dashboard, Alerts, Cases
2. **Investigate & respond:** Observables, Automation, Playbooks
3. **Configure:** Sources, Tags, Case templates, Taxonomy, Settings

## 2. Goals

- G1: Add two visual separators in the rail so the three groups read as distinct.
- G2: Keep the existing item order, labels, icons, shortcuts and badges byte-identical.
- G3: Separators are non-focusable and hidden from assistive tech — keyboard traversal and the
  `g`-then-key muscle memory are unaffected.
- G4: Visual style matches the dark theme and existing rail chrome (no new colours, no captions).

## 3. Non-goals

- N1: **No new menu items.** "Assets" and "Employment" were considered and explicitly rejected
  (user decision, 2026-10-21) — they are **not** part of this change and must not be added.
- N2: No reordering of existing items.
- N3: No renaming, no route/URL changes, no view changes.
- N4: No keyboard-shortcut changes.
- N5: No caption/section-label text (Option B was declined in favour of Option A).
- N6: No narrow-screen collapse handling — the rail has no collapse behaviour (fixed 220px), so
  this is not applicable (user decision).
- N7: No change to the nested `rail-sub` Settings submenu behaviour.
- N8: No new icons.
- N9: No new Tailwind/CSS classes beyond `.rail-sep` (undefined-class backlog baseline stays 42).

## 4. Architecture / approach

The rail is a single `<ul class="rail-links">` with `display:grid; gap:2px`. The minimal change is
to insert a **non-focusable separator row** as a grid child between groups. Two separators are
required: after **Cases** (end of group 1) and after **Playbooks** (end of group 2).

Chosen markup: `<li class="rail-sep" aria-hidden="true"></li>`.

* Why `<li>` — it is a legal child of `<ul>`; no wrapper/restructure needed.
* Why `aria-hidden="true"` — a purely decorative rule conveys no content; a screen reader should not
  announce it. (A `role="separator"` was considered but is semantically a "thematic break between
  content sections" and would be announced; `aria-hidden` is the quieter, correct choice for a
  decorative rule — matches AGENTS.md §6 "never colour alone / decorative elements hidden".)
* Not focusable — a plain `<li>` is not in the tab order, so keyboard traversal is unchanged.

CSS: `.rail-sep` is a 1px inset rule. "Inset" = aligned to the icon column (user decision), i.e.
horizontal margin matching the link's inner padding, not full-bleed.

```css
.rail-sep {
  height: 1px;
  margin: 8px 10px;
  background: var(--color-line);
}
```

`margin: 8px 10px` gives vertical breathing room and an inset that lines up with the link text
column (`.rail-links a` uses `padding:7px 10px`). No colour is introduced — `--color-line` is the
existing hairline token used by the rail's own `border-right`.

## 5. Data model / API

None. No schema, migration, endpoint, or serialized contract changes.

## 6. UI / UX

### 6.1 Resulting rail

```
┌──────────────────────────┐
│  ██  Amalthea            │
├──────────────────────────┤
│  ⊙  g  Dashboard         │
│  🔔 a  Alerts       (12) │
│  🗀 c  Cases         (4) │
│  ─────────────────────   │  ← .rail-sep (inset hairline)
│  ⌕  o  Observables       │
│  🤖 r  Automation        │
│  📖 p  Playbooks         │
│  ─────────────────────   │  ← .rail-sep
│  📡 s  Sources           │
│  🏷  t  Tags              │
│  📄 m  Case templates    │
│  🗺  x  Taxonomy          │
│  ⚙  ,  Settings          │
│          ├ Organisations │
│          ├ Users         │
│          └ Sources       │
│                          │
│  Acme Corp               │
│  Change password         │
│  Keyboard shortcuts  ?   │
│  Sign out alice          │
└──────────────────────────┘
```

### 6.2 Accessibility

* Separator: `aria-hidden="true"`, not focusable, not interactive.
* Separator is a shape (1px rule), not a colour-only signal — it does not rely on colour to convey
  grouping; and grouping is a convenience (order is already meaningful), so nothing is lost if the
  rule is not perceived.
* Existing `aria-current="page"` styling and the `rail-sub` nested list are untouched.
* Contrast is unaffected (no text/foreground introduced).

### 6.3 Keyboard

Unchanged. The `g`-then-key cheatsheet mapping stays: `g a c o r p s t m x ,` (Dashboard, Alerts,
Cases, Observables, Automation, Playbooks, Sources, Tags, Case templates, Taxonomy, Settings).

## 7. Phases & tasks

### Phase P1 — markup (`ui/templates/ui/base.html`)
- P1.1 Insert `<li class="rail-sep" aria-hidden="true"></li>` after the **Cases** `</li>` (end of
  group 1; before the Observables `<li>`).
- P1.2 Insert `<li class="rail-sep" aria-hidden="true"></li>` after the **Playbooks** `</li>` (end
  of group 2; before the Sources `<li>`).
- P1.3 Do **not** touch item order, labels, icons, badges, the `rail-sub` block, or `rail-foot`.

**AC-P1.1** — `base.html` renders exactly two `class="rail-sep"` rows, positioned after Cases and
after Playbooks, each with `aria-hidden="true"`.
**AC-P1.2** — The 11 existing rail items keep their exact order and labels; a diff shows only the
two inserted `<li>` rows inside `.rail-links`.

### Phase P2 — stylesheet (`ui/static/ui/app.src.css`, regenerate `app.css`)
- P2.1 Add `.rail-sep` rule as specified in §4, placed logically near the rail/`.rail-sub` block.
- P2.2 Run `make css` to regenerate `ui/static/ui/app.css`.

**AC-P2.1** — `.rail-sep` is a 1px `--color-line` rule with `margin:8px 10px`; `make css-check`
passes (artifact byte-matches source).
**AC-P2.2** — No new undefined CSS classes introduced (backlog stays 42).

### Phase P3 — tests (`tests/conformance/test_settings_ui.py`, or a new `test_ui_rail_grouping.py`)
- P3.1 Assert the rendered rail contains exactly **two** `rail-sep` rows for a logged-in user.
- P3.2 Assert both are `aria-hidden="true"` and contain no anchor/link (non-focusable).
- P3.3 Assert the group boundaries: the first `rail-sep` occurs after the Cases link and before the
  Observables link; the second after Playbooks and before Sources (parse the ordered item labels).
- P3.4 Falsifiability: with the separators removed (temporary sabotage), P3.1–P3.3 fail; restore and
  confirm `git diff` empty / hash match.

**AC-P3.1** — New tests pass against the implemented rail.
**AC-P3.2** — Falsifiability proof recorded in the verification report (§7.5 requirement): the
positive assertions fail when the guard (the separator rows) is removed.

## 8. Acceptance criteria (feature-level)

* **AC-F1** — Three groups are visually separated by two inset hairline rules; no caption text.
* **AC-F2** — Item order, labels, icons, shortcuts, badges and the Settings submenu are unchanged.
* **AC-F3** — Separators are non-focusable and `aria-hidden="true"`.
* **AC-F4** — Only `.rail-sep` added; `make css`/`css-check` green; undefined-class backlog = 42.
* **AC-F5** — `make check` green.
* **AC-F6** — No new menu items (Assets/Employment explicitly excluded).
* **AC-F7** — Tests + falsifiability proof present.

## 9. Security

No security surface. No user input, no queries, no auth/scoping change, no new endpoint.

## 10. Testing

* Added: rail-grouping conformance tests (P3) plus a falsifiability proof.
* Regression: existing `test_settings_ui.py` rail tests (submenu hidden/expanded, Settings link
  visibility by role) must still pass.
* Targeted run only (user directive — no full pytest suite without approval):
  `pytest tests/conformance/test_settings_ui.py tests/conformance/test_ui_rail_grouping.py`.
* `make check` (ruff check, ruff format --check, mypy, Django check, makemigrations --check, css-check).

## 11. Risks

* **R1 (Low)** — Separator vertical rhythm feels off vs. `gap:2px`. Mitigation: the 8px vertical
  margin is deliberately larger than the 2px gap; verify visually, adjust margin only if needed
  (record any change here).
* **R2 (Low)** — Breakpoint/width: none, rail is fixed 220px (N6).
* **R3 (Low)** — Over-styling risk: avoided by using the existing `--color-line` token only.
* **R4 (Low)** — Test brittleness if it asserts on full HTML; assert on ordered labels, not markup.

## 12. Open questions

None — all resolved by the user on 2026-10-21:

| # | Question | Decision |
|---|----------|----------|
| Q1 | Assets / Employment items? | No — not needed, do not add (N1) |
| Q2 | Tags duplicated in groups 2 & 3? | Keep **once**, in Configure (group 3) |
| Q3 | Option A (separators) vs B (captions)? | **A** — separators only |
| Q4 | Settings placement? | Last item inside Configure (unchanged) |
| Q5 | Keyboard shortcuts? | Unchanged |
| Q6 | Taxonomy icon? | Unchanged |
| Q7 | Scope? | Markup + CSS only |
| Q8 | Separator inset vs full-width? | **Inset** |
| Q9 | Narrow-screen collapse handling? | Not applicable (no collapse) |

## 13. Implementation record

**Implemented by:** django-frontend (2026-10-21)

**Files changed:**
- `ui/templates/ui/base.html` (+2 lines): inserted two `<li class="rail-sep" aria-hidden="true"></li>` rows — after Cases (line 59) and after Playbooks (line 78).
- `ui/static/ui/app.src.css` (+6 lines): added `.rail-sep` rule (height 1px, margin 8px 10px, background var(--color-line)) near the `.rail-sub` block.
- `ui/static/ui/app.css` (regenerated): artifact updated via `make css`; `css-check` passes.
- `tests/conformance/test_ui_rail_grouping.py` (new, 318 lines): six tests covering separator count, accessibility attributes, group boundary positions, and falsifiability proof.

**Deviations from plan:** None. All ACs met exactly as specified.

**Falsifiability proof result:** PASS. The test `test_falsifiability_separators_removed_assertions_fail` renders the real page, removes the two separator `<li>` elements from the HTML string, then verifies that:
1. The "exactly 2 separators" assertion fails (0 found).
2. The "separators have aria-hidden and no anchor" assertion is vacuously true but explicitly checks 0 separators exist.
3. The "group boundaries" assertion fails (0 separators found).
4. The template file is byte-identical before/after the test (SHA-256 match).

**Test summary:** 63 tests pass (57 existing `test_settings_ui.py` + 6 new `test_ui_rail_grouping.py`).
