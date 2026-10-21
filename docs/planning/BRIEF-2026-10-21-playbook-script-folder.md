# BRIEF — 2026-10-21 — Playbook scripts in a filesystem folder

**For:** `django-backend` (P1–P4), then `django-frontend` (P5); `security-auditor` (P7) before merge
**Plan:** `docs/planning/PLAN-2026-10-21-playbook-script-folder.md` (DRAFT → approved by operator)
**ADR:** `docs/decisions/ADR-011-playbook-scripts-folder.md`
**Verifier:** `verifier` (P8)

---

## Ground truth (verified 2026-10-21)

* Registry today: `automation/executor.py` — `_ACTIONS` (line 54), `register_action` (65),
  `registered_actions` (70), `run_python_action` (386), built-in registration (423:
  `register_action("amalthea.automation.executor.enrichment_probe", _enrichment_probe)`).
* `_enrichment_probe` at `automation/executor.py:~402` — takes `**values`, returns `str`.
* Validation: `automation/playbooks.py:20,95,98` (`_validate_python` uses `registered_actions()`).
* API `_meta`: `automation/views.py:141-165` serves `actions[python].registeredPaths`.
* `playbook_json`: `core/serializers.py:245` (config inlined — unchanged).
* UI usages of `registered_actions()`: `ui/views.py:58,271,274,2098,2110,2160,2196,2271,2273,2323`.
* Tests to update: `tests/conformance/test_automation_executor.py` (imports `register_action`,
  `registered_actions`), `tests/conformance/test_playbook_authoring.py`
  (`test_playbook_meta_registered_actions_match_executor`).
* Docs to update: `docs/spec/automation.md:39,120-123,132-133`, `docs/spec/api.md:304`,
  `docs/spec/deviations.md:52`.
* Settings pattern: `amalthea/settings/base.py` (`BASE_DIR = Path(__file__)...parent.parent.parent`).
* No dynamic import/exec/eval exists today.

## Hard constraints

* **Remove the registry entirely** — `register_action` / `_ACTIONS` / `registered_actions` must not
  exist after this change (code, tests, docs). One mechanism only.
* **The app must never write to `PLAYBOOK_SCRIPTS_DIR`** and must expose **no** upload/edit for it.
* **Nothing from a `Playbook` row is imported or evaluated** — resolution maps a DB string to a
  discovered script *name* only; loading uses an explicit path from the folder scan.
* Create/edit playbook routes: **`admin`/`superAdmin` only**.
* No new CSS classes beyond what exists; keep undefined-class backlog at 42. If a new class is truly
  needed, hand the CSS change to the same pipeline (`app.src.css` → `make css`).
* `.venv/bin/python` (no `python` on PATH); `.venv/bin/ruff`; `make check` green.
* Targeted pytest only (no full suite without approval). Run exactly:
  `.venv/bin/python -m pytest tests/conformance/test_playbook_scripts_loader.py tests/conformance/test_automation_executor.py tests/conformance/test_playbook_authoring.py`

## Tasks

### P1 — Loader + folder (backend)
1. Create `automation/scripts/__init__.py` (package marker; not a script).
2. Extract `_enrichment_probe` from `executor.py` into `automation/scripts/enrichment_probe.py`
   with `NAME`, `DESCRIPTION`, `TRIGGER_HINT` and `def run(**values) -> str:` (same body/logic).
3. New `automation/scripts_loader.py`:
   - `ScriptInfo` dataclass: `name`, `path`, `description`, `trigger_hint`, `run`, `load_error`.
   - `discover_scripts(*, refresh=False) -> dict[str, ScriptInfo]` (lazy, cached per process).
   - `load_script(name) -> Callable[..., str]` (strict; raises for unknown).
   - `script_names() -> list[str]`; `reset_cache()` (tests).
   - Rules: only `*.py`, stem not starting with `_`; require callable `run`; syntax/import errors →
     skip + record `load_error` (never fatal); duplicate identity → deterministic first-wins + report;
     path-escape/symlink guard (resolved path must be inside the scripts dir).
   - Default dir: `BASE_DIR / "automation" / "scripts"`.
4. `amalthea/settings/base.py`: add `PLAYBOOK_SCRIPTS_DIR` (env `AMALTHEA_PLAYBOOK_SCRIPTS_DIR`,
   default package `automation/scripts`).

### P2 — Executor rewrite (backend)
1. Delete `_ACTIONS`, `register_action`, `registered_actions` from `automation/executor.py`.
2. Rewrite `run_python_action` to `load_script(config["action_path"])` and call it; error text:
   `"no discovered script at {path!r}; known: [...]"`.
3. Update the module docstring (remove "registered in register_action" wording).

### P3 — Validation + API + migration (backend)
1. `automation/playbooks.py::_validate_python`: resolve against `script_names()`; message lists known
   scripts.
2. `automation/views.py::playbook_meta`: replace `registeredPaths` with
   `"scripts": [{"name","description","triggerHint"}, ...]` (sorted).
3. Data migration in `automation/migrations/`: rewrite
   `config["action_path"] == "amalthea.automation.executor.enrichment_probe"` → `"enrichment_probe"`
   (idempotent, only touches matching rows).

### P4 — Management command (backend)
1. `automation/management/commands/list_playbook_scripts.py`: list discovered scripts (name, path,
   `load_error`), and report playbooks whose `action_path` is not discovered. Non-zero exit if any
   candidate has a `load_error` (so CI catches a broken script).
2. Wire it into `make check` (a new step, like the `css-check` step).

### P5 — UI (frontend)
1. Replace all `registered_actions()` usages in `ui/views.py` with loader calls.
2. `playbook_form.html`: Python action picker = `<select>` of discovered scripts (name + description).
3. `playbook_detail.html`: read-only "Script" card (NAME, DESCRIPTION, path, escaped source via
   `inspect.getsource`); no edit control.
4. Empty state when no scripts found: explain the folder (deploy a file), no upload control.
5. Tighten create/edit to `admin`/`superAdmin` only (both `playbook_create` and `playbook_detail` POST).

### P6 — Docs
Update `docs/spec/automation.md`, `docs/spec/api.md`, `docs/spec/deviations.md`; add a runbook section
"Adding a playbook script".

## Acceptance criteria

See plan §11 (AC-1..AC-13). Every AC must be independently checkable.

## Verification commands

```
make check
.venv/bin/python -m pytest tests/conformance/test_playbook_scripts_loader.py \
  tests/conformance/test_automation_executor.py tests/conformance/test_playbook_authoring.py
.venv/bin/python manage.py list_playbook_scripts
```
Plus the §7.5 falsifiability proofs (path-escape, skip-on-error, `action_path` validation).

## Deliverable back to planner

* Files changed (paths + line counts), and confirmation the registry symbols are gone (`grep` output).
* `make check` tail (green) + `list_playbook_scripts` output.
* Targeted pytest summary.
* Falsifiability results (before/after).
* Any deviation → record in plan §13; do not diverge silently.
