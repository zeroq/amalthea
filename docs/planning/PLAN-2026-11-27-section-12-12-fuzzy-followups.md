# PLAN-2026-11-27 — §12.12 fuzzy-similarity follow-ups

**Status:** Implemented and verified by targeted suites; full pytest remains user-owned. Uncommitted on top of the §15 and §11.4 work. Targeted suites: `89 passed in 74.87s`; broader integration selection: `415 passed in 309.49s`; `make check`, `git diff --check`, and secret scan clean.

## Summary
Close the remaining `TODO.md` §12.12 items: bound fuzzy-hash CPU (FIND-03), make volatile-key handling extensible (FIND-06), document the two accepted canonicalization deviations (FIND-07/FIND-12), wire the already-written lock script into `make lock` (FIND-11), and pin the cross-org merge feedback fix (pre-existing bug).

## Goals
- **12.12-fu2:** add `ALERT_FUZZY_MAX_TEXT_SIZE` truncation before `ppdeep.hash`, and cap case signal hashes with `ALERT_FUZZY_MAX_CASE_SIGNALS`.
- **12.12-fu3:** add `ALERT_FUZZY_EXTRA_VOLATILE_KEYS` and an opt-out UUID/timestamp/epoch value heuristic.
- **12.12-fu4:** document `backfill --force` failure semantics (no blanking by default), add `--clear-on-failure`, and document scalar-list sorting false-positive risk.
- **12.12-fu5:** implement `make lock` as a real target backed by `scripts/lock.sh`.
- **12.12-fu6:** pin the already-correct refusal path in `case_alert_link` — error banner, never success — with a falsifiable assertion.

## Non-goals
- No schema migration; `Alert.payload_ssdeep` stays as-is.
- No async/Celery cache for the Similar tab in this change (deferred; documented).
- No Postgres microbenchmark in this change — no harness is available here; the bounds are pinned by conformance tests and the runtime ceiling is documented.
- No per-source list-sort configuration; only document the trade-off.
- No new user-facing API endpoints or UI layout changes.

## Architecture
- **Stack rationale:** Django settings remain the single configuration surface; hashing stays synchronous at ingest and in the Similar helper. All new knobs are plain env-backed settings so existing deployment patterns work unchanged.
- **Source of truth:** `alerts/hashing.py` owns canonicalization/truncation; `cases/correlation/similarity.py` owns comparison bounds; `alerts/management/commands/backfill_alert_ssdeep.py` owns invalidation behavior.
- **ADR reference:** no new ADR; this continues `PLAN-2026-10-20-alert-fuzzy-similarity.md` Decision (A) and the Option B title gate.

## Data model
- No migration.
- `Alert.payload_ssdeep` remains `CharField(max_length=160, blank=True, default="")`.
- Changing the canonicalization inputs changes hashes for existing rows: operators must run `manage.py backfill_alert_ssdeep --force` after upgrading; this is documented in the runbook.

## API contracts
- No REST API changes.
- Management command gains `--clear-on-failure`:
  - `--force` recomputes all rows; when recomputation returns `""`, the existing hash is preserved by default (historical conservative behavior).
  - `--force --clear-on-failure` blanks the existing hash when recomputation returns `""`, making the row explicitly non-comparable.
  - Without `--force`, `--clear-on-failure` has no practical effect because only empty-hash rows are selected; this is documented.

## UI/UX
- No layout changes.
- Cross-org merge refusal via Similar tab must render `Cross-org alert bundle refused` as an error message and must never render `Alert linked to case N.`
- Dark theme, keyboard-driven behavior, and modal conventions are unaffected.

## Phases and Acceptance Criteria
### Phase 1 — Bounded CPU (fu2)
- Add to `amalthea/settings/base.py`:
  - `ALERT_FUZZY_MAX_TEXT_SIZE` from `AMALTHEA_ALERT_FUZZY_MAX_TEXT_SIZE`, default `262144`.
  - `ALERT_FUZZY_MAX_CASE_SIGNALS` from `AMALTHEA_ALERT_FUZZY_MAX_CASE_SIGNALS`, default `50`.
- **AC1:** `compute_payload_ssdeep` never passes more than `ALERT_FUZZY_MAX_TEXT_SIZE` characters to `ppdeep.hash`.
- **AC2:** `ALERT_FUZZY_MAX_TEXT_SIZE <= 0` yields `""` (explicitly disabled), never an exception.
- **AC3:** `alerts_fuzzy_similar_to_case` compares at most `ALERT_FUZZY_MAX_CASE_SIGNALS × MAX_FUZZY_COMPARISONS` pairs.
- **AC4:** Runbook documents the new ceiling and that async caching remains deferred.

### Phase 2 — Extensible volatile handling (fu3)
- Add to `amalthea/settings/base.py`:
  - `ALERT_FUZZY_EXTRA_VOLATILE_KEYS` from comma-separated `AMALTHEA_ALERT_FUZZY_EXTRA_VOLATILE_KEYS`, defaulting to common vendor keys (`flow_id`, `session_id`, `transaction_id`, `correlation_id`, `message_id`, `log_id`, `sequence`, `epoch`, `clock`, `uptime`, `counter`, `nonce`, `token`, `session`).
  - `ALERT_FUZZY_VOLATILE_VALUE_PATTERNS` from `AMALTHEA_ALERT_FUZZY_VOLATILE_VALUE_PATTERNS`, default `true`.
- **AC5:** Casefolded/underscore/hyphen-normalized extra keys are dropped at any nesting depth.
- **AC6:** With the value heuristic enabled, scalar string values matching UUID, ISO timestamp, or 10–13 digit epoch forms are dropped.
- **AC7:** `ALERT_FUZZY_VOLATILE_VALUE_PATTERNS=False` restores key-only behavior.
- **AC8:** A non-volatile, non-matching change still changes the canonical text (non-vacuous control).

### Phase 3 — Backfill/list-sort documentation (fu4)
- Add `--clear-on-failure` to `backfill_alert_ssdeep`.
- Update command help and `docs/runbook.md` for both caveats.
- **AC9:** Default `--force` behavior remains "preserve existing hash on recomputation failure".
- **AC10:** `--force --clear-on-failure` blanks the hash on recomputation failure.
- **AC11:** Runbook states that scalar-list sorting can collapse order-significant arrays and that per-source configuration is a future decision.

### Phase 4 — Lock recipe (fu5)
- Add `lock` recipe to `Makefile` invoking `scripts/lock.sh` (and preserve `lock` in `.PHONY`).
- **AC12:** `make lock` is a real target; no network/regeneration runs during this implementation.
- **AC13:** Conformance guard reads the Makefile and asserts the recipe delegates to `scripts/lock.sh`.

### Phase 5 — Feedback pin (fu6)
- Add message assertions to the existing superAdmin cross-org link test.
- **AC14:** The response messages contain the refusal text and never contain the success text.
- **AC15:** Persistence/audit invariants remain unchanged.

## Security
- Bounds prevent a single case view from driving the historical `500 × 1000` comparison worst case.
- Truncation happens before SSDEEP hashing; no raw payload is logged or rendered.
- Value heuristics drop only scalar strings matching narrow patterns; numeric values are not heuristically dropped.
- Tenant scoping, title gate, and audit behavior are untouched.
- Secret scan remains mandatory; no new secrets or fixture values.

## Testing
- New/updated conformance suite: `tests/conformance/test_alert_fuzzy_followups.py`.
- Existing pin updated: `tests/conformance/test_alert_fuzzy_similarity.py`.
- Local gates: `make check`, `git diff --check`, `scripts/secret-scan.sh --files ...`.
- User-owned final gate: full `pytest -q`.

## Risks
- **Canonicalization hash changes:** existing hashes must be backfilled with `--force`; documented.
- **False negatives from heuristics:** operators can disable value patterns or add/remove vendor keys via env.
- **False positives from list sorting:** documented; per-source control remains future work.
- **Async caching deferred:** bounded synchronous path is the interim mitigation.

## Open Questions
- Whether to move Similar-tab computation to Celery/cached results remains open pending real Postgres timings.
- Per-source list-sort configuration remains open; no concrete ingestion-source contract exists yet.
