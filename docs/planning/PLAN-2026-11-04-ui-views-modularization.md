# PLAN-2026-11-04 — Modularize `ui/views.py`

**Status:** Approved 2026-11-04. Implementation may proceed using the recommended defaults below.

## Summary

`ui/views.py` is 6,366 lines and contains roughly 134 top-level definitions spanning auth,
dashboard, alerts, cases, case detail, templates, tasks, observables, suggestions, tags, pages,
attachments, procedures, sharing, automation, ingestion sources/tokens, settings, taxonomy,
and error handlers. It is the last large UI monolith and is difficult to navigate, review, and
modify safely.

This plan splits it into a focused `ui/views/` package with one module per domain. The refactor is
intentionally **structural only**: URL names, view behaviour, decorators, templates, and the
public `ui.views` import surface stay compatible. No schema, API contract, or UI behaviour change is
in scope.

## Goals

- Reduce `ui/views.py` from one 6,366-line module to domain modules of roughly 100–850 lines.
- Keep `ui/urls.py`, `amalthea/urls.py`, and existing tests working through a compatibility layer.
- Make ownership obvious: alerts live in `alerts.py`, cases in `cases.py`, settings in `settings.py`,
  and so on.
- Preserve all existing behaviour and authorization checks exactly.
- Make each extraction step independently reviewable and testable.

## Non-goals

- No functional changes, bug fixes, or new features.
- No URL renaming, route reordering, or permission changes.
- No template or CSS changes.
- No database migration.
- No new service layer, generic base class, or abstraction over the views.
- No wholesale rewrite of large views such as `case_detail`; only move them.
- No cleanup of unrelated historical review comments or docs that mention line numbers in
  `ui/views.py` (they are historical snapshots).

## Current state

- `ui/views.py`: 6,366 lines, ~134 top-level definitions.
- `ui/urls.py`: 347 lines, references `views.<name>` for almost every route.
- `ui/taxonomy_views.py`: imports shared helpers from `.views`.
- `amalthea/urls.py`: uses dotted strings `ui.views.handler404`, `ui.views.handler500`,
  `ui.views.handler403` so `ui` stays outside the pinned mypy scope.
- Known compatibility imports/tests:
  - `tests/conformance/test_alert_fuzzy_similarity.py:854` patches
    `ui.views.alerts_fuzzy_similar_to_case` (used by `_similar_for_case`).
  - `tests/conformance/test_ui_similar_tab_lazy.py:324/327` imports and patches
    `ui.views._similar_for_case`.
  - `tests/conformance/test_ui_p1_wiring.py` imports `handler404`/`handler500`/`handler403`.
  - `tests/conformance/test_settings_users_scoping.py:108` imports `USERS_PER_ORG_CAP`.
  - `tests/conformance/test_ui_procedures.py:569` imports `MAX_BULK_PROCEDURES`.
  - `tests/conformance/test_settings_source_mapping.py:392` imports `_org_blockers_text`.
  - `ui/taxonomy_views.py` imports `_htmx_redirect`, `_require_superadmin_ui`,
    `_taxonomy_context`, `not_viewer`.

## Architecture

### Stack and scope

Amalthea is Django 5.x + DRF + Channels with server-rendered Django templates + HTMX. This plan does
not choose a new stack or introduce a new architectural pattern; it applies normal Python package
decomposition to the existing view layer. No new ADR is needed. Existing decisions such as
ADR-008/ADR-009 (webhook routing and org-owned ingestion sources) are unaffected.

### Package shape

Recommended:

```text
ui/views/
  __init__.py            # compatibility re-exports only
  common.py              # shared helpers, constants, decorators
  auth.py                # SignInView, sign_out
  dashboard.py
  alerts.py
  cases.py               # case list/create/edit/mutations/sharing
  case_detail.py         # case detail, similar fragment, similar helper
  case_templates.py      # template CRUD + case apply-template
  tasks.py
  observables.py
  suggestions.py
  tags.py
  case_artifacts.py      # pages, attachments, procedures
  automation.py
  ingestion_sources.py   # sources list + source detail/mapping
  ingestion_tokens.py    # settings sources + token lifecycle
  settings.py            # settings home, organisations, users
  taxonomy.py
  errors.py
```

`ui/views/__init__.py` re-exports every name needed by `ui/urls.py`, `amalthea/urls.py`,
`ui/taxonomy_views.py`, and the tests listed above. It is a compatibility layer, not a second
implementation.

### Module ownership

| Module | Owns | Approx. source | Notes |
|---|---|---:|---|
| `common.py` | `_base_context`, `not_viewer`, `_case_detail_redirect`, `_htmx_redirect`, `_api_error_detail`, `SEVERITY_LABELS`, `SEVERITY_NAMES`, `CASE_STAGE_VALUES`, `CASE_INLINE_EDIT_FIELDS`, `CASE_TABS`, `CASE_TAB_SLUGS`, `MAX_HEADERS`, `MAX_BULK_PROCEDURES`, `USERS_PER_ORG_CAP`, `_parse_due_date` | 1–344 | No view functions. Imports models/enums, no imports from sibling view modules. |
| `auth.py` | `SignInView`, `sign_out` | 344–389 | Re-export `PasswordChangeView`/`LogoutView` from `__init__.py` if needed. |
| `dashboard.py` | `dashboard` | 389–441 | |
| `alerts.py` | `alert_list`, `alert_detail`, `alert_escalate`, `alert_merge`, `alert_delete_ui`, `alert_status`, `alert_close` | 441–707, 3490–3624 | |
| `cases.py` | `case_list`, `case_create`, `case_create` helpers, `case_delete_ui`, `case_alert_link`, `case_edit`, `case_field_edit`, `case_set_status`, `case_close`, `case_comment`, `case_custom_field_edit`, `case_merge`, `case_export`, `case_share_list`, `case_share_revoke`, `case_tag_add`, `case_tag_remove` | 707–1029, 1329–1762, 2910–2969, 3624–3836, 4444–4536, 4615–4653 | Keep tag mutations here; tag CRUD lives in `tags.py`. |
| `case_detail.py` | `case_detail`, `case_similar_fragment`, `_similar_for_case`, `_inline_edit_fields` | 1029–1100, 1098–1464, 1464–1533 | Large `case_detail` moved intact. |
| `case_templates.py` | `case_apply_template`, `case_template_list/create/detail/edit/delete`, `_parse_template_form`, `_template_source_cases` | 2969–3306 | |
| `tasks.py` | `case_task_create/edit/delete/toggle`, `task_detail`, `task_source_payload`, `task_note`, `_render_task_form_error` | 1762–2158 | |
| `observables.py` | `observable_list/detail/edit/delete`, `case_observable_create/update/remove`, `case_extract_observables` | 2258–2299, 3387–3490, 3833–4261 | |
| `suggestions.py` | `suggestion_accept/reject/ignore`, `_bulk_suggestion_action`, `suggestions_bulk_accept/reject/ignore` | 4261–4444 | |
| `tags.py` | `tag_list/create/detail/delete`, `case_tags`, `case_tag_toggle` | 2930–2969, 3306–3389 | |
| `case_artifacts.py` | `case_page_create/update/delete`, `case_attachment_list/upload/download/delete/detail`, `case_procedure_create`, `case_procedures_create` | 2158–2258, 4653–4922 | Uses `MAX_BULK_PROCEDURES` from `common`. |
| `automation.py` | `automation_list`, `_resolve_playbook_ui`, `playbook_list/create/detail/edit/delete/run` | 2299–2331, 2412–2910 | `sources_list` moves to `ingestion_sources.py`. |
| `ingestion_sources.py` | `sources_list`, `settings_source_detail`, `_source_detail_context`, mapping helpers, `_save_source_mapping`, `_confirm_mapping_apply`, `_apply_mapping_to_alerts`, `_unresolved_lines` | 2331–2412, 5572–5990 | |
| `ingestion_tokens.py` | `_settings_sources_context`, `settings_sources`, `settings_token_create/revoke/deactivate/activate/delete`, `_org_scoped_token` | 5517–5572, 5988–6345 | Uses shared context for token-create re-render. |
| `settings.py` | `settings_home`, `settings_organisations`, `settings_organisations_edit/delete`, `settings_users`, `settings_users_edit/delete`, `_require_superadmin_ui`, `_require_admin_ui`, `_org_blockers_text` | 4922–5517 | `settings_home` stays with settings, not ingestion. |
| `taxonomy.py` | `_taxonomy_context`, `taxonomy` | 4536–4615 | `taxonomy_views.py` changes its import to `.views.taxonomy`. |
| `errors.py` | `handler404`, `handler500`, `handler403` | 6345–6366 | Must remain importable as `ui.views.handler*`. |

Approximate module sizes are guidance, not acceptance criteria. `cases.py` and `case_detail.py` are
the largest candidates; `case_detail` is split out specifically so `cases.py` does not inherit the
330-line detail view.

### Compatibility strategy

1. Convert the existing module to a package with `git mv ui/views.py ui/views/__init__.py`.
2. Extract one domain module at a time. After each extraction:
   - add imports to `ui/views/__init__.py`;
   - run the targeted tests for that domain;
   - run `ruff check` / `ruff format --check` on touched files.
3. At the end, `ui/views/__init__.py` contains only re-exports and a short package docstring.
4. Keep `ui/urls.py` unchanged. It continues to use `from . import taxonomy_views, views` and
   `views.<name>`. This is the strongest compatibility check: if a name is not re-exported, the
   URLconf fails at import time.
5. Keep `amalthea/urls.py` dotted handler strings unchanged.
6. Update `ui/taxonomy_views.py` imports:
   ```python
   from .views.common import _htmx_redirect, _require_superadmin_ui, not_viewer
   from .views.taxonomy import _taxonomy_context
   ```
7. Update the two monkeypatch tests to patch the module that actually calls the function:
   - `tests/conformance/test_alert_fuzzy_similarity.py`: patch
     `ui.views.case_detail.alerts_fuzzy_similar_to_case`.
   - `tests/conformance/test_ui_similar_tab_lazy.py`: import/patch
     `ui.views.case_detail._similar_for_case`.
   These are test-target changes only; the assertions and behaviour remain identical.
8. Keep compatibility aliases in `__init__.py` for names imported by tests and internal modules,
   including `USERS_PER_ORG_CAP`, `MAX_BULK_PROCEDURES`, `_org_blockers_text`,
   `_similar_for_case`, `_htmx_redirect`, `_require_superadmin_ui`, `_taxonomy_context`,
   `not_viewer`, and the error handlers.

## Data model

No changes. No migrations. No model fields, constraints, indexes, or relationship changes.

## API

No changes. No DRF viewsets, serializers, endpoints, status codes, or payloads are touched. The
only routes affected are UI routes, and their names and handler behaviour remain identical.

## UI

No changes. Template names, context keys, HTMX response shapes, modal behaviour, keyboard
shortcuts, and flash messages stay exactly as they are. This plan is deliberately a move-only
refactor.

## Phases and tasks

### Phase 0 — Baseline and package skeleton

**Task 0.1 — Capture baseline**
- Run the existing UI-focused conformance suites and record the result.
- Confirm the worktree is clean.
- Acceptance criteria:
  - [ ] Baseline suite is green before any move.
  - [ ] `git status --short` is clean.
  - [ ] No production code changed.

**Task 0.2 — Create the package**
- `git mv ui/views.py ui/views/__init__.py`.
- Add a short package docstring in `__init__.py`.
- Run `python -c "import ui.urls"` and the UI URL smoke tests.
- Acceptance criteria:
  - [ ] `ui.views` still imports.
  - [ ] `ui.urls` still imports and resolves.
  - [ ] `amalthea.urls.handler404/500/403` still resolve.
  - [ ] No view body changed in this step.

### Phase 1 — Shared foundation

**Task 1.1 — Extract `common.py`**
- Move shared constants and helpers from `__init__.py` to `common.py`.
- Re-export the names from `__init__.py`.
- Acceptance criteria:
  - [ ] `common.py` has no imports from sibling view modules.
  - [ ] `not_viewer`, `_base_context`, `_case_detail_redirect`, `_htmx_redirect`,
    `_api_error_detail`, `CASE_TABS`, `CASE_TAB_SLUGS`, `MAX_HEADERS`, `MAX_BULK_PROCEDURES`,
    `USERS_PER_ORG_CAP` remain importable from `ui.views`.
  - [ ] Viewer-role and case-redirect tests pass unchanged.

**Task 1.2 — Extract auth, dashboard, and errors**
- Move `SignInView`, `sign_out`, `dashboard`, and the three handlers.
- Acceptance criteria:
  - [ ] Login/logout/password-change routes resolve.
  - [ ] `handler404`/`handler500`/`handler403` tests pass.
  - [ ] No DB access added to `handler500`.

### Phase 2 — Primary read surfaces

**Task 2.1 — Extract `alerts.py`**
- Move alert list/detail/escalate/merge/delete and status/close views.
- Acceptance criteria:
  - [ ] All `ui-alert-*` route names resolve and render as before.
  - [ ] Alert escalation/merge tests pass.
  - [ ] `not_viewer` remains the outermost decorator on mutations.

**Task 2.2 — Extract `case_detail.py`**
- Move `case_detail`, `case_similar_fragment`, `_similar_for_case`, `_inline_edit_fields`.
- Acceptance criteria:
  - [ ] Case detail renders every tab exactly as before.
  - [ ] Similar-tab lazy test patches the new module and passes.
  - [ ] Fuzzy-similarity test patches the new module and passes.
  - [ ] `case_similar_fragment` remains viewer-readable and cross-org-safe.

**Task 2.3 — Extract `cases.py`**
- Move case list/create/edit, status/close, field/custom-field edits, comments, delete, merge,
  export, shares, case-alert link, and case-tag mutations.
- Acceptance criteria:
  - [ ] All `ui-case-*` route names resolve.
  - [ ] Case create/edit/close/tag/share tests pass.
  - [ ] No change to audit/atomicity behaviour.
  - [ ] `_case_detail_redirect` is imported from `common`, not redefined.

### Phase 3 — Case sub-resources

**Task 3.1 — Extract `tasks.py`**
- Move task create/edit/delete/toggle, task detail, source payload, and notes.
- Acceptance criteria:
  - [ ] Task tab and task detail tests pass.
  - [ ] `task_source_payload` remains `never_cache` and login-protected.
  - [ ] HTMX redirects still go to the same task/case URLs.

**Task 3.2 — Extract `observables.py` and `suggestions.py`**
- Move observable list/detail/edit/delete, case-observable CRUD, extraction, and all suggestion
  actions/bulk helpers.
- Acceptance criteria:
  - [ ] Observable list/detail/edit/delete tests pass.
  - [ ] Case extraction and suggestion bulk actions pass.
  - [ ] Cross-case observable guard and org scoping are unchanged.
  - [ ] Suggestion actions keep viewer denial and POST-only method guards.

**Task 3.3 — Extract `tags.py`, `case_templates.py`, and `case_artifacts.py`**
- Move tag CRUD/toggle, case template CRUD/apply, pages, attachments, and procedures.
- Acceptance criteria:
  - [ ] Tag list/detail/delete tests pass.
  - [ ] Template CRUD and apply-template tests pass.
  - [ ] Page/attachment/procedure tests pass.
  - [ ] `MAX_BULK_PROCEDURES` still caps bulk procedures at 100.
  - [ ] Attachment upload/download/delete keep the same status codes and messages.

### Phase 4 — Automation, ingestion, settings, taxonomy

**Task 4.1 — Extract `automation.py`**
- Move automation ledger, playbook list/create/detail/edit/delete/run, and `_resolve_playbook_ui`.
- Acceptance criteria:
  - [ ] Playbook authoring/run tests pass.
  - [ ] Read-only users still get uniform 404 on mutations.
  - [ ] Cross-tenant run tests still create zero `AutomationRun` rows.

**Task 4.2 — Split ingestion into sources and tokens**
- Move `sources_list` and source detail/mapping into `ingestion_sources.py`.
- Move `_settings_sources_context`, settings sources, and token lifecycle into
  `ingestion_tokens.py`.
- Acceptance criteria:
  - [ ] Source detail GET/POST and mapping tests pass.
  - [ ] Token create/revoke/deactivate/activate/delete tests pass.
  - [ ] Show-once token plaintext behaviour is unchanged.
  - [ ] Last-token source reaping remains atomic and audited.
  - [ ] Org scoping and uniform 404s are unchanged.

**Task 4.3 — Extract `settings.py` and `taxonomy.py`**
- Move settings home, organisations, users, and their guards/helpers.
- Move taxonomy page and context.
- Update `ui/taxonomy_views.py` imports.
- Acceptance criteria:
  - [ ] Settings organisation/user tests pass.
  - [ ] `USERS_PER_ORG_CAP` remains importable from `ui.views`.
  - [ ] `_org_blockers_text` remains importable from `ui.views`.
  - [ ] Taxonomy page and taxonomy mutation tests pass.
  - [ ] No circular import between `ui.views` and `ui.taxonomy_views`.

### Phase 5 — Cleanup and final compatibility pass

**Task 5.1 — Shrink `__init__.py`**
- Leave only the package docstring and explicit re-exports.
- Remove unused imports introduced during extraction.
- Acceptance criteria:
  - [ ] `ui/views/__init__.py` contains no view bodies or helper implementations.
  - [ ] All `ui/urls.py` names resolve.
  - [ ] Ruff reports no unused imports.

**Task 5.2 — Full verification**
- Run targeted suites and the full gate.
- Acceptance criteria:
  - [ ] Full pytest suite passes.
  - [ ] `make check` passes.
  - [ ] No migration is generated.
  - [ ] `git diff --stat` shows essentially code movement plus the two test patch-target updates.
  - [ ] No behavior changes outside the planned test-target changes.

## Security

- Preserve every `@not_viewer`, `@login_required`, `@require_POST`, `@require_http_methods`, and
  `@serializes_superadmin_changes` decorator on the same view, in the same order.
- Preserve uniform 404s for missing/cross-org objects and viewer mutation attempts.
- Preserve transaction boundaries around audit rows and destructive operations.
- Do not add module-level side effects that run database queries, resolve URLs, or read settings at
  import time.
- Do not change CSRF handling, session auth, or any authorization check.
- The refactor must not expose new names in the dotted-handler path or change `ui.views.handler*`
  resolution.

## Testing

At minimum:

```bash
.venv/bin/pytest tests/conformance/test_ui_p1_wiring.py -q
.venv/bin/pytest tests/conformance/test_ui_urls_smoke.py -q
.venv/bin/pytest tests/conformance/test_ui_similar_tab_lazy.py -q
.venv/bin/pytest tests/conformance/test_alert_fuzzy_similarity.py -q
.venv/bin/pytest tests/conformance/test_settings_users_scoping.py -q
.venv/bin/pytest tests/conformance/test_settings_source_mapping.py -q
.venv/bin/pytest tests/conformance/test_ui_procedures.py -q
```

After each phase, run the relevant subset. At the end:

```bash
.venv/bin/pytest
make check
```

Add no new production tests for the split itself unless a compatibility gap is discovered. The
existing suite, the URL smoke test, and `make check` are the regression gate. The two monkeypatch
targets are the only expected test edits.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Circular imports between domain modules | Import-time failure | Keep `common.py` dependency-free relative to siblings; domain modules import only `common` and app services. |
| Missed re-export breaks `ui/urls.py` | All UI routes fail at import | Import `ui.urls` after every extraction; run URL smoke tests. |
| Monkeypatch tests silently stop exercising the real call path | False green | Update patch targets to the module that owns the function; run both tests explicitly. |
| Large `case_detail` move hides accidental edits | Behaviour drift | Move-only extraction first; review diff for body changes; no refactoring in the move commit. |
| Decorator order changes | Security regression | Treat decorator blocks as atomic; verify with viewer/POST/method tests. |
| `ui/taxonomy_views.py` imports create a cycle | Import-time failure | Import from `views.common`/`views.taxonomy`, not the package `__init__`. |
| Historical docs refer to `ui/views.py:NNNN` | Confusing but harmless | Do not rewrite historical reviews; note in the plan/PR that line references are pre-split. |
| Module count feels fragmented | Review overhead | Use domain-sized modules; `case_detail` and ingestion are split because they are independently large. |

## Implementation notes 2026-11-04

- `ui/views.py` was moved to `ui/views/__init__.py`, then split into the approved modules.
- `_require_superadmin_ui` and `_require_admin_ui` live in `common.py` (not `settings.py`) so
  `ingestion_*` modules and `ui/taxonomy_views.py` can import them without a settings-to-ingestion
  dependency.
- `AuditLog` remains re-exported from `ui.views` for compatibility with
  `tests/conformance/test_ui_p2_wiring.py`, which monkeypatches `ui.views.AuditLog.objects.create`.
- Ruff removed redundant function-local imports that duplicated module-level imports. This is a
  lint-only normalization; those names resolve to the same objects and tests pass.
- The two approved monkeypatch targets were updated to `ui.views.case_detail.*`.
- `tests/conformance/test_wire_boundary.py` now treats `ui/views/` as the same presentation
  boundary the old `ui/views.py` occupied via the `*/views.py` allowlist. The UI form field names
  (`dataType`, `ignoreSimilarity`, …) are unchanged; no product code was altered for this.

## Decisions confirmed 2026-11-04

1. **Package shape:** use `ui/views/` with a compatibility `__init__.py` that re-exports names.
   Do not use sibling `ui/views_*.py` files.
2. **Granularity:** use the domain modules listed above, including separate `case_detail.py`,
   `ingestion_sources.py`, and `ingestion_tokens.py`.
3. **Compatibility:** keep `ui.views.*` working for all current imports. Update only the two
   monkeypatch targets that must point at the new owner module.
4. **Taxonomy placement:** move only the taxonomy page/context into `ui/views/taxonomy.py`; keep
   `ui/taxonomy_views.py` as the mutation module.
5. **`ui/urls.py`:** leave it unchanged in this refactor. A direct-module-import cleanup is a
   follow-up, not part of this change.

## Approval gate

Approved. Implementation follows Phases 0–5. Any deviation from the approved module map or
compatibility strategy must be recorded in this plan before or alongside the change.
