# PLAN — Fuzzy (SSDEEP) alert similarity for the case "Similar" tab

**Status:** DRAFT — awaiting approval
**Date:** 2026-10-20
**Author:** planner
**Supersedes / relates to:** `PLAN-2026-10-20-similar-alerts-card.md` (the exact-signal card this **replaces**)

---

## 0. Summary

Alert payloads from SIEM/EDR/cloud tools are frequently **near-duplicates** — the same event with a
different timestamp, PID, request-id, hostname, or key ordering. The current "Similar alerts" card
matches only when extracted observables are byte-identical after normalization, so it misses those
near-duplicates. This plan adds a **fuzzy-hash similarity layer**: compute an SSDEEP-compatible hash of
each alert's canonicalized payload **at ingest**, store it on the `Alert`, and rank unlinked same-org
alerts by SSDEEP similarity against the case's attached alerts, keeping those at/above a configurable
threshold.

Per the approved decisions (Q1–Q8, §9), this layer **replaces** the exact-signal basis of the
"Similar alerts" card (the existing `alerts_similar_to_case` exact-overlap ranking is retired from the
card; the card keeps its shell, tenant scoping, viewer gating and one-click link action).

---

## 1. Goals

- Compute an SSDEEP-compatible fuzzy hash from each alert's payload **once, at ingest**, and store it on
  the `Alert` (`payload_ssdeep`).
- Provide a batched, idempotent **backfill** command for alerts created before this change.
- Rank **unlinked, same-org** alerts by fuzzy similarity to the case's attached alerts; surface those at
  or above a **configurable threshold**.
- Show a **similarity score** per row so analysts can judge the match.
- Preserve every existing guarantee of the card: tenant safety, viewer read-only, one-click
  `Link to case`, tab-gating, empty states.

## 2. Non-goals

- No case-level fuzzy hash (Q8: `Alert` only).
- No automatic bundling/escalation from fuzzy matches (still §12.3-fu1, out of scope).
- No per-org / per-ingestion-source threshold (Q3: global setting now; per-source is a follow-up).
- No index on the hash (SSDEEP has no native index; comparison is bounded in Python — §6).
- No change to the API wire contract (the field is internal; `GET /alert/{id}` is unchanged).

---

## 3. Architecture & decisions

### 3.1 Library (Q1) — `ppdeep`
`ppdeep` is a **pure-Python, SSDEEP-compatible** implementation — no `libfuzzy`, no C toolchain, no
system dependency. This fits the repo's **hash-pinned, hermetic CI** (`pip install --require-hashes`)
far better than the `ssdeep` C-extension (which builds from sdist and needs `libfuzzy` at build time).
API used: `ppdeep.hash(text) -> str`, `ppdeep.compare(h1, h2) -> int` (0–100).

- Add `ppdeep` to the pinned closure. **The pinned requirements have no `base.in` and no `lock`
  target recipe** (see TODO: `make hooks`/`make lock` are empty `.PHONY` targets). Dependency addition
  procedure: add the package + its `--hash=sha256:…` entries to `requirements/base.txt` (and mirror to
  `dev.txt` if it is a dev-only need — it is **runtime**, so `base.txt` only), preserving the pinned
  closure so `pip install --require-hashes -r requirements/dev.txt` (CI, §.github/workflows/ci.yml:66)
  still verifies. Record the exact version + hashes in the plan's implementation note.
- **Supply-chain note:** new third-party dependency → in scope for the `security-auditor` pass (§11).

### 3.2 What we hash (Q2 decision (b)) — canonicalized projection
Hashing the raw payload blob is noisy (volatile fields change every event). We hash a **canonicalized
projection**:

1. Start from `alert.raw_payload` (a dict).
2. **Drop volatile keys** (case-insensitive, top-level and nested) — a documented `VOLATILE_KEYS`
   frozenset, e.g.: `_id`, `id`, `uuid`, `date`, `timestamp`, `time`, `@timestamp`, `created`,
   `createdAt`, `updatedAt`, `startTime`, `endTime`, `eventId`, `event_id`, `requestId`,
   `request_id`, `traceId`, `trace_id`, `pid`, `processId`, `process_id`, `ingestionTime`,
   `ingestedAt`, `hash`, `signature`, `sourceRef`, `source_ref`.
   > Rationale: these are the fields that make two same-event alerts differ. The list is data, not
   > logic, and lives in one constant so it is auditable/extendable.
3. **Recursively sort** dict keys; preserve list order (list order can be meaningful, but see below).
   - **List normalization:** sort **scalar lists** (e.g. lists of strings) to absorb ordering noise;
     leave **lists of dicts** in place after recursively canonicalizing each element. Document this.
4. Serialize deterministically with `json.dumps(..., sort_keys=True, separators=(",", ":"),
   ensure_ascii=False)`.
5. Prepend `summary` (included so alerts with sparse payloads still carry signal), then
   `ppdeep.hash(<canonical text>)`.

> **Decision (A), 2026-10-20 — `title` is EXCLUDED from the hash.** Measured with the real
> implementation: including `title` made the 80-threshold fragile because SIEM/EDR titles commonly
> embed per-event tokens. Scores: same title + volatile-only payload diff → **100**; title with a
> timestamp (`"… at 12:00:00"` vs `12:05:33`) → **33**; title with an ID (`"ALERT-99"` vs
> `"ALERT-123"`) → **22**; different title, identical payload → **22**. Since volatile-key stripping
> cleans the *payload* but not the *title*, a per-event title token collapses the score far below 80
> and silently defeats the feature for exactly the "same event, different noise" case it exists to
> catch. Therefore the hashed text is **`summary` + canonical payload only** — `title` is not hashed.
> (Consequence: two different events with similar payloads but different titles could score high;
> the strict 80 threshold is the guard against that, to be validated by measurement in verification.)

**Bounds re-applied** (mirroring `candidate_cases_for_alert`): skip/reject payloads larger than
`WEBHOOK_MAX_BODY_SIZE` or deeper than `WEBHOOK_MAX_DEPTH`; catch `(Exception, RecursionError,
MemoryError)` and store an **empty string** on failure (never fail ingest). An empty hash means "not
fuzzy-comparable" and such alerts are simply excluded from the fuzzy candidate set.

**Short-input caveat:** `ppdeep`/SSDEEP produce degenerate hashes for very short inputs; a hash for an
input shorter than ~a few dozen bytes may compare as non-similar or 0. Document; do not special-case
beyond storing what `ppdeep` returns.

### 3.3 Threshold (Q3) — global Django setting
- `settings/base.py`: `ALERT_FUZZY_SIMILARITY_THRESHOLD = int(os.getenv("AMALTHEA_ALERT_FUZZY_THRESHOLD",
  "80"))`. Range 0–100; SSDEEP scores below ~50 are generally noise, and **80** is a stricter,
  higher-precision default (favours "same event, different noise" over loose resemblance).
- Documented in `base.py` next to `WEBHOOK_MAX_*`. Per-ingestion-source is a follow-up; not now.

### 3.4 Replace vs combine (Q4) — **replace**
The "Similar alerts" card switches from the exact-signal basis to the fuzzy basis:
- `alerts_similar_to_case` (exact-overlap ranking) is **removed** from the card's data path.
- New `alerts_fuzzy_similar_to_case(case, *, user, threshold=None, limit=None)` computes fuzzy scores
  and returns rows at/above threshold.
- `SimilarAlert.shared_count`/`shared` (exact-overlap identities) are replaced by
  `SimilarAlert.similarity` (0–100). The card shows "N% similar".
- **Retain** the `cases/correlation.py` exact-overlap helpers (`cases_sharing_findings`,
  `candidate_cases_for_alert`) — those power the **Similar cases** card and the alert-merge dropdown and
  are untouched. Only the *alert* exact-overlap function is retired.
- The retired function's tests are removed/replaced (see §8); a short note records the retirement.

### 3.5 Backfill (Q5) — management command, not a data migration
`alerts/management/commands/backfill_alert_ssdeep.py`:
- Idempotent, batched (`--batch-size`, default 500), `--org`/`--all-orgs` scoping optional,
  `--dry-run`, `--force` (recompute non-empty).
- Iterates alerts where `payload_ssdeep == ""` (or all with `--force`), computes via the shared helper,
  bulk-updates. Reports counts. Uses `.iterator()` to avoid loading everything.
- **Not** a data migration: keeps deploys fast and avoids running the hasher inside `migrate`.
- Deployment note in `COMPLETED.md`/runbook.

### 3.6 Which ingest paths (Q6) — both, via one helper
A single implementation `Alert.compute_ssdeep()` (method) or module function
`alerts/hashing.py::compute_payload_ssdeep(alert_or_payload)` used by:
- `ingest/pipeline.py::store_alert` — set `payload_ssdeep` in the `defaults={...}` of `get_or_create`
  (only on create; a replayed alert keeps its hash).
- `alerts/views.py` API create path — set it on the new `Alert`.
- Plus `Alert.save()` does **not** silently recompute (to avoid surprising writes and to keep ingest
  explicit). The two callers set it explicitly. (Rationale: a `save()` hook would also fire on every
  unrelated `alert.save()` — e.g. status changes — wasting CPU.)

### 3.7 Cost cap (Q7) — separate, smaller
- New `MAX_FUZZY_COMPARISONS = 500` in `cases/correlation.py` (or `alerts/hashing.py`).
- Candidate set: unlinked + same-org + non-empty-hash, ordered `-date`, sliced to
  `MAX_FUZZY_COMPARISONS` before the O(A×N) compare loop.
- Case signals: the case's attached alerts' hashes, sliced to `MAX_CORRELATION_OBS` (reuse).
- Compare cost: `len(case_hashes) × len(candidates)`, each `ppdeep.compare` — bounded by the two caps.

### 3.8 Case-signal definition
"Similar" = an unlinked alert's hash scores ≥ threshold against **at least one** of the case's attached
alerts. Reported `similarity` = the **maximum** score across those comparisons (best match).

---

## 4. Data model & migration

### 4.1 `Alert` field
```python
payload_ssdeep = models.CharField(max_length=160, blank=True, default="")
```
- SSDEEP hashes are ≤ ~148 chars (`blocksize:hash1:hash2`); 160 gives headroom.
- No index: not used for lookups/seeks (candidates are filtered by `case__isnull` + org + hash non-empty;
  the hash itself is only compared in Python).
- Migration: `alerts/migrations/0017_alert_payload_ssdeep.py` (additive, nullable-with-default →
  no table rewrite on Postgres; safe on large tables).

### 4.2 No other schema change.

---

## 5. API contract

**No external API change.** `payload_ssdeep` is internal:
- Not added to `GET /api/v1/alert/{id}` (keeps the wire contract stable; the field is a derived
  internal artifact). If we ever expose it, that's a separate decision.
- The UI POST `ui-case-alert-link` is unchanged.

---

## 6. UI/UX

`ui/templates/ui/_tab_similar.html` — "Similar alerts" card, changes:
- Column set: **Source / Title / Severity / Status / Received / Similarity / Action**.
  (Adds a **Similarity** column showing `{{ sa.similarity }}%`; the previous "shared artifacts" basis is
  gone.)
- Server passes `similar_alerts` (list of `SimilarAlert` with `.similarity`) and `similar_alerts_total`.
- Truncation line + two empty states unchanged in spirit; reword the no-match empty state to
  "No unlinked alerts are similar enough right now." and keep "This case has no alerts to compare
  against." when the case has no attached alerts.
- Viewer gating / hidden `tab=similar` / one-click link: **unchanged** (AC-6/AC-7 of the prior plan carry
  over).
- No new CSS required if `sev`/`pill`/`data-table` cover the Similarity column; if a small badge is
  wanted, reuse existing tokens — **no `app.src.css` change unless unavoidable**, and then `make css`.

---

## 7. Phases & tasks (with Acceptance Criteria)

### Phase A — model + hashing primitive (`django-backend`)
- **A1** Add `payload_ssdeep` to `Alert` + migration `0017`. 
  **AC-A1:** migration applies cleanly; `makemigrations --check` clean afterwards.
- **A2** `alerts/hashing.py`: `canonicalize_payload(payload, *, max_body, max_depth) -> str` (volatile-key
  drop, recursive key sort, scalar-list sort) and `compute_payload_ssdeep(text_or_alert) -> str`
  (bounded, exception-safe → `""` on failure).
  **AC-A2:** deterministic (same payload → same hash); volatile-only differences → identical canonical
  text; oversize/overdeep/exception payloads → `""` without raising.
- **A3** Wire both ingest paths (Phase A §3.6): `ingest/pipeline.py::store_alert` `defaults`,
  `alerts/views.py` create. 
  **AC-A3:** a freshly ingested alert and a freshly API-created alert each have non-empty
  `payload_ssdeep` (when payload is hashable).
- **A4** `settings/base.py` threshold setting + docs. **AC-A4:** setting present, env-overridable,
  default 80.
  > **Update (2026-10-21):** the shipped default was **lowered to 60** by operator decision after manual
  > evaluation (traded precision for recall; see Q3 and the FIND-05 update in
  > `docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md`). AC-A4 now reads "default **60**".
  > The conformance fixtures were re-calibrated to the new default (see §14 of
  > `PLAN-2026-10-21-similar-tab-lazy-badge.md`).
- **A5** Backfill command `backfill_alert_ssdeep`. 
  **AC-A5:** idempotent (second run updates 0); `--dry-run` writes nothing; batched via `.iterator()`.

### Phase B — fuzzy comparison helper (`django-backend`)
- **B1** `alerts_fuzzy_similar_to_case(case, *, user, threshold=None, limit=None) -> (rows, total)` +
  `SimilarAlert` updated to carry `.similarity` (0–100). Unlinked + same-org + non-empty hash; bounded by
  `MAX_FUZZY_COMPARISONS`; max-score semantics (§3.8); rank by similarity desc, then `-date`.
  **AC-B1:** identical-payload unlinked same-org alert scores high (≥ threshold) and is returned; a
  disjoint alert is excluded; cross-org alert never returned; attached alerts never returned;
  scoring is bounded and exception-safe.
- **B2** Remove the exact-overlap alert path from the card's data path (retire
  `alerts_similar_to_case` from `_case_detail`; the function may be deleted or left with a deprecation
  note — decide at implementation, but the card must no longer use it). **AC-B2:** the card's context is
  populated solely by `alerts_fuzzy_similar_to_case`; tab-gating preserved.

### Phase C — UI (`django-frontend`)
- **C1** Add the **Similarity** column; reword empty state. **AC-C1:** rendered card shows `%`; empty
  states correct; viewer sees no Link button; hidden `tab=similar` intact.

### Phase D — tests (`qa-tester`)
- **D1** `tests/conformance/test_alert_fuzzy_similarity.py`: determinism; volatile-key insensitivity;
  threshold boundary (a crafted pair just above/below threshold); cross-org exclusion (+falsifiability);
  attached exclusion; viewer read-only; link flow idempotence (carried over); backfill idempotence;
  oversize/exception payload → `""`.
  **AC-D1:** all pass; **falsifiability**: removing the org filter / `case__isnull` / threshold check
  makes the corresponding test fail (prove by temporary sabotage, then restore).

### Phase E — verification (`verifier`) + security (`security-auditor`)
- **E1** Independent AC verification report.
- **E2** `security-auditor`: new dependency supply-chain; canonicalization never persists secrets beyond
  what's already stored (hash is non-reversible but treat as sensitive-if-payload-sensitive); no new
  auth/tenant holes.

### Phase F — planner
- Update `COMPLETED.md`, `TODO.md`, runbook backfill note.

---

## 8. Security & testing notes

- **Tenant isolation** is preserved exactly (same `org_q` + `case__isnull=True` discipline; the only new
  predicate is `payload_ssdeep != ""`).
- **Falsifiability (§7.5)** applies to: org filter, unlinked filter, threshold gate, and the
  volatile-key drop (a test that fails if we hash the raw blob).
- **Dependency** `ppdeep` added to the hashed closure — CI's `--require-hashes` must still pass; this is
  a hard AC for Phase A.
- **Perf:** hashing is O(payload) once at ingest; comparison is bounded by
  `MAX_FUZZY_COMPARISONS × |case hashes|`. A rough budget: 500 × 20 = 10k `ppdeep.compare` calls/page —
  measure on Postgres before shipping (§12.1-fu1-style measurement; note the known Postgres-harness gap).
- **Migration safety:** additive `CharField(default="")` — no rewrite.

## 9. Locked decisions (Q1–Q8)

| Q | Decision |
|---|----------|
| Q1 | Library = **`ppdeep`** (pure-Python, SSDEEP-compatible, no system deps). |
| Q2 | Hash a **canonicalized projection** (decision (b)): drop documented volatile keys, recursive key sort, scalar-list sort, deterministic JSON, plus `summary` — **`title` excluded** (decision (A), 2026-10-20; see §3.2). |
| Q3 | **Global Django setting** `ALERT_FUZZY_SIMILARITY_THRESHOLD` (env `AMALTHEA_ALERT_FUZZY_THRESHOLD`), default **80**; per-source later. **Amended 2026-10-21: shipped default is now `60`** (operator decision after manual evaluation — more recall, more candidate matches). |
| Q4 | **Replace** the exact-signal basis of the "Similar alerts" card with fuzzy similarity; keep the shell/scoping/link action. |
| Q5 | **Management command** backfill, batched + idempotent; not a data migration. |
| Q6 | Wire **both** ingest paths via one shared helper. |
| Q7 | **Separate, smaller** cap `MAX_FUZZY_COMPARISONS = 500`. |
| Q8 | Store the hash on **`Alert` only** (no case-level hash). |

## 10. Risks / open questions

- **R1 — Threshold calibration.** 80 is a strict starting default (high precision, lower recall); real
  payload distributions may need tuning. Mitigation: configurable; measure during verification; document.
- **R2 — Canonicalization quality.** The volatile-key list is a judgment call; a missing key adds noise,
  an overzealous key removes signal. Mitigation: central constant, tests, review.
- **R3 — SSDEEP short-input degeneracy.** Very small payloads may not compare meaningfully. Mitigation:
  document; `summary` inclusion helps.
- **R4 — Dependency addition mechanics.** No `base.in`/`lock` recipe exists; hashes must be added
  manually and CI's `--require-hashes` re-verified. Mitigation: explicit AC; also fix the empty
  `hooks`/`lock` targets as a follow-up.
- **R5 — Perf on Postgres unmeasured** (known gap). Mitigation: bounded caps; measure in B1/verification.
- **R6 — Scope size.** This is a model change + new dependency + new command + card rework. It is **not**
  a "quick UI tweak"; plan for review by security-auditor and verifier.

## 11. Deviations
1. **Backfill uses per-row `save(update_fields=…)`, not `bulk_update`** (brief §A5 suggested per-batch
   `bulk_update`). Functionally identical and idempotent, but slower on large tables — accepted for a
   one-time command; the command documents the tradeoff inline.
2. **`--force` does not clear a previously-computed hash when recomputation now yields `""`** (it skips
   instead). Conservative — a transient failure cannot blank a good hash. Note for QA: `--force` means
   "recompute where computable", not "guarantee re-set".
3. **`_check_bounds` depth probe — initially recursive, now iterative** (|plan §3.2 referenced the
   iterative `_json_depth`; the first cut recursed). **Fixed in Phase E** (security FIND-02):
   `alerts/hashing.py::_max_json_depth` uses an explicit stack, so a payload deeper than the
   recursion limit cannot raise `RecursionError` during the probe. Falsifiable test:
   `TestUnhashablePayloads::test_depth_check_is_iterative_not_recursive` (a payload
   `recursionlimit()+500` deep is measured and rejected → `""`).
4. **`title` excluded from the hashed text (decision (A), 2026-10-20)** — a *design* change made after
   measuring the implementation, not an implementation slip: `compute_payload_ssdeep` no longer takes
   `title`; the hashed text is `summary` + canonical payload. See §3.2 for the measurements. The three
   call sites (`ingest/pipeline.py`, `alerts/views.py`, the backfill command) are updated accordingly.
   **Existing rows hashed under the old scheme must be re-hashed** — the backfill command's `--force`
   path covers this; deployment runbook must run `backfill_alert_ssdeep --force` once after upgrade.
5. **Retired `alerts_similar_to_case` deprecation stub deleted** (Phase E). It returned `([], 0)` and had
   zero callers repo-wide (verified); leaving a dead function invited future confusion.

### 11.1 Security follow-ups (from `docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md`)

Verdict **APPROVE WITH NOTES**. Fixed in Phase E: **FIND-02** (iterative depth probe, above). Open
items are recorded in `TODO.md` rather than fixed here (each is a design/ops change, not a defect):
- **FIND-05 (High, by design)** — excluding `title` (decision A) removes the title discriminator, so two
  different events with similar payloads can score high; the 80 threshold is the only guard. Mitigation
  candidates: an `ALERT_FUZZY_INCLUDE_TITLE` opt-in setting and/or a title-fingerprint require-both pass.
  **Trade-off is accepted for now and must be documented in the runbook.**
- **FIND-03 (Medium)** — `ppdeep.hash/compare` CPU on large inputs; propose `ALERT_FUZZY_MAX_TEXT_SIZE`
  truncation + a lower `case_hashes` cap; measure on Postgres (ties to risk R5).
- **FIND-06 (Medium)** — `VOLATILE_KEYS` may miss vendor-specific keys; propose
  `ALERT_FUZZY_EXTRA_VOLATILE_KEYS` (and a regex heuristic).
- **FIND-04 (Low)** — the superAdmin cross-org link path is now covered by a test; see §11.2.
- **FIND-07 / FIND-10 / FIND-11 / FIND-12 (Low)** — documented; FIND-11 (`make lock`) overlaps the
  existing empty-target follow-up.

### 11.2 Bug discovered during Phase E (pre-existing, out of scope)

`ui/views.py::case_alert_link` reports **success** (`messages.success("Alert linked to case N.")` + 302)
even when the merge is refused: `cases/correlation.py::record_alert_bundled` raises
`PermissionDenied("Cross-org alert bundle refused")` **for every role, superAdmin included**, but the
view only catches the exception *inside* the `try` — the `messages.success` runs after the `except`, so a
superAdmin attempting a cross-org link sees a false "linked" banner while the transaction rolls back.
The **invariant is correct** (cross-org bundling is refused regardless of role); only the **feedback** is
wrong. Not part of the fuzzy feature — recorded in `TODO.md` for a separate fix. Pinned by
`TestCrossOrgIsolation::test_superadmin_cross_org_link_is_refused_and_not_persisted`.

## 12. Handoff
- Implementation Brief: `docs/planning/BRIEF-2026-10-20-alert-fuzzy-similarity.md`.
- Phase order: A → B → C → D → E → F. A and B touch shared files (`cases/correlation.py`, `ui/views.py`)
  → run sequentially where files overlap.
- Phase A status: **implemented, `make check` green** (2026-10-20).
- Phase status: **A–D complete; E complete (verifier READY-TO-COMMIT 22/22; security APPROVE WITH
  NOTES, FIND-02 fixed); F in progress** (2026-10-21).
