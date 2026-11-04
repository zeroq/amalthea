# BRIEF-2026-11-04 — Modularize `ui/views.py`

**Plan:** [PLAN-2026-11-04-ui-views-modularization.md](./PLAN-2026-11-04-ui-views-modularization.md)  
**Owner:** `django-backend`  
**Status:** Approved for implementation

## Objective

Split the 6,366-line `ui/views.py` into the approved `ui/views/` package. This is a **move-only
structural refactor**. Do not change behaviour, URLs, templates, permissions, API contracts, or the
database schema.

## Ground rules

- Keep `ui/urls.py` unchanged.
- Keep `amalthea/urls.py` dotted handlers unchanged: `ui.views.handler404/500/403`.
- Keep `ui.views.*` import compatibility through `ui/views/__init__.py`.
- Preserve every decorator, decorator order, transaction boundary, status code, message, and
  context key.
- Move code; do not “improve” it while moving.
- Do not commit, amend, or push unless explicitly asked.
- Do not generate migrations.
- If a deviation is necessary, record it in the plan before finishing.

## Target layout

```text
ui/views/
  __init__.py
  common.py
  auth.py
  dashboard.py
  alerts.py
  cases.py
  case_detail.py
  case_templates.py
  tasks.py
  observables.py
  suggestions.py
  tags.py
  case_artifacts.py
  automation.py
  ingestion_sources.py
  ingestion_tokens.py
  settings.py
  taxonomy.py
  errors.py
```

## Implementation steps

### 1. Baseline

- Confirm the worktree is clean except for the approved planning docs.
- Run a baseline UI suite:
  ```bash
  .venv/bin/pytest tests/conformance/test_ui_p1_wiring.py tests/conformance/test_ui_urls_smoke.py -q
  ```

### 2. Create the package

```bash
git mv ui/views.py ui/views/__init__.py
```

- Keep the existing module docstring temporarily.
- Verify imports:
  ```bash
  .venv/bin/python -c "import ui.urls"
  ```
- AC: `ui.urls` imports and all dotted handlers still resolve.

### 3. Extract modules in this order

For each step: create the module, move the listed definitions, remove them from
`ui/views/__init__.py`, add re-exports to `__init__.py`, and run the listed tests.

#### `common.py`

Move shared constants/helpers:

- `SEVERITY_LABELS`, `SEVERITY_NAMES`, `CASE_STAGE_VALUES`, `CASE_INLINE_EDIT_FIELDS`
- `MAX_HEADERS`, `MAX_BULK_PROCEDURES`, `USERS_PER_ORG_CAP`
- `_parse_due_date`, `not_viewer`, `_base_context`
- `CASE_TABS`, `CASE_TAB_SLUGS`, `_case_detail_redirect`, `_htmx_redirect`, `_api_error_detail`

AC:
- No imports from sibling view modules.
- These names remain importable from `ui.views`.
- `tests/conformance/test_viewer_readonly_ui.py` passes.

#### `auth.py`

Move `SignInView`, `sign_out`.
Re-export `PasswordChangeView` and `LogoutView` from `ui.views` if needed by `ui/urls.py`.

AC: login/logout/password-change routes resolve.

#### `dashboard.py`

Move `dashboard`.

AC: dashboard route renders; nav counts unchanged.

#### `errors.py`

Move `handler404`, `handler500`, `handler403`.

AC:
- `tests/conformance/test_ui_p1_wiring.py` passes.
- `handler500` still performs no DB access.

#### `alerts.py`

Move:

- `alert_list`, `alert_detail`, `alert_escalate`, `alert_merge`, `alert_delete_ui`
- `alert_status`, `alert_close`

AC:
- Alert list/detail/escalate/merge/status/close tests pass.
- Mutation decorators remain `@not_viewer`, `@login_required`, `@require_POST` in the same order.

#### `case_detail.py`

Move:

- `case_detail`, `case_similar_fragment`
- `_similar_for_case`, `_inline_edit_fields`

AC:
- Case detail tab tests pass.
- Similar-tab lazy test passes after patching `ui.views.case_detail._similar_for_case`.
- Fuzzy-similarity test passes after patching
  `ui.views.case_detail.alerts_fuzzy_similar_to_case`.
- Cross-org/unknown case still returns 404.

#### `cases.py`

Move:

- `case_list`, `case_create` and its create/list render helpers
- `case_delete_ui`, `case_alert_link`
- `case_edit`, `case_field_edit`, `case_set_status`, `case_close`
- `case_comment`, `case_custom_field_edit`, `case_merge`
- `case_export`, `case_share_list`, `case_share_revoke`
- `case_tag_add`, `case_tag_remove`

AC:
- All `ui-case-*` routes resolve.
- Case create/edit/close/merge/share tests pass.
- Audit/atomicity behaviour is unchanged.
- `_case_detail_redirect` is imported from `common`, not redefined.

#### `tasks.py`

Move:

- `case_task_create`, `case_task_edit`, `case_task_delete`, `case_task_toggle`
- `task_detail`, `task_source_payload`, `task_note`
- `_render_task_form_error`

AC: task tab/detail/source-payload/note tests pass.

#### `observables.py`

Move:

- `observable_list`, `observable_detail`, `observable_edit`, `observable_delete`
- `case_observable_create`, `case_observable_update`, `case_observable_remove`
- `case_extract_observables`

AC:
- Observable list/detail/edit/delete tests pass.
- Extraction and cross-case guard tests pass.
- Org scoping is unchanged.

#### `suggestions.py`

Move:

- `suggestion_accept`, `suggestion_reject`, `suggestion_ignore`
- `_bulk_suggestion_action`
- `suggestions_bulk_accept`, `suggestions_bulk_reject`, `suggestions_bulk_ignore`

AC: suggestion single/bulk tests pass; viewer denial and POST-only guards unchanged.

#### `tags.py`

Move:

- `tag_list`, `tag_create`, `tag_detail`, `tag_delete`
- `case_tags`, `case_tag_toggle`

AC: tag list/detail/delete and case-tag toggle tests pass.

#### `case_templates.py`

Move:

- `case_apply_template`
- `case_template_list`, `case_template_create`, `case_template_detail`,
  `case_template_edit`, `case_template_delete`
- `_parse_template_form`, `_template_source_cases`

AC: template CRUD and apply-template tests pass.

#### `case_artifacts.py`

Move:

- `case_page_create`, `case_page_update`, `case_page_delete`
- `case_attachment_list`, `case_attachment_upload`, `case_attachment_download`,
  `case_attachment_delete`, `case_attachment_detail`
- `case_procedure_create`, `case_procedures_create`

AC:
- Page/attachment/procedure tests pass.
- `MAX_BULK_PROCEDURES` still caps bulk procedures at 100.
- Attachment upload failure still renders the API error detail, not a 500.

#### `automation.py`

Move:

- `automation_list`
- `_resolve_playbook_ui`
- `playbook_list`, `playbook_create`, `playbook_detail`, `playbook_edit`,
  `playbook_delete`, `playbook_run`

AC: playbook authoring/run tests pass; read-only users still get uniform 404; cross-tenant run
tests create no `AutomationRun`.

#### `ingestion_sources.py`

Move:

- `sources_list`
- `settings_source_detail`
- `_source_detail_context`, `_source_mapping_rows`, `_source_mapping_post`,
  `_submitted_mapping`, `_reject_mapping`, `_persist_mapping`, `_mapping_saved_message`,
  `_save_source_mapping`, `_confirm_mapping_apply`, `_apply_mapping_to_alerts`,
  `_unresolved_lines`

AC: source list/detail/mapping tests pass; org scoping and audit behaviour unchanged.

#### `ingestion_tokens.py`

Move:

- `_settings_sources_context`, `settings_sources`
- `settings_token_create`, `settings_token_revoke`, `settings_token_deactivate`,
  `settings_token_activate`, `settings_token_delete`
- `_org_scoped_token`

AC:
- Settings sources and token lifecycle tests pass.
- Show-once plaintext behaviour is unchanged.
- Last-token source reaping remains atomic and audited.
- Org scoping and uniform 404s are unchanged.

#### `settings.py`

Move:

- `settings_home`
- `settings_organisations`, `settings_organisations_edit`, `settings_organisations_delete`
- `settings_users`, `settings_users_edit`, `settings_users_delete`
- `_require_superadmin_ui`, `_require_admin_ui`, `_org_blockers_text`

AC:
- Organisation/user settings tests pass.
- `USERS_PER_ORG_CAP` and `_org_blockers_text` remain importable from `ui.views`.
- Delete modals still receive blocker text.

#### `taxonomy.py`

Move:

- `_taxonomy_context`, `taxonomy`

Update `ui/taxonomy_views.py`:

```python
from .views.common import _htmx_redirect, _require_superadmin_ui, not_viewer
from .views.taxonomy import _taxonomy_context
```

AC:
- Taxonomy page and mutation tests pass.
- No circular import between `ui.views` and `ui.taxonomy_views`.

### 4. Shrink `ui/views/__init__.py`

- Keep only a short package docstring and explicit re-exports.
- Re-export every name used by `ui/urls.py`, `amalthea/urls.py`, `ui/taxonomy_views.py`, tests, and
  any other internal importer.
- Remove unused imports.

AC:
- `ui/views/__init__.py` has no view bodies or helper implementations.
- `ruff` reports no unused imports.

### 5. Required test-target updates

Only these two test edits are expected:

```python
# tests/conformance/test_alert_fuzzy_similarity.py
with mock.patch(
    "ui.views.case_detail.alerts_fuzzy_similar_to_case", return_value=([], 0)
) as patched:
```

```python
# tests/conformance/test_ui_similar_tab_lazy.py
from ui.views.case_detail import _similar_for_case as real_helper

with mock.patch("ui.views.case_detail._similar_for_case", wraps=real_helper) as patched:
```

Do not change assertions or test behaviour.

## Verification

Run targeted checks during extraction:

```bash
.venv/bin/pytest tests/conformance/test_ui_p1_wiring.py -q
.venv/bin/pytest tests/conformance/test_ui_urls_smoke.py -q
.venv/bin/pytest tests/conformance/test_ui_similar_tab_lazy.py -q
.venv/bin/pytest tests/conformance/test_alert_fuzzy_similarity.py -q
.venv/bin/pytest tests/conformance/test_settings_users_scoping.py -q
.venv/bin/pytest tests/conformance/test_settings_source_mapping.py -q
.venv/bin/pytest tests/conformance/test_ui_procedures.py -q
```

Final gate:

```bash
.venv/bin/pytest
make check
```

## Report back

Report:

- Final module layout and line counts.
- Exact list of files created/moved.
- Any compatibility re-exports that were non-obvious.
- Test commands and results.
- Any deviations from this brief or the plan.
- Confirmation that no commit/push was made.
