# PLAN — Refresh playbook script discovery on form GET

**Date:** 2026-11-12
**Status:** Superseded by `PLAN-2026-11-12-playbook-script-fetch-on-change.md` (GET-refresh replaced by an on-change fetch)
**Area:** Automation · Playbook authoring UI
**Related:** `ui/views/automation.py`, `automation/scripts_loader.py`,
`ui/templates/ui/playbook_form.html`, `ui/static/ui/playbook_form.js`,
ADR-011 (deploy-only playbook scripts folder)

---

## 1. Summary

The PYTHON action's **Script** dropdown is populated from `discover_scripts()`
(`automation/scripts_loader.py`), which scans `PLAYBOOK_SCRIPTS_DIR` (default
`automation/scripts/`) once and caches the result in a process-global (`_scripts_cache`) with no
TTL and no file-watch. The JS only shows/hides the field group; it does not fetch. Consequently a
newly deployed `.py` file does not appear in the picker until the Django process restarts (each
Daphne/Celery worker independently).

The requested change: call `discover_scripts(refresh=True)` in the **playbook form GET**
(`ui/views/automation.py::playbook_create` and `::playbook_edit`), so the picker re-scans the
folder on each form load.

## 2. Goals

- G1: `GET` on the playbook **create** form reflects the scripts on disk at request time.
- G2: `GET` on the playbook **edit** form reflects the scripts on disk at request time.
- G3: Preserve the deploy-only model and all existing validation (POST still validates via
  `script_names()`).

## 3. Non-goals

- No upload control; the folder remains deploy-only (ADR-011).
- No new cache architecture (TTL/file-watch) unless chosen in Q2.
- No change to `playbook_detail` read-only script display or `GET /api/v1/playbook/_meta`
  unless chosen in Q1.
- No change to the JS; the options are still server-rendered, not fetched on select.

## 4. Current state

- `automation/scripts_loader.py::discover_scripts(refresh=False)` — caches per process under
  `_cache_lock`; `refresh=True` pops tracked `amalthea_playbook_script_*` modules from
  `sys.modules`, clears `_inserted_modules`, and re-runs `_discover_once()` (which imports every
  candidate `.py`).
- `ui/views/automation.py::playbook_create` GET (~line 239): `scripts = discover_scripts()`.
- `ui/views/automation.py::playbook_edit` GET (~line 489): `scripts = discover_scripts()`.
- POST validation (`_parse_playbook_config` → `script_names()`) and `automation/playbooks.py::
  _validate_python` read the same cache; they will see the refreshed cache written by the last
  form GET (each request re-scans on GET).

## 5. Plan / Tasks

### T1 — Create form GET refresh (`ui/views/automation.py`)
Change the GET branch of `playbook_create`:
```python
scripts = discover_scripts(refresh=True)
```
**AC**
- AC1: After the cache is warm, adding a `.py` file to `PLAYBOOK_SCRIPTS_DIR` and GETting the
  create form shows the new script's `<option>` without restarting the process.

### T2 — Edit form GET refresh (`ui/views/automation.py`)
Change the GET branch of `playbook_edit`:
```python
scripts = discover_scripts(refresh=True)
```
**AC**
- AC2: Same as AC1, for the edit form (and the current `action_path` stays selected).

### T3 — Regression test (`tests/conformance/test_playbook_script_ui.py`)
Use `override_settings(PLAYBOOK_SCRIPTS_DIR=tmpdir)` and `reset_cache()`; write one script, GET the
form to warm the cache, write a second script, GET again, assert both options appear. Cover create
and edit. Clean up `reset_cache()` in teardown (already the file's pattern).
**AC**
- AC3: New test fails on the current code (cached list) and passes with `refresh=True`.
- AC4: Existing `test_playbook_script_ui.py` and `test_playbook_scripts_loader.py` stay green.

### T4 — Documentation note
Add a one-line comment at each call site explaining **why** the refresh is deliberate (deploy-only
folder, no watcher), so a future reader does not "optimise" it back to the cached call.
**AC**
- AC5: Comment present at both call sites.

## 6. Testing / verification

- `pytest tests/conformance/test_playbook_script_ui.py tests/conformance/test_playbook_scripts_loader.py -q`
- `pytest tests/conformance/test_ui_p1_wiring.py -k playbook -q`
- `make check`

## 7. Risks

- **Re-import cost on every form load.** `refresh=True` re-imports every script module
  (re-executes top-level code). For cheap, metadata-only scripts this is negligible; a slow or
  side-effecting import would add latency per form GET.
- **Log noise for broken scripts.** A script that fails to import logs an exception on every form
  load (previously once per process). Useful signal, but noisier.
- **Concurrency.** Refresh is serialised by `_cache_lock`; in-flight runs keep their `run`
  function references even after `sys.modules` cleanup, so no execution break. Acceptable.

## 8. Open Questions — RESOLVED (2026-11-12)

1. **Scope:** refresh **create + edit** forms only; `playbook_detail` GET and
   `GET /api/v1/playbook/_meta` stay cache-only. **Decision: create + edit.**
2. **Cost:** unconditional `refresh=True` on every form GET. **Decision: unconditional.**
3. **Broken scripts:** a failing import re-logs on every form GET. **Decision: accepted.**
4. **Test:** add the temp-dir regression test in T3. **Decision: yes.**
