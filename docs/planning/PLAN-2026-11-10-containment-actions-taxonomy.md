# Plan — Containment Actions Taxonomy

**Status:** APPROVED — recommendations accepted 2026-11-10; ready for implementation.
**Date:** 2026-11-10
**Owner:** planner
**Scope:** Add a sixth editable taxonomy vocabulary, **Containment Actions**, with four seeded terms and a taxonomy page card. Case-level recording of actions is a **future phase**, not implemented here unless explicitly approved.

---

## 1. Summary

Amalthea already has an editable, global `TaxonomyTerm` vocabulary for five compiled categories
(`case_scope`, `case_phase`, `case_type`, `data_classification`, `case_verdict`). This plan adds
`containment_actions` as a sixth category, seeds four default terms, exposes them in the existing
taxonomy page using the same add/edit/delete controls, and keeps the UI consistent with Verdict.

The immediate goal is vocabulary management only. The longer-term workflow — selecting containment
actions when a case is moved to the `Contained` status — needs a separate case-to-action relation,
API/UI contract, validation rules, and audit/timeline design. That is documented here as a future
phase so the taxonomy shape does not block it.

## 2. Goals

- Add `containment_actions` to `TaxonomyTerm.CATEGORY_CHOICES` and the runtime taxonomy constants.
- Seed four default terms:
  - `password_reset` — Password reset
  - `process_killed` — Process Killed
  - `asset_network_contained` — Asset network contained
  - `user_disabled` — User disabled
- Render a **Containment Actions** card directly below the **Verdict** card on `/taxonomy`.
- Reuse the existing taxonomy term create/edit/delete modals, permissions, validation, and audit path.
- Keep terms editable/deletable through the same superAdmin-only controls, unless product decides the
  seeded terms should be protected.
- Add regression tests for seeding, CRUD, category exposure, and page rendering.
- Document the new category and the deferred case-link design.

## 3. Non-goals

- No case-level containment-action field, relation, API, or UI in this implementation.
- No change to `/api/v1/taxonomy` unless product explicitly wants taxonomy terms exposed there.
- No automated containment execution (firewall/AD/EDR actions).
- No org-scoped taxonomy; `TaxonomyTerm` remains global, matching the existing model.
- No replacement of the existing `Contained` case status or `containment` case phase.

## 4. Current-state findings

- `TaxonomyTerm` is defined in `core/models.py`, global (`not org-scoped`), with:
  - `category`, `code`, `label`, `order`, `hidden`, `protected`, `is_closing`.
  - Unique constraint on `(category, code)`.
  - `CATEGORY_CHOICES` currently has five categories.
- Initial terms are seeded by `core/migrations/0002_populate_taxonomy_terms.py`.
- `core/taxonomy_read.py` documents five compiled categories as the runtime source of truth:
  `ALL_CATEGORIES` and category constants must include the new category.
- `core/taxonomy.py` already validates `category in TaxonomyTerm.CATEGORY_CHOICES`, so a new choice
  automatically becomes valid for create/update/delete.
- `ui/views/taxonomy.py` groups terms by category and calculates `in_use_count`.
- `ui/templates/ui/taxonomy.html` renders one card per compiled vocabulary. The Verdict card is the
  direct placement and markup reference.
- `ui/templates/ui/_taxonomy_term_form.html` contains a hard-coded category `<select>` and
  Verdict-only `is_closing` controls; both need awareness of the new category.
- `ui/taxonomy_views.py` and `ui/taxonomy_forms.py` already provide generic term CRUD for any
  valid category. No new URL is needed.
- Only `superAdmin` may mutate taxonomy; non-superadmins see the page with management controls hidden.
- Existing `CaseStatus` seed includes `Contained` with stage `InProgress`. Therefore “contained
  status” means the **status value `Contained`**, not the `Closed` stage or the `containment` phase.
- The taxonomy page intentionally shows hidden terms so managers can edit them; hidden terms are
  excluded from pickers by `taxonomy_read.choices()`.

## 5. Proposed data model

Use the existing `TaxonomyTerm`; no new table for this phase.

```text
TaxonomyTerm
  category = "containment_actions"
  code     = stable machine token
  label    = analyst-facing prose
  order    = display order
  hidden   = false by default
  protected = false (proposed; see Q3)
  is_closing = false (verdict-only)
```

Seeded defaults:

| code | label | order |
|---|---|---:|
| `password_reset` | Password reset | 0 |
| `process_killed` | Process Killed | 1 |
| `asset_network_contained` | Asset network contained | 2 |
| `user_disabled` | User disabled | 3 |

Rationale for `category = "containment_actions"`:
- Matches the existing plural snake_case convention.
- Reads clearly in `TaxonomyTerm` code (`containment_actions:password_reset`).
- Leaves the future case relation free to name itself `CaseContainmentAction` without colliding.

Migration approach:
1. `core/migrations/0003_alter_taxonomyterm_category.py` — generated `AlterField` for the new choice.
2. `core/migrations/0004_seed_containment_actions.py` — data migration using `get_or_create`
   keyed on `(category, code)`.
   - Forward: create only the four defaults; do not overwrite existing rows.
   - Reverse: delete only `category="containment_actions"` rows; do not repeat the old
     “delete all taxonomy terms” pattern.
   - Migration must be idempotent and safe for databases where a manager already created one of
     these codes.
3. Verify `makemigrations --check` is clean.

No database check constraint is added for category; `TaxonomyTerm` uses `choices` and service
validation, consistent with existing categories.

## 6. API and read model

### Implemented now

- Extend `core/taxonomy_read.py`:
  - `CATEGORY_CONTAINMENT_ACTIONS = "containment_actions"`
  - add it to `ALL_CATEGORIES`
  - update module docstring from “five” to “six”
- No new REST endpoint.
- No change to `/api/v1/taxonomy` unless Q5 says otherwise. That endpoint currently aggregates
  observable types, statuses, templates, and tags, not `TaxonomyTerm` categories.
- Future case API will need a `containmentActions` field; contract is defined in §11, not built now.

## 7. UI / UX

### Taxonomy page

Add a new `<section class="card" aria-labelledby="h-containment-actions">` immediately after the
Verdict card.

- Heading: **Containment Actions**
- Caption: “Containment actions” / “Case containment actions” (screen-reader caption).
- Table columns: Code, Label, Order, Hidden, Actions.
- `Code` cells use `class="mono"`, matching all other code-valued taxonomy cells.
- Add term button opens the existing create modal with `category="containment_actions"`.
- Each row gets the existing edit and delete icon controls.
- Delete is disabled with `title` when `protected` or `in_use_count`; for now usage is `0`.
- Empty state: “No terms defined.”
- Modal titles: “New containment action” / “Edit taxonomy term”.
- No new keyboard shortcut required; `?` cheat-sheet remains unchanged unless the page gains a
  primary action that is not already covered.

### Term form

- Add `containment_actions` to the category `<select>` in
  `ui/templates/ui/_taxonomy_term_form.html`.
- Keep `is_closing` visible only for `case_verdict`.
- No `is_closing` field for containment actions.
- Code remains create-only; label/order/hidden are editable.
- Preserve HTMX + progressive-enhancement behavior and 422 error re-render.

### Permissions

- Reuse `can_manage_taxonomy` (`role == "superAdmin"`).
- Non-superadmins see the card and its terms, but no add/edit/delete controls.
- Viewer role remains read-only.
- No CSRF bypass or new auth path.

## 8. Implementation tasks and acceptance criteria

### Task 1 — Model choice and runtime constant

Files:
- `core/models.py`
- `core/taxonomy_read.py`

Work:
- Add `("containment_actions", "Containment Actions")` to `CATEGORY_CHOICES`.
- Add `CATEGORY_CONTAINMENT_ACTIONS` and append to `ALL_CATEGORIES`.
- Update the taxonomy-read docstring.

Acceptance criteria:
- AC1. `TaxonomyTerm.CATEGORY_CHOICES` contains exactly one `containment_actions` entry.
- AC2. `taxonomy_read.ALL_CATEGORIES` includes `containment_actions` and remains ordered after the
  existing categories.
- AC3. `taxonomy_read.choices("containment_actions")` returns seeded terms ordered by `order`,
  then `code`, and excludes hidden terms.
- AC4. Existing taxonomy read behavior and import-time safety are unchanged.

### Task 2 — Migrations and seed data

Files:
- `core/migrations/0003_alter_taxonomyterm_category.py`
- `core/migrations/0004_seed_containment_actions.py`

Work:
- Generate the `AlterField` migration.
- Add the data migration described in §5.
- Reverse deletes only containment-action terms.

Acceptance criteria:
- AC5. Fresh migration creates the four terms with the exact codes, labels, and order above.
- AC6. Migration is idempotent: re-running forward does not duplicate or overwrite existing rows.
- AC7. Reverse removes only `category="containment_actions"` terms.
- AC8. `python manage.py makemigrations --check` reports no missing migrations.

### Task 3 — Taxonomy page context

Files:
- `ui/views/taxonomy.py`

Work:
- Add `containment_actions` to `taxonomy_terms_by_category`.
- Update usage-count handling so the category has a defined `in_use_count` (initially `0`).
- Optionally expose `containment_action_choices` for future pickers; do not add a case field yet.

Acceptance criteria:
- AC9. The taxonomy page context contains all terms for `containment_actions`, including hidden ones.
- AC10. Seeded terms have `in_use_count == 0` until a case relation exists.
- AC11. No query regression: page still renders with seeded and user-created terms.

### Task 4 — Taxonomy card and form option

Files:
- `ui/templates/ui/taxonomy.html`
- `ui/templates/ui/_taxonomy_term_form.html`

Work:
- Add the card immediately below Verdict.
- Copy the Verdict card’s add/edit/delete/modal patterns, omitting the Closing column and control.
- Add the category option.
- Use “Containment Actions” heading and “Containment action” accessible labels.

Acceptance criteria:
- AC12. `/taxonomy` renders a “Containment Actions” heading after the Verdict card in DOM order.
- AC13. The card lists all four seeded terms with mono code cells and labels.
- AC14. SuperAdmin sees Add, Edit, and Delete controls; non-superadmin and viewer do not.
- AC15. Creating, editing, and deleting a containment-action term works through the existing modal
  endpoints and emits the existing `taxonomy.term.*` audit actions.
- AC16. The create form offers `containment_actions` as a category and never offers `is_closing`.
- AC17. Editing a seeded term can change label/order/hidden but cannot change code or category.
- AC18. HTMX error rendering still returns 422 with field errors; non-HTMX falls back to a full page.

### Task 5 — Tests

Files:
- `tests/conformance/test_ui_case_verdict.py` or a new focused containment test module.
- `tests/conformance/test_ui_taxonomy_editing.py`
- `tests/conformance/test_ui_p2_wiring.py` if category-card coverage is asserted there.
- Taxonomy read/model tests as appropriate.

Work:
- Add focused regression tests for seed, read, UI, permissions, CRUD, and card placement.
- Avoid broad full-suite changes unless needed.
- Keep tests deterministic and independent of migration order.

Acceptance criteria:
- AC19. A test proves the four defaults exist after migrations.
- AC20. A test proves `ALL_CATEGORIES` and `choices()` expose the category.
- AC21. A test proves the card renders with seeded terms and proper mono cells.
- AC22. A test proves superAdmin-only mutation behavior for containment-action terms.
- AC23. A test proves create/edit/delete succeeds and writes the expected audit action.
- AC24. Focused tests pass; user runs the full suite.

### Task 6 — Documentation

Files:
- `docs/spec/data-model.md`
- `docs/spec/api.md` only if API behavior changes
- `TODO.md` if this closes a tracked item
- This plan

Work:
- Document the sixth category, seed terms, and the deferred case-link phase.
- Record deviations if implementation differs from this plan.
- Do not claim case-level containment recording is implemented.

Acceptance criteria:
- AC25. Data-model docs list `containment_actions` with codes and labels.
- AC26. Docs clearly mark case-level containment-action recording as future work.
- AC27. Any deviation is recorded in this plan before handoff completion.

## 9. Security

- Reuse existing superAdmin-only UI mutations; no new authorization surface.
- Validate category server-side through `TaxonomyTerm.CATEGORY_CHOICES` and `core.taxonomy`.
- Keep CSRF protection and login requirements on all mutation endpoints.
- Continue Django template auto-escaping; no `safe` rendering of labels/codes.
- No secrets, tokens, org identifiers, or customer data in migrations, tests, or docs.
- No execution of containment actions in this phase; taxonomy terms are inert labels.
- Migration reverse must not delete non-containment taxonomy terms.

## 10. Testing strategy

- Focused model/migration tests for seeded rows and idempotency.
- Read-layer tests for `ALL_CATEGORIES`, `choices()`, hidden filtering, and ordering.
- UI wiring tests for card presence, card order, code cells, and form option.
- Permission tests for superAdmin vs analyst vs viewer.
- CRUD/audit tests using existing patterns in `test_ui_taxonomy_editing.py`.
- Regression test for the taxonomy page when Observable Types is non-empty.
- User runs the full suite; planner/verifier only run focused checks when asked.

## 11. Future phase — recording actions on a case (not in this handoff)

When approved later:

1. Add a relation, e.g. `CaseContainmentAction`:
   - `case` FK, `term` FK to `TaxonomyTerm`, `created_at`, `created_by`.
   - Unique `(case, term)`.
   - `on_delete=PROTECT` for the term once referenced, or store an immutable code/label snapshot.
2. Add `containmentActions` to the case transition/update contract.
3. Show a multi-select in the “Contained” status transition modal.
4. Validate:
   - only supplied when moving to the `Contained` status (or editing an already-contained case);
   - terms must exist, be unhidden, and belong to `containment_actions`;
   - duplicate selections are rejected or de-duplicated explicitly.
5. Write an audit event and a case timeline entry such as
   `Containment actions recorded: Password reset, User disabled`.
6. Extend delete protection: a taxonomy term referenced by any case cannot be deleted.
7. Decide whether actions can be recorded after the case leaves `Contained`, and whether changing
   them requires a separate edit action.

Open design questions for that phase are listed below.

## 12. Approved decisions

Approved 2026-11-10. Recommendations were accepted as written.

1. **Scope now — taxonomy card + CRUD only.** Do not implement the case-level “mark actions taken”
   flow in this handoff.
2. **Category code — `containment_actions`.**
3. **Seed protection — `protected=False`.** The four defaults are editable/deletable like ordinary
   terms.
4. **Codes/labels — confirmed exactly:**
   - `password_reset` / “Password reset”
   - `process_killed` / “Process Killed”
   - `asset_network_contained` / “Asset network contained”
   - `user_disabled` / “User disabled”
5. **API exposure — leave `/api/v1/taxonomy` unchanged.** The UI uses the server-rendered taxonomy
   page; no new REST payload in this handoff.

### 12.1 Future-phase questions still open

These do **not** block this implementation and must be resolved before the case-level phase:
- Whether “Contained” means the seeded status value, any status with a particular stage, or a new
  explicit `CaseStatus` flag.
- Whether actions are optional or mandatory, and whether “none taken” is representable.
- Whether multiple actions and repeated actions (different assets/users) are allowed.
- Visibility by role/case access.
- Delete behavior once a term is referenced by a case (proposed: block and show in-use).

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hard-coded category `<select>` is missed | AC16 + UI test asserting option presence and `is_closing` absence |
| New category omitted from `ALL_CATEGORIES` | AC2 + read-layer test |
| Seed migration overwrites manager edits | `get_or_create`; never update existing rows |
| Reverse migration deletes unrelated terms | Reverse filters on `category="containment_actions"` |
| Future case FK blocks deletion unexpectedly | Design relation with `PROTECT`/snapshot and extend `in_use_count` before enabling case use |
| Confusion between `Contained` status and `containment` phase | Docs and Q6 explicitly distinguish them |
| Global taxonomy vs org scoping confusion | Keep model global; document that this is vocabulary, not tenant scope |
| Full-suite regression from uncommitted parallel work | Run focused tests; user runs full suite; do not commit unrelated files |

## 14. Handoff

Approved and handed off 2026-11-10.

- Implementation brief: `docs/planning/BRIEF-2026-11-10-containment-actions-taxonomy.md`
- Owners: `django-backend` (model, migrations, read layer, page context, backend tests) and
  `django-frontend` (taxonomy card, form option, UI tests).
- Full test suite remains a user-run step. Agents run focused tests only.
- Deviations must be recorded in this plan before completion.

## 15. Implementation record (2026-11-10)

- Implemented category + seed migration + taxonomy page context:
  `core/models.py`, `core/taxonomy_read.py`, `ui/views/taxonomy.py`,
  `core/migrations/0003_alter_taxonomyterm_category.py`,
  `core/migrations/0004_seed_containment_actions.py`.
- Implemented the taxonomy card and form category option:
  `ui/templates/ui/taxonomy.html`, `ui/templates/ui/_taxonomy_term_form.html`.
- Added focused tests:
  - `tests/conformance/test_taxonomy_containment_actions.py` (5 tests)
  - `tests/conformance/test_ui_containment_actions_taxonomy.py` (8 tests)
- Documented the sixth category in `docs/spec/data-model.md`.
- Focused verification: 44 taxonomy/verdict/editing tests passed; `manage.py check`,
  `makemigrations --check`, `ruff`, and `git diff --check` passed.
- Deviations during implementation:
  - The first seed migration omitted the `category` field in its tuples; fixed before verification.
  - The first card implementation placed the new card outside the page stack and omitted edit/delete
    dialogs; fixed and covered by a dialog regression test.
  - An agent briefly rewrote unrelated `CheckConstraint(check=...)` calls across the repo to work
    around a system-Python/Django mismatch. Those unrelated edits were reverted; only the required
    taxonomy files and pre-existing work remain changed.
  - The audit action for a label edit is `taxonomy.term.updated` (existing service behavior), not
    `renamed`; the test asserts the actual action.
- No commit made. User ran the full pytest suite after implementation:
  **3622 passed, 3 skipped, 15 warnings in 876.80s** (2026-11-10). Log:
  `/tmp/amalthea-full-pytest.log`. Skips are the existing Postgres-only cases; no failures.
