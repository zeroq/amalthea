# Implementation Brief — Containment Phase Action Capture

**Plan:** `docs/planning/PLAN-2026-11-10-containment-phase-capture.md`  
**Date:** 2026-11-10  
**Status:** Implemented — see plan deviations

## Goal

When a case enters the Containment phase, collect at least one containment action via checkboxes,
set `Case.phase="containment"` and `CaseStatus.value="Contained"`, persist the actions, and add a
Markdown case page showing the selected actions.

## Non-negotiable constraints

- Do not commit unless explicitly asked.
- Use `.venv/bin/python` / `.venv/bin/pytest`; system Python is Django 5.0.14.
- Do not run the full test suite; the user runs it.
- No secrets/tokens in code, fixtures, tests, or docs.
- Preserve dark theme, keyboard access, progressive enhancement, and CSP (no inline JS).
- Record any deviation from the plan in the plan doc.

## Confirmed product decisions

- Trigger both `Case.phase` and `CaseStatus`.
- Setting phase `containment` also sets status `Contained`.
- Existing `Page` model, listed in General-tab Pages card.
- At least one action required.
- Add seeded taxonomy action `other` / “Other” with required free text.
- Actions editable; re-entry pre-checks prior actions and creates another page.
- Pages show selected actions only.
- UI-only; no API contract change.
- Closed cases cannot enter containment (reopen first). Other phase changes do not alter status.

## Backend tasks

### B1 — Model + migrations

Files:
- `cases/models.py`
- `cases/migrations/0023_case_containment_actions.py` (next migration)
- `core/migrations/0005_seed_containment_action_other.py`
- `core/taxonomy_read.py`

Add:

```text
CaseContainmentAction
  id UUID PK
  case FK Case CASCADE related_name=containment_action_links
  term FK TaxonomyTerm PROTECT
  other_text CharField(max_length=500, blank=True)
  created_by FK User SET_NULL null=True blank=True
  created_at auto_now_add
  unique(case, term)
  case db_index=False (covered by unique left prefix)
  term uses the implicit FK index
```

- Add `Case.containment_actions = ManyToManyField(TaxonomyTerm, through=CaseContainmentAction)`.
- Seed taxonomy term `("containment_actions", "other", "Other", 4, false, false)`.
- Add `CONTAINMENT_ACTION_OTHER_CODE = "other"` in `core/taxonomy_read.py`.

**AC-B1:** migrations apply and reverse; four original terms remain; `other` seeded once;
unique constraint enforced.

### B2 — Deletion guard

File: `core/taxonomy.py`

- In `delete_taxonomy_term`, for `containment_actions`, reject when any
  `CaseContainmentAction.term_id == term.id`.
- Raise `TaxonomyError("Term is in use", "non_field")`; unreferenced terms still delete.

**AC-B2:** referenced term deletion is refused without a 500; unreferenced deletion works.

### B3 — Containment service

New file: `cases/containment.py`

Responsibilities:
- Resolve visible `Contained` CaseStatus:
  `CaseStatus.objects.filter(value="Contained", hidden=False, stage="InProgress").first()`.
  If missing → error, no writes.
- Load visible taxonomy terms for submitted codes from `containment_actions`.
- Reject unknown, hidden, foreign-category, and duplicate codes; dedupe before lookup.
- Require ≥1 action.
- Require trimmed `other_text` when `other` is selected; ignore text when not selected.
- Reject when `case.status.stage == "Closed"`.
- Apply `{"phase": "containment", "status": "Contained"}` through `apply_case_update`.
- Replace current `CaseContainmentAction` rows when action set changed.
- Create a `Page` when:
  - previous phase was not `containment`; or
  - the action selection/text changed while already in containment.
- Page:
  - category `containment`
  - title `Containment actions — YYYY-MM-DD HH:MM` on entry/re-entry,
    `Containment actions updated — YYYY-MM-DD HH:MM` on same-phase change
  - content: case is in Containment phase; selected labels only; `Other: <text>`; timestamp
  - `created_by=actor`
  - `order = max(existing order) + 1`
- Emit timeline event `kind="containment-actions"` and audit action `case.containment_actions`
  with codes, other text, page id. Do not emit containment events when nothing changed.
- Publish `page` on commit using `publish_case_event` + `page_json`.

**AC-B3:** all writes are atomic; invalid input writes nothing; repeated identical save creates no
duplicate page; re-entry copies prior action selection and creates a new page.

### B4 — UI handler

File: `ui/views/cases.py`

- Extend `case_field_edit`.
- If `field == "phase"` and posted `value == "containment"`:
  - read `request.POST.getlist("containment_actions")` and `other_text` (or `other`);
  - call the containment service inside one transaction;
  - on error: flash message + redirect, no writes;
  - on success: success message + redirect to `?tab=general`.
- All other fields keep current behavior.

**AC-B4:** viewer 404, cross-org 404, closed case rejected, no partial writes on errors.

### B5 — Case-detail context

File: `ui/views/case_detail.py`

Pass:
- visible containment action choices;
- current selected codes;
- current `other_text`.

Do not change the generic field-edit descriptors for other fields.

**AC-B5:** existing phase choice + other inline fields still render as before.

## Frontend tasks

### F1 — Dedicated phase modal

Files:
- `ui/templates/ui/_tab_general.html`
- new `ui/templates/ui/_case_phase_form.html`
- new `ui/static/ui/case_phase.js`
- `ui/templates/ui/case_detail.html` (load JS)

- Keep the dialog id `case-field-phase`.
- Replace only the phase modal body with the dedicated form.
- Include:
  - phase select named `value`;
  - fieldset “Containment actions” with one checkbox per visible term, `name="containment_actions"`;
  - checked state from current selections;
  - `Other` checkbox + text input named `other_text`;
  - note: at least one action required;
  - warning: setting Containment also sets case status to Contained.
- JS toggles the fieldset when phase is `containment`, and the Other text input when Other is
  checked.
- No-JS fallback: fieldset remains visible; server validates.

**AC-F1:** keyboard-only flow works; `?`/Esc/backdrop modal behavior unchanged; no inline script.

### F2 — Display

- The created Page appears in the existing Pages card with no template changes.
- Verify the page title/category/content render through the existing Markdown pipeline.

**AC-F2:** page is visible on General tab and shows only selected actions.

## Tests

New focused suite: `tests/conformance/test_containment_phase_capture.py`

Cover:
1. migration seed `other`;
2. unique/PROTECT/other text;
3. phase modal renders checkboxes;
4. no action / other without text rejected;
5. hidden/foreign/duplicate codes rejected/de-duplicated;
6. valid transition sets phase + status + relation + Page + timeline + audit;
7. missing `Contained` and closed case write nothing;
8. non-containment phase creates no page/relation;
9. editable actions create a new page only when changed;
10. re-entry pre-checks previous actions and creates another page;
11. page shows selected labels only;
12. taxonomy deletion guard;
13. viewer/cross-org 404.

Update:
- `tests/conformance/test_taxonomy_containment_actions.py` for the fifth `other` term.
- `tests/conformance/test_ui_case_verdict.py` containment phase tests to supply actions.

Suggested focused commands:

```bash
.venv/bin/pytest tests/conformance/test_containment_phase_capture.py \
  tests/conformance/test_taxonomy_containment_actions.py \
  tests/conformance/test_ui_case_verdict.py -q
```

## Docs

- Update `docs/spec/data-model.md`: relation, `other`, page category.
- Add deviations to the plan doc.
