# Plan — Case ledger modularization (2026-11-27; approved as §13.4)

**Status:** Closed 2026-11-27 — implementation complete; user full pytest green; see numbering note
**Numbering note:** This plan was approved as §13.4, but `TODO.md` §13.4 is
**Split `alerts/views.py` into an `alerts/views/` package**. The ledger split is recorded in
`COMPLETED.md` as an extra hygiene item, and the real §13.4 remains open.
**Owner:** project planner (`opencode/big-pickle`)
**Implementation target:** `django-backend`
**Related:** `TODO.md` §13.4; `AGENTS.md`; brief T1 choke-point guard in `tests/conformance/test_realtime.py`

## Summary

Split `cases/ledger.py` into a package with a write-side module and a read-side module while keeping
`cases.ledger.append_timeline_event` and `cases.ledger.events_after` as stable re-exports. This is a
move-only refactor with two required guard updates: the realtime choke-point scan must allow the new
writer path, and the org-refresh monkeypatch must target the module that now owns the publisher name.

Assumption carried forward: §13.2 and §13.3 are implemented but still uncommitted and awaiting the
user-owned full pytest gate. §13.4 must preserve those changes and must not fold them into its own
eventual commit.

## Goals

- Separate the ledger write path from the keyset read path.
- Keep every existing `from cases.ledger import ...` call site working unchanged.
- Keep `append_timeline_event` as the single `TimelineEvent.objects.create` choke point.
- Keep `transaction.on_commit` publish ordering, event vocabulary, org refresh reason map, and
  `(date, id)` keyset semantics byte-for-byte inside function bodies.
- Update the two tests whose literal targets encode the old module layout.
- Record deviations in this plan before merge.

## Non-goals

- No API endpoint, route, template, schema, or migration change.
- No new event type, publish reason, serializer shape, or keyset behaviour.
- No placeholder module for helpers that do not exist yet.
- No change to `cases/models.py`, `core/serializers.py`, or `realtime/publisher.py`.
- No full pytest run by the implementation agent.
- No commit or push until explicitly requested after user acceptance.
- No subagents.

## Architecture

Stack remains Django 5.x + DRF, PostgreSQL, Redis/Celery, Channels, and server-rendered HTMX UI per
`AGENTS.md`. This change is a Python module-layout refactor only.

Option chosen: **`cases/ledger/` package with compatibility `__init__.py`**. A package is required
because the TODO asks for separation of concerns and because the writer path must be recognizable by
the choke-point scan at a stable file location. Flat sibling modules with a `cases/ledger.py` shim
would also work, but a package makes the write/read split explicit and matches the §13.1/§13.2
package precedent.

### Baseline measured 2026-11-27

- `cases/ledger.py`: 150 lines, 2 top-level functions, no constants.
- `append_timeline_event`: writer + case-room publish + owner-org refresh publish.
- `events_after`: keyset read path ordered by `(date, id)`.
- External imports from `cases.ledger` are unchanged call sites in 20+ app/test files.
- One monkeypatch encodes the module global: `cases.ledger.publish_org_event` in
  `tests/conformance/test_realtime_org.py:189`.
- One static guard encodes the writer file: `LEDGER = REPO_ROOT / "cases" / "ledger.py"` in
  `tests/conformance/test_realtime.py:72`.
- No phase-capture helper functions exist yet despite the TODO wording.

## DB schema

No change. `TimelineEvent` remains the same model. No migration is required.

## API

No API contract change. `append_timeline_event` and `events_after` keep their signatures and return
types. All existing `from cases.ledger import ...` statements remain valid via package re-exports.

## UI

No template or markup change. Live case-room and org-room event payloads stay identical. The dark,
keyboard-driven UI contract is unaffected.

## Module ownership

| Module | Owned names |
| --- | --- |
| `cases/ledger/event_log.py` | `append_timeline_event` (the only `TimelineEvent.objects.create` writer) |
| `cases/ledger/read.py` | `events_after` |
| `cases/ledger/__init__.py` | Compatibility re-exports only; no implementations and no `__all__` |

The original module docstring's contract explanation moves to `cases/ledger/__init__.py`. Each
submodule gets a short focused docstring; function docstrings and bodies remain unchanged.

## Required test updates

These are required because the tests encode the old file layout, not because behaviour changes.

### `tests/conformance/test_realtime.py`

- Change `LEDGER` from `REPO_ROOT / "cases" / "ledger.py"` to
  `REPO_ROOT / "cases" / "ledger" / "event_log.py"`.
- Update comments/docstrings that say the literal is forbidden outside `cases/ledger.py` so they say
  `cases/ledger/event_log.py`.
- In `test_the_scan_would_fail_a_call_site_reverted_to_a_direct_create`, create
  `tmp_path / "cases" / "ledger"` with `parents=True` before writing the synthetic ledger file.
- AC: the positive half still asserts `DIRECT_CREATE` exists in the allowed writer file.
- AC: the negative half still fails on direct creates in `alerts`, `ui`, and other scanned apps.

### `tests/conformance/test_realtime_org.py`

- Change the monkeypatch target from `cases.ledger.publish_org_event` to
  `cases.ledger.event_log.publish_org_event`.
- AC: `test_ledger_emits_owner_org_refresh_after_commit` still passes with the same payload asserts.

## Phases and acceptance criteria

### Phase 0 — Baseline and guardrails

- Confirm §13.2/§13.3 work remains untouched.
- Capture AST snapshot of `HEAD:cases/ledger.py` (2 functions, no constants).
- AC: plan approved before implementation.

### Phase 1 — Create package and move functions

- Create `cases/ledger/`; move the original file to `cases/ledger/__init__.py`.
- Add `event_log.py` with `append_timeline_event` and `read.py` with `events_after`.
- Reduce `__init__.py` to re-exports plus the contract docstring.
- AC: function bodies and signatures are AST-identical to `HEAD:cases/ledger.py`.
- AC: the literal `TimelineEvent.objects.create(` appears only in `cases/ledger/event_log.py` within
  scanned apps.

### Phase 2 — Update guard tests

- Apply the two required test updates above.
- AC: no other test edits are made.
- AC: both guard tests pass.

### Phase 3 — Structural and static verification

- AST compare old vs new functions; import smoke all 20+ external call sites indirectly via Django.
- Run `ruff check --fix`, `ruff format`, `make check`, `git diff --check`, and secret scan.
- AC: no missing, extra, changed, or signature-drifted functions.
- AC: `from cases.ledger import append_timeline_event, events_after` works.
- AC: static checks clean; no trailing whitespace.

### Phase 4 — Targeted behavioural verification and closeout prep

- Run the targeted suites listed below. Do not run full pytest.
- AC: targeted suites green.
- AC: no further deviations discovered; if found, record them here before merge.
- AC: report results and leave changes uncommitted pending user-run full pytest and acceptance.

## Security / invariants

- Preserve `transaction.on_commit` for both case-room and org-room publishes; no bare publish.
- Preserve `partial(publish_case_event, str(case.id), kind, {"event": timeline_event_json(event)})`.
- Preserve the exact `reason_map` and `refresh` payload keys.
- Preserve the keyset query shape (`Q(date__gte=anchor.date) & ~Q(date=anchor.date, id__lte=anchor.id)`)
  because it is indexed and perf-reviewed.
- Preserve tenant scoping: anchor lookup stays inside the case's own queryset.
- Do not weaken the choke-point scan to allow arbitrary files.
- No secrets in code, tests, docs, or logs.

## Testing

Targeted suites proposed for Phase 4:

```bash
DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/pytest -q \
  tests/conformance/test_realtime.py \
  tests/conformance/test_realtime_org.py \
  tests/conformance/test_ledger_keyset.py \
  tests/conformance/test_t1_surface.py \
  tests/conformance/test_t2_p4_surface.py \
  tests/conformance/test_ui_loop.py \
  tests/conformance/test_ui_pagination.py \
  tests/conformance/test_case_export_import.py \
  tests/conformance/test_viewer_reads.py \
  tests/conformance/test_containment_phase_capture.py
```

Structural checks:

```bash
.venv/bin/python - <<'PY'
import ast, subprocess
from pathlib import Path

old = ast.parse(subprocess.check_output(["git", "show", "HEAD:cases/ledger.py"], text=True))
def funcs(tree):
    return {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
old_funcs = funcs(old)
new_funcs = {}
for p in (Path("cases/ledger/event_log.py"), Path("cases/ledger/read.py")):
    new_funcs.update(funcs(ast.parse(p.read_text())))
print("old", len(old_funcs), "new", len(new_funcs))
print("missing", sorted(set(old_funcs) - set(new_funcs)))
print("changed", sorted(k for k in old_funcs if old_funcs[k] != new_funcs.get(k)))
PY

DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/python -c \
  'import django; django.setup(); from cases.ledger import append_timeline_event, events_after; print("imports ok")'
```

## Risks

- **R-13.4-1:** Choke-point guard could be accidentally weakened. Mitigation: allow exactly one new
  file and keep both positive and negative assertions.
- **R-13.4-2:** Monkeypatch could silently stop intercepting the publisher. Mitigation: update target
  to the owning module and run `test_realtime_org.py`.
- **R-13.4-3:** Package `__init__.py` re-exports can be pruned by ruff and rejected by mypy as
  implicit. Mitigation: define `__all__ = ["append_timeline_event", "events_after"]`.
- **R-13.4-4:** ~~User-owned full pytest remains unrun for §13.2–§13.4.~~ Resolved: user supplied a
  green full-pytest log covering this work.

## Deviations

- **D-13.4-1:** Use a `cases/ledger/` package instead of flat sibling modules with a shim.
  Rationale: stable writer path for the choke-point guard and consistency with §13.1/§13.2.
- **D-13.4-2:** No `phase_capture.py` module is created because no phase-capture helpers exist today.
  Rationale: avoid placeholder files.
- **D-13.4-3:** `tests/conformance/test_realtime.py` is edited to point the choke-point guard at
  `cases/ledger/event_log.py`.
- **D-13.4-4:** `tests/conformance/test_realtime_org.py` is edited to patch
  `cases.ledger.event_log.publish_org_event`.
- **D-13.4-5:** `__all__ = ["append_timeline_event", "events_after"]` is added to the package
  `__init__.py`. Rationale: mypy rejects implicit package re-exports with `attr-defined`; the same
  pattern was used for `cases/views/__init__.py` in §13.1.
- **D-13.4-6:** No full pytest, commit, or push.

## Open questions

None blocking. Defaults above are proposed for approval.

## Verification record

Recorded 2026-11-27 after implementation. User-provided full pytest log `/tmp/amalthea-full-pytest.log`
is green: **4444 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.92s**.

- Structural AST comparison against `HEAD:cases/ledger.py`: 2 functions, no missing or changed
  definitions.
- Import smoke: `from cases.ledger import append_timeline_event, events_after` succeeds with Django
  initialized.
- Direct-create scan: `TimelineEvent.objects.create(` occurs only in
  `cases/ledger/event_log.py` across `cases`, `ui`, `alerts`, and `automation`.
- Only the two planned guard tests were edited; no other test edits were needed.
- Targeted suites: **246 passed** in 319.19s across the 10 listed conformance files.
- Post-`__all__` guard recheck: **3 passed** in 49.19s.
- `make check`: passed (ruff, format, mypy over 170 files, Django checks, migration sync, CSS drift,
  playbook scripts).
- `scripts/secret-scan.sh --tracked`: clean over 985 tracked files.
- `git diff --check`: clean; no trailing whitespace in new package, edited tests, or plan/brief docs.
- No deviation beyond D-13.4-1 through D-13.4-6 was discovered.

## Approval gate

Approved 2026-11-27. Implementation brief written to
`docs/planning/IB-2026-11-27-ledger-modularization.md`. Phases 0–4 completed with D-13.4-1 through
D-13.4-6 recorded. Do not commit or push.
