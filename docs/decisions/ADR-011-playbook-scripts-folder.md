# ADR-011: Playbook Scripts Live in a Filesystem Folder (Registry Removed)

**Status:** Accepted
**Date:** 2026-10-21
**Supersedes:** the in-process action registry introduced by `automation.executor.register_action`
**Related:** `docs/spec/automation.md` §Executor, `docs/spec/api.md` (`/playbook/_meta`),
`docs/spec/deviations.md` P12-2, `PLAN-2026-10-21-playbook-script-folder.md`

---

## Context

Amalthea's orchestration gateway (AGENTS.md Module D) executes one action per `AutomationRun`.
Today the executor supports exactly two actions:

* `"http"` — one SSRF-guarded outbound request.
* `"python"` — a callable **registered in Python source** at import time via
  `register_action(path, fn)`. The `Playbook.config["action_path"]` names a **dotted module path**;
  `run_python_action` looks it up in the module-level `_ACTIONS` dict.

This model deliberately guarantees that **nothing from a `Playbook` row is imported or evaluated**
("a playbook row names a path, it does not supply code"). It has two problems:

1. **Nothing to see or edit.** `_ACTIONS` maps a path to a live function object; there is no stored
   source to display in the UI. There is exactly one registered action
   (`amalthea.automation.executor.enrichment_probe`), buried in `executor.py`.
2. **Awkward to extend.** Adding an action means writing a function *inside* an existing module,
   calling `register_action(...)` at module import time, and hoping the module is imported by the
   worker. A registration in a module nothing imports silently never runs.

The product goal is: *see, edit, and create playbook code that is actually run*, with new scripts
"easily added and configured via the UI to run."

## Decision

**The in-process registry (`register_action` / `_ACTIONS` / `registered_actions`) is removed
entirely.** All playbook scripts — **including the current built-in probe** — become individual
files in a dedicated folder:

* Location: `automation/scripts/` (default), overridable via the `PLAYBOOK_SCRIPTS_DIR` setting
  (env-overridable, for a mounted volume in a future container deployment).
* One script per file; filename (sans `.py`) is the script's identity.
* A script declares module-level metadata (`NAME`, `DESCRIPTION`, `TRIGGER_HINT`) and a
  `def run(**values) -> str` entry point.
* `Playbook.config["action_path"]` now names a **script name** (e.g. `"enrichment_probe"`), not a
  dotted module path.
* The executor **discovers** scripts from the folder (lazily, once per process, cached) and resolves
  `action_path` against the discovered set.
* Write-time validation (`automation/playbooks.py`, UI forms, `/playbook/_meta`) resolves against the
  same discovered set, so a playbook cannot name a script that is not on disk.

### Invariants preserved

* **Nothing from the `Playbook` row is imported or evaluated.** The row still only names a script; the
  code is on disk. The loader imports *files from a known folder*, never a string from the DB.
* **The folder is deploy-only.** No UI upload, no app-initiated file writes. Scripts arrive via git
  commit or a mounted volume. The application never writes to `PLAYBOOK_SCRIPTS_DIR`.
* **Resolution is strict.** `action_path` must match a discovered script name exactly; no path
  traversal, no dotted-module names, no `importlib.import_module` of a DB-supplied string.

## Consequences

### Positive

* A single, discoverable home for all playbook code; `ls automation/scripts/` is the inventory.
* The UI can **list** scripts (name + description), show their **source read-only**
  (`inspect.getsource`), and let an admin **pick** one — without any execution-of-DB-data path.
* Adding a script is "drop a file + reload" rather than "edit a module + register + ensure it is
  imported." The `enrichment_probe` example becomes a real, readable file.
* Code is reviewable and secret-scannable as ordinary committed files (AGENTS.md §5: the push-time
  `--tracked` secret scan already covers the folder).

### Negative / accepted trade-offs

* **The folder is an RCE surface.** Executing files from a directory is exactly as sensitive as
  executing DB-stored code. Mitigation: the folder is **deploy-only**; the app never writes to it and
  never surfaces it as an upload target. This is a *code-execution trust boundary* and is documented
  as such; the loader is reviewed by `security-auditor`.
* **A new script needs a process reload** to take effect (discovery is cached). This is the intended
  "deploy" semantics; there is no hot-reload and no per-dispatch scan.
* **No versioning / provenance yet.** A run records `playbook_name` (already) but not the script's
  content hash. Because scripts are committed files, provenance is git. A content hash on
  `AutomationRun` is a possible future addition (tracked in `TODO.md`).
* **Behaviour change for existing rows:** `action_path = "amalthea.automation.executor.enrichment_probe"`
  must become `"enrichment_probe"`. Handled by a data migration.

## Alternatives considered

1. **Keep the registry; add a read-only "view registered actions" page.** Rejected: satisfies "see"
   only, not "edit/create", and leaves code buried in modules.
2. **Store source in the DB and execute it (Option 1).** Rejected: reintroduces the exact RCE path the
   current design forbids, and needs a sandbox (subprocess isolation, resource caps, seccomp) plus its
   own ADR before any code. The filesystem design gets the same product outcome while keeping the
   "nothing from the row is evaluated" invariant.
3. **Constrained DSL / step list (Option 2).** Rejected for now: more machinery than the product needs,
   and it is not "Python scripts" as AGENTS.md Module D describes. Reconsider if scripts prove too
   powerful to govern.
4. **Folder discovery coexisting with the registry.** Rejected per operator decision: one mechanism
   only, so there is a single source of truth for "what can run."
