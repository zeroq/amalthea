# PLAN — CI workflow + hash-pinned requirements

Date: 2026-10-07 · Status: **implemented & verified** (verifier 8/8 Met; recorded in master plan §13 Phase 11) · Owner: planner

## Summary

Close TODO **§4.3** (hash-pin `requirements/*.txt`) and **§8.6** (guard the CI invocation itself)
in one wave:

1. **Hash-pin the verified closure without moving a version.** The current 53+45 exact pins are the
   tested truth. The hashing pass must add all-platform `--hash=sha256:...` entries and change
   **zero** `name==version` facts. Platform-correct hashes come from `pip-compile --generate-hashes`
   (queries the PyPI JSON API, emits hashes for *every* distribution file, not just the local wheel —
   `pip hash` on the macOS `.venv` would produce mac-only hashes and break Linux CI).
2. **A CI workflow (GitHub Actions)** that runs the same Definition of Done on a remote runner:
   ruff/mypy/django-check/migration-sync/pytest (SQLite) via `make check`, the Postgres conformance
   suite (`amalthea.settings.test_pg`), the coverage gate (`fail_under=80`), `pip-audit`, and a
   **lock-currentness check** so hand-edited requirements fail CI, not just review.

## Goals

- G1 — Every package line in `requirements/base.txt` and `requirements/dev.txt` carries
  `--hash=sha256:...` for all platforms (pip install hash-checking mode is then automatic).
- G2 — Version identity is preserved: `name==version` multiset before vs after is identical, except
  the deliberate addition of `pip-tools` (the hasher itself) + its new transitive deps to `dev.txt`.
- G3 — `make lock` regenerates deterministically (run twice → identical files) and never drifts.
- G4 — `.github/workflows/ci.yml` pins the exact gate commands from the Makefile/repo, runs the
  SQLite + Postgres + coverage + audit gates, with a Postgres service container mirroring
  `docker-compose.yml` credentials.
- G5 — Docs stay truthful: README/pyproject comments updated to the pip-compile mechanism, deviations
  recorded in the master plan §13, TODO items closed, COMPLETED.md appended.

## Non-goals

- **No version upgrades** (pin-ages stay as committed; hash-only pass).
- **No `pyproject.toml` dependency declarations** — the existing "requirements are the single source
  of truth" decision stands (pyproject comment updated, not inverted).
- **No GitHub repository creation / remote push** (TODO §4.5 stays deferred — needs user decisions:
  org, visibility, LICENSE).
- **No migration of `make coverage` to blocking locally** — §1.18 keeps local `make check` the DoD and
  coverage a separate signal; only CI treats `fail_under=80` as a hard gate (see P11-2).
- **No smoke/readyz live-CI checks** (AC1.3/AC1.4 stay manual — they need a running worker + Redis).

## Architecture / mechanism

### Lock flow (rewritten `scripts/lock.sh`)

- Inputs to `pip-compile` are the **existing pins themselves** (self-referential), not new `.in`
  files: `base.txt` (53 exact pins) and `dev.txt`-minus-its-`-r base.txt`-line (dev-only pins),
  constrained by `-c base.txt` so base versions cannot move relative to dev resolution.
- `pip-compile --generate-hashes` resolves fully-pinned input → no version choice is available → the
  only possible change is hash/comment/formatting. Output format differs from today's sorted bare
  lines (pip-compile groups and annotates); ordering is deterministic.
- `dev.txt` is reassembled as: header comment + `-r base.txt` + hashed dev-only body, preserving the
  documented "one `pip install -r dev.txt` installs everything" contract and the runtime/dev
  disjointness invariant.
- **Closure invariant check (the real guard):** before the pass, parse both files into
  `(name==version)` multisets; after, re-parse the *package* lines (strip line-continuations and
  hashes) and assert equality, with an approved-diff allowlist for the pip-tools addition. A failing
  invariant aborts `make lock` with output — no silent drift.
- `--check` mode: run the same computation and exit non-zero if the committed files differ from what
  the pass would produce. CI invokes it to catch hand-edited requirements.

### What gets added to dev.txt

`pip-tools==7.6.2` (the hasher) plus its new transitive deps (`build`, `pyproject-hooks`, `wheel` —
exact versions from the installed closure). Everything else stays byte-identical in version terms.

### CI workflow (`.github/workflows/ci.yml`)

- Trigger: `push` + `pull_request` on `main`.
- Job `gate` on `ubuntu-latest`, Python 3.14 (local parity), `permissions: contents: read`.
- Service container: `postgres:16-alpine`, `POSTGRES_USER/PASSWORD/DB=amalthea` (matches
  `docker-compose.yml`), exposed on `localhost:5432`; `test_pg.py` connects via its existing
  `POSTGRES_*` env defaults.
- Steps (each command is exactly a locally-runnable, locally-verified command):
  1. checkout · setup-python (pip cache) · `pip install --require-hashes -r requirements/dev.txt`
  2. `make check` → SQLite gate (733/3 today)
  3. `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg .venv/bin/python -m pytest -q`
     → Postgres gate (712/24 today)
  4. `.venv/bin/python -m pytest -q --cov=. --cov-fail-under=80` → coverage gate (`fail_under=80`
     in pyproject; the explicit `--cov-fail-under` duplicates the pyproject floor so the CI step reads
     its floor without opening the config)
  5. `scripts/lock.sh --check` → lock-currentness gate
  6. `.venv/bin/pip-audit` → supply-chain gate (0 CVEs today)
- No Redis service: the pytest suite uses in-memory Channels + eager Celery by design; the
  "Redis-unreachable → 503" path is itself under test.

## Deviations to record in master plan §13 (Phase 11)

- **P11-1** — Lock mechanism changes from freeze-subtraction (`pip freeze` of a throwaway runtime
  venv) to `pip-compile --generate-hashes` over the existing exact pins. Same source of truth
  (`requirements/*.txt`), same two-file split, same versions; different generator (pip-tools) and
  added hashes. Supersedes the "byte-reproducible via freeze" wording in TODO §4.3.
- **P11-2** — CI treats coverage as a hard gate (`fail_under=80`); local `make coverage` stays
  non-blocking (`|| true`) per §1.18. Recorded because it changes the meaning of "coverage" between
  local and CI, and because TODO §2.1 already declares the gate met (83.10%).
- **P11-3** — One-phase-only: no `.in`-file top-level declaration layer introduced (as pip-tools
  purists would). Chosen so the tested closure remains the only source of truth; revisited if top-level
  ranges ever become desired.
- **P11-4** — The workflow can only be *runner-validated on first push* (TODO §4.5 stays deferred; the
  repo has no remote). Every step is a locally-verified invocation (AC8), but a fresh
  `ubuntu-latest` runner's `pip-compile` output must still prove byte-identical to the mac-generated
  lock (marker/ordering stability) — that is exactly what the `lock.sh --check` step asserts on push.
- **P11-5** — `--no-strip-extras` added to both `pip-compile` invocations: pip-compile 7.6.2 warns
  that extras-stripping becomes the default in 8.0, and the warning breaks the script's "quiet then
  tail" output contract. Explicitly pinning the *current* behaviour makes the lock deterministic
  against a future pip-tools upgrade (extra-forms like `twisted[tls]` stay intact).
- **P11-6** — Real counts differ slightly from the plan's estimate ("53+45"), which had not been
  re-counted since the Phase 10 wave: committed `base.txt` has **49** runtime pins, `dev.txt` has
  **48** dev-only pins (45 pre-existing + 3 that pip-compile's dev resolution materialises under the
  exact-pin constraint), and the final dev closure is **54** = 48 + 6 bootstrap (`build`,
  `pyproject-hooks`, `pip-tools`, `wheel`, `pip`, `setuptools`). Closure-diff AC4 is computed against
  the **actual** git-HEAD snapshot, so the numbers are self-verifying, not hand-maintained.

## DB schema / API / UI

None. Devops/tooling wave: zero Django code, zero migrations, zero model/endpoint surface. The CI
workflow *runs* the existing gates and changes nothing about them.

## Phases, tasks, acceptance criteria

### Task 1 — Bootstrap pip-tools and add it (plus transitives) to dev.txt

- [x] T1.1 Install `pip-tools==7.6.2` into `.venv`; record `pip freeze` facts for pip-tools' own
      transitive deps.
- [x] T1.2 Append the new pins to `requirements/dev.txt` (pip-tools + `build`/`pyproject-hooks`/
      `wheel` at exact versions).
- **AC1** — `pip show pip-tools` → 7.6.2; dev.txt contains exactly the old 45 dev pins + the new
  tool pins; base.txt untouched at this step. **MET** — pip-tools 7.6.2 in `.venv`; first lock pass
  added exactly the 6 bootstrap pins to dev.txt (48 → 54; see P11-6).

### Task 2 — Rewrite `scripts/lock.sh` with hashing + closure invariant + `--check`

- [x] T2.1 Two-pass `pip-compile --generate-hashes` (self-input + `-c base.txt` for dev); reassemble
      dev.txt header (`-r base.txt` line) around the compiled dev body.
- [x] T2.2 Closure-invariant comparison (parse → multiset → assert, allowlist for pip-tools add).
- [x] T2.3 `--check` mode (idempotence/currentness probe).
- [x] T2.4 `make lock` target rewired; README/pyproject references updated (G5).
- **AC2** — `make lock` twice → `git diff` is empty on the second run. **MET** — three consecutive
  byte-identical runs (see P11-5 for the temp-path normalisation that makes this hold).
- **AC3** — Every package line carries ≥1 `--hash=sha256:...`; the `-r base.txt` contract line is
  intact at the top of dev.txt; runtime/dev name-sets remain disjoint. **MET** — base: 49 pkgs / 710
  hash lines, dev: 54 pkgs / 878 hash lines, zero hash-less pins, zero name overlap.
- **AC4** — `name==version` multiset before vs after is identical except the T1.2 allowlist.
  **MET** — verified with the standalone parser (replicates `parse_closure`) against the git-HEAD
  snapshot: diff is exactly the 6 bootstrap entries.
- **AC5** — `pip install --dry-run --require-hashes -r requirements/dev.txt` exits 0 (hash
  verification over the resolved set). **MET**.

### Task 3 — Verify the gates still hold on the hashed closure

- [x] T3.1 `make check` on SQLite (expect 733 passed / 3 skipped).
- [x] T3.2 Postgres suite with containers up (expect 712 / 24).
- [x] T3.3 Coverage gate run (expect pass at 83.10% or above).
- **AC6** — SQLite 733/3, Postgres 712/24, coverage ≥80, all as before the hash pass (no behavioral
  delta from hashing). **MET** — SQLite **733 passed / 3 skipped**, Postgres **712 passed / 24
  skipped**, coverage **83.47%** (gate ≥80), `pip-audit` 0 CVEs.

### Task 4 — Write `.github/workflows/ci.yml`

- [x] T4.1 Workflow as specified in Architecture; exact gate commands inline; postgres service
      container; `permissions: contents: read`.
- [x] T4.2 Remove the pre-commit "skip docs/config" carve-out ambiguity for `.yml` (already matched
      by the hook, verify).
- **AC7** — `.github/workflows/ci.yml` exists, parses as YAML, and every step command matches a
  locally-verified invocation; `.yml` is inside the pre-commit hook's code-scope regex (yes by
  construction — `\.(py|toml|yml|yaml)$`). **MET** — 8 steps, YAML-validated via PyYAML;
  `pre-commit.sh:22` regex covers `.yml`.
- **AC8** — Locally executable equivalents of the six CI steps all exit 0 on this machine
  (check, test_pg, cov, lock-check, audit). Actual runner-behaviour is validated on first push
  (P11-4 note in plan). **MET** — all six run green locally; `lock --check` exit-0 pending commit
  (its semantics are "differs from git HEAD", correct for CI).

### Task 5 — Record & close

- [x] T5.1 Master plan §13: Phase 11 record with P11-1..P11-6; TODO §4.3/§8.6 closed with figures;
      COMPLETED.md appended.
- [x] T5.2 Verifier pass over AC1–AC8 (independent, read-only) → `VERIFY-2026-10-07-phase11.md`.
- **AC9** — Verifier report present; all ACs Met or explicitly Deviated with a reason. **MET** —
  `docs/planning/VERIFY-2026-10-07-phase11.md`: **8/8 Met**, 0 Deviated, 0 Not Met, no blockers.

## Security

- Supply chain: all-platform hash verification on every install (CI `--require-hashes`); the lock
  file itself is guarded by the CI `--check` step, so a hand-edited or accidentally-drifted
  requirement fails CI even if review misses it.
- CI hygiene: `permissions: contents: read` (no write token); no secrets in the workflow (Postgres
  password is the same local dev default `amalthea` used in `docker-compose.yml`; no prod secrets
  involved). Pinned action versions (tags) — action-SHA-pinning recorded as a later hardening note.
- No new attack surface: hashing adds no code paths in the application.

## Testing

- The hashing pass itself is tested by AC2/AC4/AC5 (determinism, closure identity, hash
  verification).
- The gates are re-run post-wave (AC6) so the wave can prove it changed nothing behavioural.
- CI steps are individually verified locally (AC8); full runner execution pending first push (§4.5).

## Risks

| Risk | Mitigation |
|---|---|
| `pip-compile --generate-hashes` slow (97 packages × PyPI JSON) | generous tool timeout; increases with slack noted in plan; hash pass is a one-time + lock-time cost |
| pip-compile output format surprises (e.g. emits `-c` line, reorders) | closure-invariant comparison is format-agnostic; reassembly is explicit; validated by AC2/AC4 |
| Hash mode breaks an install pip-tools didn't anticipate (extras normalization) | `--dry-run --require-hashes` gate (AC5) + `make check` after install (AC6) |
| CI reveals Postgres-only flakiness on a fresh runner | same container image & credentials as `docker-compose.yml`; Postgres suite already green twice on local PG16 |
| Workflow unvalidated until first push (§4.5 deferred) | every step is a locally-verified command (AC8); YAML parsed; failure mode is loud, not silent |
| `.env`-style secrets accidentally required by CI | workflow uses dev/test env only; grep for `SECRET_KEY` before commit |

## Open questions

1. Action version tags vs commit-SHA pinning in `ci.yml` — tags chosen for readability; SHA-pinning
   recorded as a post-GitHub-promotion hardening item (§4.5 step 5 discussion). No user decision
   needed for this wave.
2. Whether to also hash-pin `pip` itself in dev.txt — out of scope (pip is the installer, not a
   requirement); noted in COMPLETED.md.