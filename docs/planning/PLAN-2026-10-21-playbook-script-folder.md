# PLAN — 2026-10-21 — Playbook scripts in a filesystem folder

**Status:** DRAFT — awaiting user approval
**Owner:** planner
**Implementers:** `django-backend` (loader/executor/validation/migration/API), `django-frontend` (UI)
**Mandatory review:** `security-auditor` (loader trust boundary) before merge
**Verifier:** `verifier`
**Feature size:** M (cross-cutting: model migration, executor rewrite, API + UI, docs)
**ADR:** `docs/decisions/ADR-011-playbook-scripts-folder.md`

---

## 1. Summary

Replace the in-process action registry (`register_action` / `_ACTIONS` / `registered_actions`) with a
**single filesystem location for all playbook scripts**, `automation/scripts/`. The current built-in
probe is extracted into a file there. The executor discovers scripts from the folder; a
`Playbook.config["action_path"]` names a **script name**, and the active playbook's `trigger_event`
decides when it runs. The UI gains a read-only script inventory (list + source view) and a script
picker for playbooks.

The product outcome: *see, edit, and create playbook code that is actually run*, with new scripts
added by dropping a file and configured via the UI — without the DB ever supplying executable code.

## 2. Goals

- G1: All playbook code lives in `automation/scripts/`, one script per file. Nothing else executes.
- G2: The executor discovers scripts from the folder and resolves `action_path` to a discovered script.
- G3: Write-time validation (API, UI, `_meta`) resolves against the **same** discovered set, so a
  playbook cannot name a script that is not on disk.
- G4: The UI **lists** scripts and shows their **source read-only**; a playbook form **picks** one.
- G5: `register_action` / `_ACTIONS` / `registered_actions` are **removed entirely** (one mechanism).
- G6: Existing `Playbook` rows migrate to the new bare script name.
- G7: The "nothing from a `Playbook` row is imported or evaluated" invariant is preserved.

## 3. Non-goals

- N1: **No UI upload / no UI file editing.** The folder is deploy-only (git or mounted volume). The app
  never writes to `PLAYBOOK_SCRIPTS_DIR`.
- N2: **No versioning** of scripts (operator decision). No `AutomationRun` → script-content linkage yet.
- N3: **No inline `config`-carried code** (that is Option 1; rejected — see ADR-011).
- N4: **No hot reload.** Discovery is cached per process; a new script needs a reload.
- N5: No sandbox / subprocess isolation (scripts run in-process with the worker's privileges; the
  trust boundary is "deploy-only folder", documented in ADR-011).
- N6: No change to the `http` action or the SSRF guard.
- N7: No change to `Playbook` trigger/dispatch semantics (signals, idempotency, `triggered_by`).

## 4. Architecture

### 4.1 Folder and script contract

```
automation/scripts/
  __init__.py                 # package marker; NOT a script (underscore/dunder excluded)
  enrichment_probe.py         # the extracted built-in (doc example)
  _helpers.py                 # optional shared helpers; leading "_" => not a script
```

Each script file:

```python
# automation/scripts/block_ip.py
NAME = "block_ip"  # optional; defaults to the filename stem
DESCRIPTION = "Block an IP in the perimeter firewall and report the ticket id."
TRIGGER_HINT = "observable.created"  # advisory only; the DB row is authoritative


def run(**values) -> str:
    """Entry point. `values` carries the same interpolation names an http action gets."""
    fqdn = values.get("observable") or ""
    if not fqdn:
        return "no observable supplied; nothing to block"
    return f"block requested for {fqdn}"
```

Rules (all enforced by the loader; violations are **skipped + reported**, never fatal):

- Only `*.py` files whose stem does **not** start with `_` are candidates (so `__init__.py`,
  `_helpers.py` are excluded).
- A candidate must define a callable `run`; otherwise skipped with a reason.
- A syntax/import error while loading a candidate is caught, logged, and the script skipped.
- The script's identity is `NAME` (falling back to the filename stem); a duplicate identity is a
  load-time conflict — first wins in deterministic order, the duplicate reported.

### 4.2 Loader (`automation/scripts_loader.py`, new)

```
discover_scripts(*, refresh: bool = False) -> dict[str, ScriptInfo]   # cached per process
load_script(name: str) -> Callable[..., str]                          # strict resolution
script_names() -> list[str]                                           # sorted
reset_cache()                                                         # tests only
```

- `ScriptInfo`: `name`, `path`, `description`, `trigger_hint`, `run` (callable), `load_error` (str|None).
- Loading uses `importlib.util.spec_from_file_location` against an **explicit path inside the folder**
  (never a string from the DB). The name→path mapping comes from the folder scan only.
- `PLAYBOOK_SCRIPTS_DIR` setting (default `BASE_DIR / "automation" / "scripts"`), env-overridable.
- All discovered paths are verified to be inside the resolved scripts dir (defence in depth against a
  symlink/relative surprise); a path escaping the folder is refused.

### 4.3 Executor (`automation/executor.py`)

- **Delete** `_ACTIONS`, `register_action`, `registered_actions`.
- **Move** `_enrichment_probe` → `automation/scripts/enrichment_probe.py` (as `run(**values)`).
- Rewrite `run_python_action`:
  ```python
  path = str(config.get("action_path") or "")
  script = load_script(path)  # raises ActionError if not a discovered script
  output = script(**values)
  return ActionResult(
      output_log=(output if isinstance(output, str) else repr(output))[:MAX_OUTPUT_BYTES], ok=True
  )
  ```
  Error message keeps the "not a discovered script; known: [...]" shape (replacing "registered").
- `execute` dispatch unchanged in shape; `http` path untouched.

### 4.4 Validation (`automation/playbooks.py`)

- `_validate_python`: `action_path` must be a discovered **script name** (else a 400 with the known
  list). Rename user-facing wording from "registered action path" to "script". `ACTIONS` stays
  `("http", "python")`.

### 4.5 API (`automation/views.py`, `core/serializers.py`)

- `/api/v1/playbook/_meta`: the `python` entry replaces `registeredPaths` with
  `"scripts": [{"name": "...", "description": "...", "triggerHint": "..."}, ...]`.
  Keep `actionNames`/`actions`/`triggerEvents` shape otherwise.
- `playbook_json`: **unchanged** (`config` remains inlined).

### 4.6 UI (`ui/views.py`, templates)

- Replace all 5 `registered_actions()` usages with `scripts_loader.script_names()` / `discover_scripts()`.
- `_parse_playbook_config` validates `action_path` against discovered script names.
- `playbook_form.html` / `playbook_detail.html`: the Python-action picker becomes a `<select>` of
  discovered scripts (name + description); show a **read-only source view** of the selected script's
  file (rendered in a `<pre>`/`.body`, escaped) on the detail page.
- Empty state: "No playbook scripts found in `automation/scripts/`." with a hint that scripts are added
  by deploying a file (no upload control).
- Authz: create/edit restricted to `admin`/`superAdmin` (tighten the current `analyst`+ gate).
- All destructive/confirm flows keep the existing `<dialog>` pattern (no native dialogs).

### 4.7 Migration

- Data migration in `automation`: rewrite `Playbook.config["action_path"]` from the legacy dotted path
  `"amalthea.automation.executor.enrichment_probe"` to the bare name `"enrichment_probe"`.
  No-op-safe (only touches rows whose config carries the legacy string).

## 5. Data model / API impact

- **Schema:** none (no new fields; `config` is a JSONField). The dispatch `Playbook` index is untouched.
- **Data migration:** one, rewriting the legacy `action_path` string.
- **API:** `_meta` `python` entry key change (`registeredPaths` → `scripts`); a wire-contract change to
  document in `docs/spec/api.md` + `deviations.md`.
- **Settings:** new `PLAYBOOK_SCRIPTS_DIR`.
- **Management command:** `manage.py list_playbook_scripts` (list + validate; non-zero exit on a
  malformed candidate), wired into `make check`.

## 6. UI / UX

- Playbook form (create/edit): pick action `http` or `python`; `python` → script `<select>` populated
  from discovery; description shown under the select.
- Playbook detail: a "Script" card showing `NAME`, `DESCRIPTION`, file path, and the escaped source.
- Playbook list: optionally a "Script" column (name) — minor.
- Dark theme, keyboard-driven, Font Awesome icons where useful, no new caption text beyond the card
  heading; reuse existing classes (`.card`, `.body`, `.hint`, `.empty`, `.pill`).

## 7. Security

- **Trust boundary:** `PLAYBOOK_SCRIPTS_DIR` is code. Deploy-only; the app never writes to it and never
  offers an upload for it (N1). Documented in ADR-011.
- **Loader safety:** explicit-path import inside the folder; path-escape/symlink check; skip-on-error;
  no import of DB-supplied strings; unchanged "nothing from the row is evaluated".
- **Authz:** create/edit = `admin`/`superAdmin`.
- **Secrets (AGENTS.md §5):** scripts are committed files, covered by the push-time `--tracked` secret
  scan; runbook rule "no secrets in scripts — read env vars".
- **Mandatory `security-auditor` review** of the loader + resolution path before merge; findings
  recorded in `docs/security/`.

## 8. Testing

- `tests/conformance/test_playbook_scripts_loader.py` (new): discovery (only `*.py`, only non-`_`),
  identity/`NAME` handling, skip-on-syntax-error, skip-on-missing-`run`, duplicate-identity conflict,
  path-escape refusal, `PLAYBOOK_SCRIPTS_DIR` override, cache + `reset_cache`.
- Update `tests/conformance/test_automation_executor.py`: drop `register_action`/`registered_actions`
  imports; the "os.system / builtins.eval refused" test now asserts they are **not discovered scripts**;
  the built-in probe test loads the extracted script by name.
- Update `tests/conformance/test_playbook_authoring.py`: `_meta` `python.scripts` list parity.
- UI tests: picker renders discovered scripts; source view escaped; authz (`admin`/`superAdmin` only);
  invalid `action_path` → 400/flash.
- Migration test: a legacy row is rewritten to the bare name.
- **Falsifiability (§7.5):** for each new guard (path-escape refusal, skip-on-error, `action_path`
  validation), prove the test fails when the guard is removed (temporary sabotage, restore, hash-proof).
- Targeted pytest only per user directive; `make check` must be green (incl. the new
  `list_playbook_scripts` validation step).

## 9. Risks

- **R1 (High) — RCE trust boundary.** Executing files from a folder. Mitigation: deploy-only folder,
  no app writes/upload, security review, ADR. Accepted by operator.
- **R2 (Medium) — Reload semantics.** A new script needs a process reload; a stale worker silently
  runs old code. Mitigation: documented; `list_playbook_scripts` shows the on-disk truth; runbook note.
- **R3 (Medium) — Migration completeness.** A row left with the legacy dotted path would fail at run
  time. Mitigation: data migration + a test; `list_playbook_scripts` can report playbooks whose
  `action_path` is unknown.
- **R4 (Low) — Import side effects.** A script with import-time side effects runs on discovery. Mitigation:
  scripts should define metadata + `run` only; documented convention; discovery is lazy/cached.
- **R5 (Low) — Deploy packaging.** In a container build, the folder is baked in; override via
  `PLAYBOOK_SCRIPTS_DIR`/volume. Documented in the runbook.

## 10. Implementation phases

- **P1 (backend):** `scripts_loader.py` + package `automation/scripts/` (+ `__init__.py`) +
  extract `enrichment_probe.py` + `PLAYBOOK_SCRIPTS_DIR`. AC: discovery + load + skip rules; unit tests.
- **P2 (backend):** executor rewrite (delete registry, `run_python_action` via loader). AC: probe runs by
  bare name; unknown/disallowed names refused; falsifiability.
- **P3 (backend):** validation (`playbooks.py`) + API `_meta` + data migration. AC: 400 on unknown
  script; `_meta` serves `scripts`; legacy row migrated.
- **P4 (backend):** `manage.py list_playbook_scripts` + `make check` wiring. AC: command lists/validates;
  non-zero exit on malformed candidate.
- **P5 (frontend):** UI picker + read-only source card + authz tightening + empty state. AC: see §6.
- **P6 (docs):** `docs/spec/automation.md`, `docs/spec/api.md`, `docs/spec/deviations.md`; runbook section.
- **P7 (security):** `security-auditor` review; findings file.
- **P8 (verify):** `verifier` maps every AC; falsifiability evidence.

## 11. Acceptance criteria (feature-level)

- **AC-1** All playbook scripts live only in `automation/scripts/`; the built-in probe is a file there.
- **AC-2** `register_action` / `_ACTIONS` / `registered_actions` no longer exist anywhere (code/tests/docs).
- **AC-3** Executor resolves `action_path` to a discovered script and runs it with interpolation values.
- **AC-4** Unknown / non-discovered / path-escaping `action_path` is refused (400 at write, `ActionError`
  at run).
- **AC-5** Discovery rules hold: `*.py`, non-`_`-prefixed, requires `run`; malformed files are skipped +
  reported, not fatal.
- **AC-6** `/playbook/_meta` advertises discovered scripts; UI picker and validation use the same set.
- **AC-7** UI lists scripts and shows source read-only; **no** upload/edit; empty state explains the folder.
- **AC-8** Create/edit restricted to `admin`/`superAdmin`.
- **AC-9** Legacy `action_path` rows are migrated to the bare script name.
- **AC-10** `manage.py list_playbook_scripts` lists/validates; `make check` green including it.
- **AC-11** "Nothing from a `Playbook` row is imported or evaluated" still holds (test asserts
  `os.system`/`builtins.eval` are not runnable).
- **AC-12** Falsifiability evidence recorded for each new guard.
- **AC-13** `security-auditor` review completed; ADR-011 present.

## 12. Open questions

None — all resolved 2026-10-21 (operator accepted all recommendations):

| # | Question | Decision |
|---|----------|----------|
| 1 | Script contract | `NAME`/`DESCRIPTION`/`TRIGGER_HINT` + `run(**values) -> str`; `action_path` = script name |
| 2 | Legacy rows | Data migration to the bare name (clean cut) |
| 3 | "Active playbook" gating | Discovery DB-agnostic; existing dispatch gating stands |
| 4 | Discovery timing | Lazy, cached per process; `list_playbook_scripts`; reload to apply |
| 5 | Folder override | `PLAYBOOK_SCRIPTS_DIR`, env-overridable |
| 6 | Filename rules | `*.py`, non-`_`-prefixed, requires `run`; skip+report on error |
| 7 | Secrets | No secrets in scripts; read env vars; secret scan covers the folder |
| 8 | Registry coexistence | None — registry removed entirely |
| 9 | Built-ins | Extracted to files in the folder |

## 13. Implementation record

### P1–P4 Implementation (2026-10-21)

**Files added:**
- `automation/scripts/__init__.py` (2 lines) — package marker
- `automation/scripts/enrichment_probe.py` (24 lines) — extracted built-in probe
- `automation/scripts_loader.py` (246 lines) — discovery/loading logic
- `automation/management/__init__.py`, `automation/management/commands/__init__.py` — package markers
- `automation/management/commands/list_playbook_scripts.py` (138 lines) — management command
- `automation/migrations/0007_rewrite_legacy_action_path.py` (45 lines) — data migration
- `tests/conformance/test_playbook_scripts_loader.py` (407 lines) — loader tests

**Files modified:**
- `amalthea/settings/base.py` (+8 lines) — `PLAYBOOK_SCRIPTS_DIR` setting
- `automation/executor.py` (389 lines, rewritten) — removed registry, uses `scripts_loader.load_script`
- `automation/playbooks.py` (98 lines) — validation against `script_names()`
- `automation/views.py` (310 lines) — `_meta` returns `scripts` instead of `registeredPaths`
- `automation/migrations/0005_seed_playbook.py` — seed uses bare name `enrichment_probe`
- `automation/migrations/0006_seed_example_playbook.py` — seed uses bare name
- `ui/views.py` (5507 lines) — all `registered_actions()` replaced with `script_names()`/`discover_scripts()`
- `tests/conformance/test_automation_executor.py` — updated to use script names, removed registry imports
- `tests/conformance/test_playbook_authoring.py` — updated all `action_path` values to bare name
- `Makefile` — added `playbook scripts` check step

**Registry removal verified:** `grep -rn "register_action\|registered_actions\|_ACTIONS" --include="*.py" automation/ ui/ core/` returns nothing.

**Tests:** All 73 targeted tests pass (23 loader + 27 executor + 23 authoring).

**Falsifiability proofs:**
1. **Path-escape guard** — Removed `_is_inside_scripts_dir` check → symlink outside scripts_dir loads as valid script (test fails).
2. **Skip-on-error** — Removed try/except in `_discover_once` → SyntaxError propagates and crashes discovery (test fails).
3. **action_path validation** — Removed `script_names()` check in `_validate_python` → unknown script accepted (test fails).
All original files restored with matching SHA256 hashes.

**make check:** Green (ruff, mypy, django check, migrations, css-check, list_playbook_scripts).

**Deviations from plan:** None — implementation follows approved plan exactly.

### P6 documentation + review cleanup (2026-10-21, planner)

Independent review of P1–P4 found the code correct (registry gone, loader safe, probe behaviour
byte-identical, migration reversible, `_meta`/validation/executor correct) but the **spec docs were
not yet updated** and one test function had a stale name. Planner closed those:

- `docs/spec/automation.md` — `python` validation + executor `run_python_action` rewritten for the
  loader; "registered no-network action" → discovered `enrichment_probe` script; evidence list gains
  `test_playbook_scripts_loader.py`; new **"Playbook scripts folder (runbook)"** section (how to add a
  script + invariants).
- `docs/spec/api.md` — `python` `action_path` validation + `_meta` shape (`scripts[]`, not
  `registeredPaths`).
- `docs/spec/deviations.md` — P12-2 `_meta` wording.
- `tests/conformance/test_playbook_authoring.py` — renamed
  `test_playbook_meta_registered_actions_match_executor` → `test_playbook_meta_scripts_match_loader`.

Verified after cleanup: `grep` for the three registry symbols across `automation/ ui/ core/ tests/`
and `docs/spec/` returns nothing; 73/73 targeted tests pass; `make check` green.

### P5 (UI) Implementation (2026-10-21, django-frontend)

**Files changed:**
- `ui/views.py` (+129/-31) — uses `discover_scripts()`/`script_names()`, passes richer `scripts` (with metadata + source via `_get_script_source`), tightens `playbook_create` GET+POST and both `playbook_detail` POSTs to `admin`/`superAdmin` only, adds `_get_script_source` helper (uses `inspect.getsource`, never executes).
- `ui/templates/ui/playbook_form.html` (+18/-2) — Python picker shows discovered scripts with name + description; label "Script"; empty state explains folder contract (deploy-only, no upload); pre-selects `action_path`.
- `ui/templates/ui/playbook_detail.html` (+34) — read-only "Script" card (NAME, DESCRIPTION, FILE PATH, TRIGGER HINT, escaped source in `<pre>`); missing/errored script warning with load error and folder guidance.
- `ui/templates/ui/playbook_list.html` (+6) — minor adjustments for new structure.

**Files added:**
- `tests/conformance/test_playbook_script_ui.py` (497 lines) — form rendering/pre-selection, analyst/admin/superAdmin authz matrix (404 for analyst create/edit), source escaping, empty-state with `override_settings` + `reset_cache()`.

**Grep proof:** `grep -rn "registered_paths\|register_action\|registered_actions" ui/ tests/` returns nothing.

**Tests:** 105 targeted tests pass (`test_playbook_script_ui.py` + `test_playbook_authoring.py` + `test_settings_ui.py`).

**Falsifiability proofs (verified):**
1. Authz reverted to allow `analyst` → analyst create GET/POST and detail POST fail to 404 (tests fail); restored and pass.
2. Source rendered via `|safe` → if a script contained `<` HTML it would not be escaped (mechanism validated; enrichment probe has no HTML and template uses auto-escaping).

**make check:** Green (including `css-check` and `list_playbook_scripts`).
**Deviations from plan:** None — implementation follows approved plan exactly.

