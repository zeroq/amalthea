# BRIEF — 2026-10-21 — Primary rail visual grouping

**For:** `django-frontend`
**Plan:** `docs/planning/PLAN-2026-10-21-rail-grouping.md` (APPROVED)
**Verifier:** `verifier`
**Size:** XS — one template, one CSS rule, tests. No backend, no schema, no routes.

---

## Ground truth (verified 2026-10-21)

* Rail markup: `ui/templates/ui/base.html`, single `<ul class="rail-links">` at line **38**.
* Item order today (11): Dashboard, Alerts, Cases, Observables, Automation, Playbooks, Sources,
  Tags, Case templates, Taxonomy, Settings (Settings carries a nested `.rail-sub`).
* `.rail-links` CSS (in `ui/static/ui/app.src.css`): `display:grid; gap:2px`. Link padding is
  `7px 10px`.
* Stylesheet rule: `ui/static/ui/app.src.css` is the **only** hand-edited stylesheet;
  `ui/static/ui/app.css` is a generated artifact — never edit it directly; run `make css`.
* Existing rail tests: `tests/conformance/test_settings_ui.py` (submenu expand/collapse, Settings
  link by role). These must keep passing.
* `make check` = ruff check, ruff format --check, mypy, `manage.py check`,
  `makemigrations --check --dry-run`, `css-check`.

## Hard constraints

* **Do NOT add "Assets" or "Employment"** items — explicitly rejected by the user.
* Do **not** reorder, rename, re-icon, re-route, or change shortcuts for any existing item.
* Do **not** add caption text (Option A only).
* Insets (not full-bleed) separators.
* No new classes other than `.rail-sep` (undefined-class backlog must stay **42**).
* Keep the `rail-sub` and `rail-foot` blocks untouched.
* No `python` on PATH — use `.venv/bin/python`; ruff at `.venv/bin/ruff`.

## Tasks

### P1 — `ui/templates/ui/base.html`
1. After the **Cases** `<li>` (closes group 1, before the Observables `<li>`), insert:
   `<li class="rail-sep" aria-hidden="true"></li>`
2. After the **Playbooks** `<li>` (closes group 2, before the Sources `<li>`), insert the same row.
3. Leave everything else byte-identical.

### P2 — `ui/static/ui/app.src.css`
Add, near the rail / `.rail-sub` block:

```css
.rail-sep {
  height: 1px;
  margin: 8px 10px;
  background: var(--color-line);
}
```

Then run `make css` to regenerate `app.css`.

### P3 — tests
Create `tests/conformance/test_ui_rail_grouping.py` (mirror the helper style in
`tests/conformance/test_settings_ui.py`: `_mk_org`, `_mk_user`, `_mk_client`). Assert:
1. Exactly **2** `rail-sep` rows render for a logged-in user.
2. Each is `aria-hidden="true"` and contains **no** `<a>` (non-focusable).
3. Group boundaries by ordered labels: sep #1 after Cases / before Observables; sep #2 after
   Playbooks / before Sources. Parse ordered item labels, do **not** match raw HTML blobs.
4. **Falsifiability (§7.5):** temporarily delete the two separator rows, confirm the new positive
   assertions **fail**, then restore and confirm `git diff` is empty (hash match). Record the proof
   in your report.

## Acceptance criteria (must all hold)

* AC-F1 three groups separated by two inset hairline rules; no captions.
* AC-F2 order/labels/icons/shortcuts/badges/submenu unchanged (diff shows only the 2 `<li>` + CSS).
* AC-F3 separators non-focusable + `aria-hidden="true"`.
* AC-F4 only `.rail-sep` added; `make css`/`css-check` green; undefined-class backlog = 42.
* AC-F5 `make check` green.
* AC-F6 no new menu items.
* AC-F7 tests + falsifiability proof present.

## Verification commands

```
make css && make check
.venv/bin/python -m pytest tests/conformance/test_settings_ui.py tests/conformance/test_ui_rail_grouping.py
.venv/bin/ruff format ui/templates/ui/base.html ui/static/ui/app.src.css tests/conformance/test_ui_rail_grouping.py
```

(No full suite without user approval.)

## Deliverable back to planner

* Files changed (paths + line counts).
* `make check` output tail (must be green).
* Targeted pytest summary.
* Falsifiability proof result (before/after).
* Any deviation — record it in the plan §13 Implementation record; do not silently diverge.
