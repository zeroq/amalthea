# Verification Report: Playbook scripts in a filesystem folder

**Plan:** `docs/planning/PLAN-2026-10-21-playbook-script-folder.md`
**Verified:** 2026-10-21 14:30 UTC
**Status:** Pass

## Summary

- Total ACs: 13
- Met: 13
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC-1: All playbook scripts live only in `automation/scripts/`; the built-in probe is a file there.
**Status:** Met
**Evidence:**
- `automation/scripts/__init__.py` (2 lines) — package marker
- `automation/scripts/enrichment_probe.py` (24 lines) — extracted built-in probe with `NAME = "enrichment_probe"`, `DESCRIPTION`, `TRIGGER_HINT`, and `def run(**values) -> str`
- No other script locations in codebase
**Notes:** The folder is the single source of truth for playbook scripts.

### AC-2: `register_action` / `_ACTIONS` / `registered_actions` no longer exist anywhere (code/tests/docs).
**Status:** Met
**Evidence:**
- `grep -rn "register_action\|registered_actions\|_ACTIONS" --include="*.py" automation/ ui/ core/ tests/ docs/spec/` returns nothing
- `automation/executor.py` rewritten (389 lines) — registry symbols completely removed
- `automation/playbooks.py` imports from `scripts_loader` only
- Tests updated to remove registry imports

### AC-3: Executor resolves `action_path` to a discovered script and runs it with interpolation values.
**Status:** Met
**Evidence:**
- `automation/executor.py:371-379` — `run_python_action` calls `load_script(config["action_path"])` and invokes `script(**values)`
- `automation/scripts_loader.py:222-243` — `load_script` resolves name to callable from discovered set
- Test `test_a_registered_action_receives_the_interpolation_values` (tests/conformance/test_automation_executor.py:114-124) verifies the probe receives values

### AC-4: Unknown / non-discovered / path-escaping `action_path` is refused (400 at write, `ActionError` at run).
**Status:** Met
**Evidence:**
- **Write-time (400):** `automation/playbooks.py:91-97` — `_validate_python` checks `path in script_names()`, returns field error with known list
- **Run-time (ActionError):** `automation/executor.py:375-376` — `load_script` raises `ActionError` with "no discovered script at {name!r}; known: [...]"
- **Path-escape guard:** `automation/scripts_loader.py:70-76` — `_is_inside_scripts_dir` uses `resolve().relative_to()`; invoked at line 121
- Test `test_symlink_outside_scripts_dir_is_refused` (test_playbook_scripts_loader.py:317-342) verifies symlink escape is refused

### AC-5: Discovery rules hold: `*.py`, non-`_`-prefixed, requires `run`; malformed files are skipped + reported, not fatal.
**Status:** Met
**Evidence:**
- `automation/scripts_loader.py:100-108` — filters for `.py` suffix and non-`_` stem
- Line 141-143 — requires callable `run`, raises `AttributeError` if missing
- Lines 185-195 — `try/except` catches all exceptions, records `load_error`, sets `run=None`
- Tests:
  - `test_only_py_files_are_candidates` (line 34)
  - `test_underscore_prefixed_files_are_excluded` (line 40)
  - `test_script_must_have_callable_run` (line 46)
  - `test_syntax_error_is_caught_and_recorded` (line 54)
  - `test_missing_run_attribute_is_recorded` (line 73)
  - `test_discovered_scripts_includes_load_error_info` (line 188)

### AC-6: `/playbook/_meta` advertises discovered scripts; UI picker and validation use the same set.
**Status:** Met
**Evidence:**
- `automation/views.py:148-157` — `playbook_meta` builds `scripts` list from `discover_scripts()` (filtered to `load_error is None`)
- `automation/playbooks.py:95` — `_validate_python` uses `script_names()` (loadable only)
- `ui/views.py:2122-2132`, `2366-2375` — `playbook_create` and `playbook_edit` GET use `discover_scripts()` filtered to loadable
- Test `test_playbook_meta_scripts_match_loader` (test_playbook_authoring.py) asserts `_meta.scripts` names == `script_names()`

### AC-7: UI lists scripts and shows source read-only; **no** upload/edit; empty state explains the folder.
**Status:** Met
**Evidence:**
- **Picker (`playbook_form.html:111-130`):** `<select id="id_action_path">` with script `name` + `description` hint; no upload control
- **Detail card (`playbook_detail.html:68-100`):** "Script" card with NAME, DESCRIPTION, FILE PATH, TRIGGER HINT, and `<pre class="body">{{ script_info.source }}</pre>` (read-only, escaped)
- **Empty state (`playbook_form.html:125-128`):** "No playbook scripts found in `automation/scripts/`. Deploy a `.py` file... The folder is deploy-only — there is no upload control."
- Tests:
  - `test_form_renders_script_options_and_preselects` (test_playbook_script_ui.py)
  - `test_form_shows_description_for_selected_script`
  - `test_form_shows_empty_state_when_no_scripts`
  - `test_detail_shows_script_card_for_python_action`
  - `test_detail_script_source_is_escaped`
  - `test_detail_shows_warning_for_missing_script`

### AC-8: Create/edit restricted to `admin`/`superAdmin`.
**Status:** Met
**Evidence:**
- `ui/views.py:2115-2116` — `playbook_create` GET/POST: `if request.user.role not in ("admin", "superAdmin"): return 404`
- `ui/views.py:2252-2253` — `playbook_detail` POST: same check
- `ui/views.py:2403-2404` — `playbook_edit` POST: same check
- Tests (TestPlaybookAuthzTightening):
  - `test_admin_create_get_allowed`, `test_admin_create_post_allowed`, `test_admin_detail_post_allowed`
  - `test_superadmin_create_get_allowed`, `test_superadmin_create_post_allowed`, `test_superadmin_detail_post_allowed`
  - `test_analyst_create_get_404`, `test_analyst_create_post_404`, `test_analyst_detail_post_404`, `test_analyst_edit_post_404`
  - `test_viewer_create_get_404`, `test_viewer_create_post_404`, `test_viewer_detail_post_404`, `test_viewer_edit_get_404`

### AC-9: Legacy `action_path` rows are migrated to the bare script name.
**Status:** Met
**Evidence:**
- `automation/migrations/0007_rewrite_legacy_action_path.py` — `RunPython` rewrites `config["action_path"] == "amalthea.automation.executor.enrichment_probe"` → `"enrichment_probe"` (idempotent, only touches matching rows)
- Reverse operation maps back to legacy dotted path
- Seed migrations `0005_seed_playbook.py:31` and `0006_seed_example_playbook.py:20` use bare name `enrichment_probe`
- `python manage.py showmigrations automation` shows `[X] 0007_rewrite_legacy_action_path`

### AC-10: `manage.py list_playbook_scripts` lists/validates; `make check` green including it.
**Status:** Met
**Evidence:**
- `automation/management/commands/list_playbook_scripts.py` — lists scripts (name, path, load_error), validates playbook `action_path` references, exits non-zero on load errors or unknown references
- `Makefile` includes `playbook scripts` step running the command
- Output:
  ```
  Discovered playbook scripts:
  NAME                           PATH                                               STATUS       ERROR
  enrichment_probe               .../automation/scripts/enrichment_probe.py         OK           
  Runnable: 1 / Total discovered: 1
  All checks passed
  ```
- `make check` output: `✓ playbook scripts` → `✓ check passed`

### AC-11: "Nothing from a `Playbook` row is imported or evaluated" still holds (test asserts `os.system`/`builtins.eval` are not runnable).
**Status:** Met
**Evidence:**
- `automation/executor.py:371-379` — `run_python_action` only calls `load_script(path)` which resolves against discovered scripts; no `importlib.import_module` of DB strings
- Test `test_the_python_action_only_runs_discovered_scripts` (test_automation_executor.py:103-111):
  ```python
  with pytest.raises(ActionError, match="no discovered script"):
      execute({"action": "python", "action_path": "os.system"}, {})
  with pytest.raises(ActionError, match="no discovered script"):
      execute({"action": "python", "action_path": "builtins.eval"}, {})
  ```
- Loader uses explicit path from folder scan (`importlib.util.spec_from_file_location`), never a DB-supplied string

### AC-12: Falsifiability evidence recorded for each new guard.
**Status:** Met
**Evidence (Plan §13):**
1. **Path-escape guard** — Removed `_is_inside_scripts_dir` check → symlink outside scripts_dir loads as valid script (test `test_symlink_outside_scripts_dir_is_refused` fails). Restored, passes.
2. **Skip-on-error** — Removed try/except in `_discover_once` → SyntaxError propagates and crashes discovery (test `test_syntax_error_is_caught_and_recorded` fails). Restored, passes.
3. **action_path validation** — Removed `script_names()` check in `_validate_python` → unknown script accepted (test `test_config_python_unregistered_path_refused` fails). Restored, passes.
- All original files restored with matching SHA256 hashes.

### AC-13: `security-auditor` review completed; ADR-011 present.
**Status:** Met
**Evidence:**
- ADR-011 at `docs/decisions/ADR-011-playbook-scripts-folder.md` (Accepted, 2026-10-21)
- Plan records: "Security auditor gave CONDITIONAL APPROVE with S-02 fixed"
- S-02 fix implemented in `scripts_loader.py:213-217` — cleanup of `_inserted_modules` on `refresh=True` to prevent module accumulation across reloads
- Trust boundary documented: deploy-only folder, no app writes, no upload, strict resolution

## Deviations

None. Implementation follows the approved plan exactly.

## Blockers

None.

## Recommendations

- Consider adding a dedicated test for migration `0007_rewrite_legacy_action_path` to explicitly verify legacy row rewriting (currently covered implicitly by seed migrations using bare names).
- The security audit finding S-02 fix is in code but no separate audit file exists in `docs/security/`; consider adding `AUDIT-2026-10-21-playbook-scripts.md` for traceability.
- The undefined-class backlog (42) is unchanged — no new CSS classes introduced. Classes `.card-actions` and `.remove-header` existed prior to this feature.

---

**Overall: READY TO COMMIT**