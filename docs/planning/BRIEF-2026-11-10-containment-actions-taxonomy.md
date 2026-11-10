# Implementation Brief — Containment Actions Taxonomy

**Plan:** `docs/planning/PLAN-2026-11-10-containment-actions-taxonomy.md` (APPROVED 2026-11-10)
**Scope:** Sixth taxonomy category + seeded terms + taxonomy page card. **No case-level recording.**
**Status:** ready for `django-backend` and `django-frontend`.
**Worktree:** uncommitted unrelated changes exist. Do not commit, revert, or modify unrelated files.

## Decisions

- Category token: `containment_actions`.
- Four seeded terms, `protected=False`, `hidden=False`, `is_closing=False`:
  - `password_reset` / `Password reset` / order 0
  - `process_killed` / `Process Killed` / order 1
  - `asset_network_contained` / `Asset network contained` / order 2
  - `user_disabled` / `User disabled` / order 3
- `/api/v1/taxonomy` unchanged.
- UI reuses existing superAdmin-only taxonomy CRUD and modals.
- Do not run the full test suite; the user runs it. Focused tests only.

## Backend task — `django-backend`

### Files
- `core/models.py`
- `core/taxonomy_read.py`
- `ui/views/taxonomy.py`
- `core/migrations/0003_alter_taxonomyterm_category.py` (generated)
- `core/migrations/0004_seed_containment_actions.py` (new)
- Focused tests, likely `tests/conformance/test_ui_taxonomy_editing.py` and/or a new
  `tests/conformance/test_taxonomy_containment_actions.py`

### Work
1. Add `("containment_actions", "Containment Actions")` to `TaxonomyTerm.CATEGORY_CHOICES`.
2. Add `CATEGORY_CONTAINMENT_ACTIONS = "containment_actions"` to `core/taxonomy_read.py`, append to
   `ALL_CATEGORIES`, and update the “five compiled categories” docstring.
3. Generate the `AlterField` migration and add an idempotent data migration:
   - Forward `get_or_create(category="containment_actions", code=...)` with exact labels/order.
   - Never overwrite existing terms.
   - Reverse deletes only `category="containment_actions"` rows.
4. Update `ui/views/taxonomy.py`:
   - Add `containment_actions` to `taxonomy_terms_by_category`.
   - Set `in_use_count = 0` for the category for now.
   - Optional: expose `containment_action_choices` from `taxonomy_read.choices(...)` for future use;
     do not add a case field.
5. Add focused tests for:
   - model choice + `ALL_CATEGORIES` / `choices()` exposure;
   - seeded codes/labels/order and migration idempotency where practical;
   - page context includes hidden and visible containment terms;
   - superAdmin create/edit/delete through existing term endpoints + audit action;
   - invalid category behavior remains unchanged;
   - non-superAdmin mutation remains 404.

### Acceptance criteria
- AC-B1. `TaxonomyTerm.CATEGORY_CHOICES` includes `containment_actions` exactly once.
- AC-B2. `taxonomy_read.ALL_CATEGORIES` includes it; `choices()` orders by `order`, `code` and
  excludes hidden by default.
- AC-B3. Fresh DB has the four exact seeded terms; no duplicates on re-run; existing rows untouched.
- AC-B4. Reverse migration removes only containment-action terms.
- AC-B5. `makemigrations --check` is clean.
- AC-B6. `ui/views/taxonomy.py` context groups all containment terms and sets `in_use_count=0`.
- AC-B7. Existing taxonomy CRUD validates/audits the new category without new URLs.
- AC-B8. Focused tests pass; full suite left to user.

## Frontend task — `django-frontend`

### Files
- `ui/templates/ui/taxonomy.html`
- `ui/templates/ui/_taxonomy_term_form.html`
- Focused UI tests, likely new `tests/conformance/test_ui_containment_actions_taxonomy.py` or additions
  to `tests/conformance/test_ui_case_verdict.py` / `test_ui_p2_wiring.py`

### Work
1. Add a `Containment Actions` card **immediately after** the Verdict card.
2. Copy existing taxonomy-card markup, but omit the Closing column and `is_closing` controls.
3. Columns: Code, Label, Order, Hidden, Actions. `Code` cells use `class="mono"`.
4. Add-term button opens `taxonomy-term-create-containment_actions-modal`; row edit/delete use the
   existing generic term endpoints and modal pattern.
5. Empty state: `No terms defined.`
6. Disable delete when `protected` or `in_use_count`, same as other cards.
7. Add `containment_actions` to the category `<select>` in `_taxonomy_term_form.html`.
8. Keep `is_closing` visible only for `case_verdict`.
9. Add tests for:
   - card heading present and after Verdict in DOM order;
   - all four seeded codes/labels and mono cells render;
   - create form includes `containment_actions` and no `is_closing` for it;
   - superAdmin sees add/edit/delete controls; analyst/viewer do not;
   - edit modal still works for a containment term.

### Acceptance criteria
- AC-F1. `/taxonomy` renders `Containment Actions` after `Verdict`.
- AC-F2. Four seeded terms render with mono codes and labels.
- AC-F3. SuperAdmin sees add/edit/delete controls; non-superAdmin does not.
- AC-F4. Create form offers `containment_actions`; no `is_closing` field for that category.
- AC-F5. Existing markup/a11y conventions are preserved (`caption`, `aria-label`, `aria-hidden`).
- AC-F6. Focused UI tests pass; full suite left to user.

## Dependency / integration

- Frontend can start immediately; backend adds the category and seed.
- Backend must land model/read/context changes before UI tests can pass.
- Do not touch unrelated uncommitted files.
- If implementation deviates, record it in the plan before completion.

## Suggested focused checks

```bash
python -m pytest tests/conformance/test_ui_taxonomy_editing.py tests/conformance/test_ui_case_verdict.py -q
python manage.py makemigrations --check --dry-run
python manage.py check
```

Do not run the full suite.
