# PLAN — Case template "Flag as important" label and checkbox alignment

**Date:** 2026-11-12
**Status:** Approved — decisions on Open Questions recorded below; implementing
**Area:** UI (Django templates) · Case templates
**Related:** `ui/templates/ui/case_template_form.html`, `ui/templates/ui/case_template_detail.html`,
`ui/templates/ui/case_template_list.html`, `ui/static/ui/app.src.css` (`.check`, `.field-row`)

---

## 1. Summary

The case-template create/edit form renders its boolean default as a bare label
`Flag` next to a checkbox that is not vertically aligned with its text:

```html
<div class="meta-inline">
  <label for="id_flag">Flag</label>
  <input type="checkbox" id="id_flag" name="flag" {% if template.flag %}checked{% endif %}>
</div>
```

`flag` is the same "Flag as important" attribute a `Case` carries. The template form is the only
place that calls it just "Flag". The requirement is to (a) label it **"Flag as important"** and
(b) put the checkbox and its text on one row, vertically centred — matching the established
`.check` pattern already used for the same field on the case create/edit forms.

No behaviour changes: `flag` remains a boolean default copied onto cases by `_apply_template`
(`case.flag = template.flag`).

## 2. Goals

- G1: Template create/edit form shows **"Flag as important"**, not "Flag".
- G2: The checkbox sits on the same line as its text, vertically centred.
- G3: Reuse the existing design-system pattern (`.check` / `.field-row`) — no bespoke CSS unless
  unavoidable.
- G4: Preserve `id="id_flag"` and `name="flag"` so POST parsing, tests, and any JS keep working.
- G5: Add a regression test that pins the label text and the one-row checkbox markup.

## 3. Non-goals

- No change to `flag` semantics, serializers, API, query engine, or template application.
- No new icon (e.g. a flag glyph) or badge on the case list/detail.
- No visual redesign of the template form beyond this one row.
- Not touching the alert/task "flag" controls (already correct).

## 4. Current state / root cause

- `case_template_form.html:42-45` uses `.meta-inline` (a `display:flex` row) with a standalone
  `<label>` and a sibling checkbox. The global `label` rule sets `display:block; margin:12px 0 5px`,
  and `.meta-inline` does not set `align-items:center`, so the label text and the checkbox do not
  share a baseline/centre line.
- The canonical checkbox pattern elsewhere (`_case_create_fields.html:126`, `_case_edit_fields.html:126`,
  `_task_form_modal_body.html:122`) wraps the checkbox **inside** `<label class="check">`, and
  `.check { color:var(--color-text); align-items:center; gap:7px; margin:12px 0; display:flex }`
  guarantees the same-row, vertically-centred layout. This is already compiled in
  `ui/static/ui/app.css`; no CSS regeneration is needed.
- Secondary observation (needs decision, see Q1/Q2): `case_template_detail.html` has both
  `<dt>Flag</dt>` (line 32) **and** `<dt>Flags</dt>` (line 44), both rendering the same
  `template.flag`; and the list header is `Flags`.

## 5. Proposed implementation

### Task T1 — Form row (`ui/templates/ui/case_template_form.html`)
Replace the `.meta-inline` block with the canonical check row:

```html
<div class="field-row">
  <label class="check" for="id_flag">
    <input type="checkbox" id="id_flag" name="flag" {% if template.flag %}checked{% endif %}>
    Flag as important
  </label>
</div>
```

**Acceptance criteria**
- AC1: Rendered form contains the literal text `Flag as important` (and the standalone `>Flag<`
  label is gone).
- AC2: The checkbox `<input ... id="id_flag" name="flag">` is a child of a `label.check`
  (one flex row, `align-items:center`).
- AC3: `id="id_flag"` and `name="flag"` unchanged; create with checked → `template.flag is True`,
  unchecked → `False` (existing tests keep passing).
- AC4: No CSS change required; `make css-check` still passes.

### Task T2 — Detail page (`ui/templates/ui/case_template_detail.html`)  *(pending Q1)*
- Rename `<dt>Flag</dt>` → `<dt>Flag as important</dt>`.
- Remove the duplicate `<dt>Flags</dt>` row (same value, rendered twice).
**AC**
- AC5: Detail page shows "Flag as important" exactly once; no "Flags" row.

### Task T3 — List column (`ui/templates/ui/case_template_list.html`)  *(pending Q2)*
- Either keep header `Flags` or rename per Q2. Rendering of the value (True/—) unchanged unless
  Q3 opts into Yes/No.
**AC**
- AC6: Header text matches the chosen option; column still shows the boolean.

### Task T4 — Regression test (`tests/conformance/test_case_template_defaults.py`)
Add a test (near the existing `test_ui_create_form_*` cases) that logs in, GETs
`ui-case-template-create`, and asserts:
- `"Flag as important"` is present,
- the checkbox is inside a `label` carrying `check` (e.g. via a small HTML parse of the form),
- `id="id_flag"` present.
A second assertion covers the edit page (`ui-case-template-edit`) showing the same label.
**AC**
- AC7: New test passes; `test_case_template_defaults.py` suite green.

## 6. Testing / verification

- `pytest tests/conformance/test_case_template_defaults.py -q`
- `pytest tests/conformance/test_ui_p1_wiring.py -q` (template create/edit wiring)
- `make check` (ruff, format, mypy, django check, migrations, css-check, playbook scripts)
- Manual: create template with the box checked/unchecked; confirm a case seeded from it gets
  `flag=True/False`.

## 7. Risks

- Low. Template-only markup change. The only risk is a stray test asserting `"Flag"` verbatim;
  grep shows no test currently pins the template label or `id_flag`, so the surface is clear.
- Using `label.check` relies on the existing compiled `.check` rule; confirmed present in
  `app.src.css`/`app.css`.

## 8. Open Questions — RESOLVED (2026-11-12)

1. **Detail page:** relabel `<dt>Flag</dt>` → "Flag as important" and delete the duplicated
   `<dt>Flags</dt>` row. **Decision: YES.**
2. **List column header:** rename to **"Important"**. **Decision: "Important".**
3. **Value rendering on detail/list:** keep `True` / `—` as-is. **Decision: leave as-is.**
