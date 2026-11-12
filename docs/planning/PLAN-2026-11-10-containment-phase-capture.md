# Plan — Containment Phase Action Capture

**Date:** 2026-11-10  
**Status:** Decisions applied — ready for implementation brief  
**Related:** `PLAN-2026-11-10-containment-actions-taxonomy.md`

## Decisions

- Trigger on **both** axes:
  - `Case.phase` → `containment`
  - `CaseStatus.value` → `Contained`
- The case page is an existing Markdown **Page** shown in the General-tab Pages card.
- At least one containment action is required.
- Add an **Other** taxonomy action with a required free-text detail.
- Actions are editable; re-entering containment creates another page and pre-checks the
  previously recorded actions.
- UI-only for now. No API contract change.
- Pages show only selected actions.

## CaseStatus finding

`Contained` is a seeded `CaseStatus` at stage `InProgress`. It **is** settable through the case
Edit modal and the API, but the General-tab stage control only offers `New`, `InProgress`, and
`Closed` (`_case_status_for_stage` maps a stage to its first status). The containment flow will
therefore set `status="Contained"` programmatically in the same update as `phase="containment"`.

Safety guard: entering containment on a **Closed** case is rejected; reopen the case first.
Changing phase away from `containment` does not change the status back.

## Data model

Add `CaseContainmentAction` in `cases/models.py`:

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDModel` |
| `case` | FK `Case`, CASCADE | `related_name="containment_action_links"` |
| `term` | FK `TaxonomyTerm`, PROTECT | category `containment_actions` |
| `other_text` | CharField(500), blank | required only when `term.code == "other"` |
| `created_by` | FK `User`, SET_NULL, nullable | recording analyst |
| `created_at` | DateTime | auto |

- Unique `(case, term)` (also the left prefix for case lookups); the `term` FK keeps Django's implicit index for the deletion guard. `case` uses `db_index=False` to avoid a redundant index.
- `Case.containment_actions = ManyToManyField(TaxonomyTerm, through=CaseContainmentAction)`.
- The relation stores the current set; each Page is the historical snapshot.

Add a seeded taxonomy term `other` / “Other” (next unused order) via a new `core` migration.
Store the constant `CONTAINMENT_ACTION_OTHER_CODE = "other"` in `core/taxonomy_read.py`.

## Behavior

1. Analyst opens the Phase dialog, selects `Containment`.
2. Checkbox list appears from visible `containment_actions` terms.
3. At least one checkbox is required. If `other` is checked, a non-empty free-text field is
   required.
4. On save, one atomic operation:
   - validates actions;
   - applies `phase="containment"` and `status="Contained"`;
   - replaces the case’s current containment-action relation;
   - creates a new Page when entering containment or when the action set changed;
   - emits timeline/audit entries and the existing `page` WebSocket event.
5. Existing case pages are never mutated or deleted by this flow.
6. Re-entering containment pre-checks the previously recorded actions and creates another page.
7. Editing actions while already in containment updates the relation and creates a new
   “updated” page if the selection changed.
8. Non-containment phase changes ignore containment-action fields and create no page.

## Page snapshot

- `category`: `containment`
- Title:
  - entry/re-entry: `Containment actions — YYYY-MM-DD HH:MM`
  - same-phase edit: `Containment actions updated — YYYY-MM-DD HH:MM`
- Content:
  - statement that the case entered / remains in the Containment phase;
  - only the selected action labels;
  - `Other: <text>` when selected;
  - recording timestamp.
- `created_by` = actor; `order` = max existing + 1.
- Publish `page` on commit.

## Validation

- Action codes must exist, be visible, and belong to `containment_actions`.
- Duplicate codes de-duplicated.
- Unknown/foreign-category/hidden codes reject the whole operation.
- `other` requires non-empty trimmed text; text is ignored when `other` is unchecked.
- Closed cases cannot enter containment.
- `Contained` must exist and be non-hidden; otherwise show an error and write nothing.

## UI

- Replace the generic phase dialog body with `_case_phase_form.html`:
  - phase select;
  - containment checkbox fieldset, shown when `containment` is selected;
  - `Other` checkbox plus free-text input;
  - helper text: at least one action required;
  - warning: “This also sets the case status to Contained.”
- `ui/static/ui/case_phase.js` toggles visibility; no inline script (CSP/ADR-003).
- Action rows are compact flex rows (`[ ] Label`); only the checkbox is the control target,
  while the explicit `<label for>` keeps the accessible name and native text-click behavior.
- No-JS fallback: fieldset is visible and server-side validation remains authoritative.
- Pre-check existing actions and Other text when already in containment.
- Invalid input: no writes; show a flash error (HTMX inline errors optional).
- Success: redirect to `?tab=general`; page appears in Pages card.
- Viewer: no opener; endpoint remains 404 for viewers/cross-org.

## Timeline + audit

- `apply_case_update` emits the existing `status-changed` and `classification-changed` rows.
- Add one `containment-actions` timeline event with action codes, `other` text, and page id.
- Add audit action `case.containment_actions` with codes/page id; no secrets.
- Do not duplicate events when the selection did not change.

## Taxonomy deletion

Extend `delete_taxonomy_term`:
- `containment_actions` term in use by `CaseContainmentAction` → `TaxonomyError("Term is in use")`.
- Unreferenced terms remain deletable.
- `other` is an ordinary taxonomy term, not protected by default.
- The taxonomy page's `in_use_count` includes containment links, so the UI disables delete
  instead of offering a button the endpoint would reject.

## API

UI-only. `PATCH /api/v1/case/{id}` keeps current behavior.

## Testing

1. Migration seeds `other`; existing taxonomy tests updated.
2. Model unique/PROTECT/other-text behavior.
3. Phase dialog renders visible actions and pre-checks existing ones.
4. No action / other-without-text / hidden / foreign code / duplicate behavior.
5. Successful transition writes phase + `Contained` status + relation + Page + timeline/audit.
6. `Contained` missing or closed case → no writes.
7. Non-containment phase creates no page/relation.
8. Editable actions create a new page only when changed.
9. Re-entry copies previous selections and creates another page.
10. Page lists only selected labels and Other text.
11. Referenced taxonomy term deletion blocked; unreferenced allowed.
12. Viewer/cross-org 404.
13. Update `tests/conformance/test_ui_case_verdict.py` containment phase tests.
14. User runs full suite; focused checks only when asked.

## Deviations

- Generated migration is `cases/migrations/0023_case_containment_actions.py` (Django's generated
  slug) rather than the provisional `0023_casecontainmentaction.py`.
- `cases/containment.py` owns the transaction; the view does not wrap it in a second atomic block.
- A repeated identical save while still in containment creates no Page, timeline event, or
  new audit row. Re-entry after leaving containment creates a new Page as specified.
- The page body says “The case is in the Containment phase.” so the same template is accurate for
  both entry and same-phase edits.
- The `other` seed migration reverses with `RunPython.noop`: a reverse must not delete a
  user-editable taxonomy term that may already be referenced by a case.
- `CaseContainmentAction.case` is `db_index=False` (covered by the unique `(case, term)`
  index) and the explicit `term` index was dropped in favour of Django's implicit FK index;
  this satisfies the repository's index-conformance guards.

## Files

- `cases/models.py`, `cases/migrations/0023_case_containment_actions.py`
- `core/migrations/0005_seed_containment_action_other.py`
- `core/taxonomy_read.py`, `core/taxonomy.py`
- `cases/containment.py` (new helper)
- `cases/update.py` (only if required for atomic integration)
- `ui/views/cases.py`, `ui/views/case_detail.py`
- `ui/templates/ui/_tab_general.html`, `ui/templates/ui/_case_phase_form.html`
- `ui/static/ui/case_phase.js`
- `docs/spec/data-model.md`
- `tests/conformance/test_containment_phase_capture.py`
- `tests/conformance/test_taxonomy_containment_actions.py`
- `tests/conformance/test_ui_case_verdict.py`
