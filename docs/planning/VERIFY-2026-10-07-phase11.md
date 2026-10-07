# Verification Report: Phase 11 — CI workflow + hash-pinned requirements

Plan: `docs/planning/PLAN-2026-10-07-ci-hashed-requirements.md`
Verified: 2026-10-07 13:50 UTC
State: working tree + untracked `.github/` (implementation uncommitted, as declared)
**Status: Pass — VERDICT: all ACs Met (AC1–AC8); no Deviated, no Not Met.**

## Summary

- Total ACs: 8 (AC1–AC8; AC9 is the report-presence criterion — satisfied by this file)
- Met: 8
- Not Met: 0
- Deviated: 0 (beyond the plan's own pre-declared deviations P11-1..P11-6, all confirmed present and accurate)

## Scope & method

**Ran (executed on this machine):**

- `.venv/bin/pip show pip-tools` / `pip list` (AC1)
- Standalone Python parser (independent re-implementation of `parse_closure` semantics) over
  `requirements/{base,dev}.txt` vs `git show HEAD:…` snapshots (AC1, AC3, AC4)
- Strict hash-coverage check: every logical package line joined across `\` continuations must
  contain `--hash=sha256:` (AC3)
- PyYAML load of `.github/workflows/ci.yml` + structural assertions (AC7)
- `make check` → **733 passed / 3 skipped** (AC6)
- `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg .venv/bin/python -m pytest -q` → **712 passed /
  24 skipped** (AC6; `docker ps` confirmed `amalthea-postgres-1` / `amalthea-redis-1` up, healthy)
- `.venv/bin/python -m pytest -q --cov=. --cov-fail-under=80` → **83.47%**, gate reached (AC6)
- `.venv/bin/pip install --dry-run --require-hashes -r requirements/dev.txt` → exit 0 (AC5)
- `.venv/bin/pip-audit` → exit 0, "No known vulnerabilities found" (AC6/AC8)
- `./scripts/lock.sh --check` → **one full independent lock run** with SHA-256 of both requirement
  files captured before and after (AC2/AC8 — see notes)
- `grep -nE` over `scripts/pre-commit.sh`, `Makefile`, `TODO.md`, `COMPLETED.md`, master plan §13
  (doc spot-checks)

**Inspected (read-only code review):**

- `scripts/lock.sh` (all 210 lines) — determinism mechanisms, `BOOTSTRAP_ALLOW`,
  `check_closure` diff-filter logic, `--check` semantics
- `.github/workflows/ci.yml` (all 73 lines), `docker-compose.yml` creds parity
- `pyproject.toml` (`fail_under = 80`, `addopts`), `Makefile` `check`/`lock` targets

**Not run (deliberate):** repeated `lock.sh` runs (each re-hashes the full closure — mitigated by
running the regeneration once inside `--check`); the exact CI `pip install` (would touch `.venv` —
the `--dry-run --require-hashes` equivalent was run instead).

**Read-only discipline:** the single `lock.sh` run was bracketed by SHA-256 snapshots; the files
came out byte-identical (`b4337a…` base, `09c34f…` dev), so the run left the working tree
unchanged. `git status` before/after shows the same 7 modified + 2 untracked paths.

## Criterion-by-Criterion

### AC1 — pip-tools 7.6.2 + exactly 6 bootstrap pins; base version facts unchanged
**Status: Met**

- `pip show pip-tools` → **7.6.2**; installed closure matches pins: `pip 26.2.1`,
  `pyproject_hooks 1.3.3`, `setuptools 84.0.0`, `wheel 0.48.0`, `build 1.6.1`.
- `requirements/dev.txt` contains exactly the 6 bootstrap entries (lines 89, 824, 860, 1111, 1119,
  1126): `build==1.6.1`, `pyproject-hooks==1.3.3`, `pip-tools==7.6.2`, `wheel==0.48.0`,
  `pip==26.2.1`, `setuptools==84.0.0`.
- Independent closure parse vs git HEAD: **dev added = exactly those 6, dev removed = ∅;
  base added = ∅, base removed = ∅** (old dev 97 → new 103 = 97 + 6; base 49 → 49).
- Observed cosmetic (non-version) changes in base.txt vs HEAD: PEP-503 name canonicalization
  (`Automat`→`automat`, `zope.interface`→`zope-interface`, `typing_extensions`→`typing-extensions`)
  and extras now retained (`Twisted`→`twisted[tls]`, per `--no-strip-extras`, P11-5). Every
  `name==version` fact under normalized names is identical to HEAD — no version moved.

### AC2 — `scripts/lock.sh` deterministic (byte-identical on repeat)
**Status: Met (mechanism inspected + one empirical regeneration)**

Mechanisms present in `scripts/lock.sh`:
- Temp-path normalisation: `sed -E "s#$TMP/##g"` at **line 135** (base output) and **line 145**
  (dev output) — strips the random `mktemp -d` path that pip-compile writes into header/`via`
  comments (P11-5).
- `--no-strip-extras` on both `pip-compile` invocations (**lines 131, 143**) — pins current
  pip-tools behaviour against the 8.0 default flip (P11-5) and keeps output quiet/deterministic.
- Stable ordering: `parse_closure` emits `sorted(set(...))` (**line 80**); pip-compile input is
  fully-pinned (`name==version`) so no version choice exists; dev body is filtered and reassembled
  from fixed `printf` header lines + filtered output (**lines 195-200**).
- Snapshot/closure logic: git-HEAD snapshot into `$TMP` (**lines 124-127**), closure compare after
  the pass (**lines 203-208**).

Empirical: one full independent regeneration (via `lock.sh --check`) reproduced both files at
SHA-256 **byte-identity** with their pre-run state — i.e. the implementer's producing run and this
verifier's run are byte-identical (≥2 runs). The plan records 3 consecutive identical runs; code
inspection confirms the remaining determinism levers. Judge: AC2 Met.

### AC3 — Hash coverage, `-r base.txt` contract, runtime/dev disjointness
**Status: Met**

- Strict per-logical-line check (continuations joined): base **49 pkg lines, 0 missing hashes**
  (710 `--hash=sha256:` lines); dev **54 pkg lines, 0 missing hashes** (878 hash lines) — matches
  the documented 49/54/710/878 figures exactly.
- First un-commented line of `requirements/dev.txt` is `-r base.txt`.
- Own-body name-sets (normalized, both files' bodies excluding `-r` expansion) intersect in **∅**.

### AC4 — `name==version` multiset unchanged except the 6-entry allowlist
**Status: Met**

- Independent parser (replicating `parse_closure`: continuation joining, hash stripping, `-r`
  resolution, `-c` skip, extras/PEP-503 normalization, env-marker stripping) against the git-HEAD
  snapshot:
  - base: added ∅ / removed ∅
  - dev: **added exactly** `build==1.6.1`, `pip-tools==7.6.2`, `pip==26.2.1`,
    `pyproject-hooks==1.3.3`, `setuptools==84.0.0`, `wheel==0.48.0` / removed ∅
  — i.e. exactly `BOOTSTRAP_ALLOW` (lock.sh **lines 107-109**).
- Mechanism soundness (`check_closure`, **lines 86-100**): `diff` of two sorted lists produces
  `<`/`>` content lines plus normal-format metadata (`11a12`, `3c3`, `---`); `grep '^[<>] '` keeps
  only content lines (metadata incl. `---` excluded); `sed 's/^[<>] //'` strips the prefix before
  `grep -vFxf` (fixed-string, whole-line match against the allowlist — regex metachars in package
  names can't false-match; a removed/changed version pair produces bare lines that don't match any
  allowlist entry → flagged). Empty-allow case (base) is harmless: it only filters blank lines,
  which the sorted set contains none of. Failing check aborts the script (`set -e` + `|| exit 1`,
  **lines 205-208**).
- Empirically exercised: the inner regeneration during this pass's `lock.sh --check` run exited 0
  (otherwise the outer script would have aborted before printing `lock: STALE`), proving
  `check_closure` ran end-to-end against the git-HEAD snapshot and passed.

### AC5 — `pip install --dry-run --require-hashes -r requirements/dev.txt` exits 0
**Status: Met** — ran once: `EXIT=0` (hash-checking mode over the resolved closure; last lines
confirm all requirements satisfied).

### AC6 — Gates unchanged on the hashed closure
**Status: Met** — all three reproduced live (see Gate figures). No behavioural delta from the hash
pass; containers were up, so no reliance on recorded figures was needed.

### AC7 — Workflow exists, valid YAML, minimal permissions, service parity, hook coverage
**Status: Met**

- `.github/workflows/ci.yml` exists; **PyYAML parses it** (top keys `name / on / permissions /
  concurrency / jobs`).
- `permissions: {contents: read}` (**line 21-22**) ✓.
- Service: `postgres:16-alpine` (**line 35**) with `POSTGRES_USER/PASSWORD/DB = amalthea` and port
  `5432:5432` — **exactly matches `docker-compose.yml` lines 8-11** ✓.
- 8 steps total: `actions/checkout@v4`, `actions/setup-python@v5` (Python 3.14, pip cache), then 6
  run steps whose commands are verbatim locally-runnable: venv+`pip install --require-hashes`,
  `make check`, the `test_pg` pytest invocation, the coverage gate, `./scripts/lock.sh --check`,
  `.venv/bin/pip-audit`. Each command was run (or dry-run equivalent) locally during this pass.
- `scripts/pre-commit.sh` **line 22**: `grep -qE '\.(py|toml|yml|yaml)$'` over staged paths —
  `.github/workflows/ci.yml` ends in `.yml`, so the hook guards it ✓.
- No `SECRET_KEY`/tokens in the workflow; only the documented dev-default `POSTGRES_PASSWORD:
  amalthea`.

### AC8 — Each of the six CI steps has a locally-executable equivalent exiting 0
**Status: Met (with the pre-declared lock-check nuance)**

| CI step | Local run | Exit |
|---|---|---|
| install (`--require-hashes`) | `pip install --dry-run --require-hashes -r requirements/dev.txt` | 0 |
| `make check` | ran as-is | 0 (733/3) |
| Postgres suite | ran as-is | 0 (712/24) |
| coverage gate | `.venv/bin/python -m pytest -q --cov=. --cov-fail-under=80` as-is | 0 (83.47%) |
| `./scripts/lock.sh --check` | ran as-is | **1 — expected pre-commit** (see below) |
| `.venv/bin/pip-audit` | ran as-is | 0 (0 CVEs) |

`lock.sh --check` mechanism judged correct: it regenerates in place (byte-identical output, SHA-256
verified) and then runs `git diff --quiet -- requirements/{base,dev}.txt` — whose semantics are
"worktree differs from git HEAD". The requirements are **uncommitted**, so HEAD still holds the old
unhashed files → exit 1 by construction. Post-commit, HEAD will contain exactly the bytes the lock
reproduces (proven above) → `git diff` empty → exit 0. This nuance is declared in the plan (AC8 MET
note) and in the task brief; the mechanism, not the pre-commit exit code, is what AC8 assesses.

## Deviations observed

None beyond the plan's own pre-declared deviations, all of which were confirmed accurate against
the implementation:

- **P11-1** — lock mechanism is `pip-compile --generate-hashes` over existing pins (lock.sh header
  lines 4-18, invocation lines 131/143). Confirmed.
- **P11-2** — CI coverage step hard-codes `--cov-fail-under=80` (ci.yml line 67) while
  `pyproject.toml` `fail_under = 80` applies regardless; local `make coverage` unchanged. Confirmed.
- **P11-3** — no `.in` layer; self-referential input (`base.txt` / dev-minus-`-r`). Confirmed.
- **P11-4** — runner-side validation deferred to first push (no remote exists); all steps verified
  locally (AC8). Confirmed.
- **P11-5** — `--no-strip-extras` on both passes (lines 131/143) + temp-path normalisation. Confirmed;
  extras now visible in output (`twisted[tls]`, `cachecontrol[filecache]`, `coverage[toml]`).
- **P11-6** — real counts 49 base / 54 dev (not the stale "53+45"); closure diff computed against the
  actual git-HEAD snapshot. Confirmed: 49/54/710/878 verified by count.

Minor observations (not AC failures):
1. Plan file T5.1 checkbox is still `- [ ]` although all T5.1 deliverables (master plan §13 record,
   TODO §4.3/§8.6 closures, COMPLETED.md entry) are present — bookkeeping nit only.
2. Master plan §13 Phase 11 record ends with "see §ref below" (dangling cross-reference to this
   report, which now exists at the named path).
3. CI coverage step uses explicit `--cov=. --cov-fail-under=80` where the plan's Architecture sketch
   wrote `--cov` (relying on pyproject `fail_under`) — a superset, and exactly the AC6 invocation;
   judged a strengthening, not a deviation.

## Gate figures (observed 2026-10-07, this pass)

| Gate | Command | Observed | Recorded claim | Match |
|---|---|---|---|---|
| SQLite DoD | `make check` | **733 passed, 3 skipped** (27.9s) | 733 / 3 | ✓ |
| Postgres suite | `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg …pytest -q` | **712 passed, 24 skipped** (34.6s) | 712 / 24 | ✓ |
| Coverage | `pytest -q --cov=. --cov-fail-under=80` | **83.47%**, gate ≥80 reached | 83.47% | ✓ |
| Hash install | `pip install --dry-run --require-hashes -r requirements/dev.txt` | exit 0 | exit 0 | ✓ |
| Supply chain | `.venv/bin/pip-audit` | exit 0, 0 CVEs | 0 CVEs | ✓ |
| Lock determinism | full regen, SHA-256 before/after | byte-identical | byte-identical | ✓ |
| Lock currentness | `./scripts/lock.sh --check` | exit 1 (files uncommitted) | declared nuance | ✓ (mechanism correct) |

Containers at time of run: `amalthea-postgres-1` (postgres:16-alpine) and `amalthea-redis-1`
(redis:7-alpine), both Up 30h (healthy) — Postgres figure is live-observed, not relied-on from
records.

## Documentation spot-checks

- Master plan §13 **Phase 11 record** present (lines **694-712**): P11-1..P11-6 table complete and
  accurate; gate line (710-711) matches observed figures (733/3, 712/24, 83.47%); counts line
  (P11-6, line 708) matches 49/710/54/878 ✓. §5 lock-mechanism wording (line 77) updated ✓.
- `TODO.md` **§4.3** closed at line 289 with correct figures (49/710, 54/878, 6-entry allowlist,
  byte-identical `make lock`); **§8.6** closed at line 473 with correct step list ✓.
- `COMPLETED.md` has the Phase 11 entry (appended, 35 lines) with matching figures and an accurate
  pointer to this report path ✓.
- `Makefile` `lock` target comment (line 73) updated to "Regenerate the pinned, hash-verified
  requirement closure (requirements/{base,dev}.txt)" ✓.

## Blockers

None. The implementation is commit-ready from a conformance standpoint; the only red exit
(`lock.sh --check` → 1) is the documented pre-commit semantics and will clear on commit.

## Closing verdict

**All eight acceptance criteria (AC1–AC8) are Met**, evidence-backed by direct execution (gates,
hash/coverage/closure parsers, YAML validation, one full lock regeneration) and by code inspection
of the determinism and allowlist mechanisms. Recorded documentation claims (master plan §13,
TODO §4.3/§8.6, COMPLETED.md, Makefile) match observed reality. The wave conforms to
`PLAN-2026-10-07-ci-hashed-requirements.md`; AC9 is satisfied by this report.
