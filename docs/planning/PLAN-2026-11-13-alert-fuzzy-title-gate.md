# Plan: Alert fuzzy similarity — title gate (TODO §12.12-fu1)

**Date:** 2026-11-13
**Status:** APPROVED / IMPLEMENTED — verified 2026-11-13
**Decision:** Option **B** — require a normalized title match in addition to payload SSDEEP similarity.
**Owner:** planner
**Source:** `TODO.md` §12.12-fu1; `docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md` FIND-05

---

## 1. Summary

The fuzzy "Similar alerts" tab currently hashes **summary + canonical payload** and deliberately excludes
`title` (Decision A). That preserves recall when titles embed per-event tokens, but two different events
with similar payloads can score high and be falsely linked. The shipped payload threshold is **60**, not
the original 80, so the title is the missing discriminator.

This wave adds a **title gate**: a candidate alert is similar only when, against the **same** case alert,
both conditions hold:

1. payload SSDEEP score ≥ `ALERT_FUZZY_SIMILARITY_THRESHOLD` (default 60); and
2. normalized title similarity ≥ `ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD` (default 70).

The gate is on by default and can be disabled with `ALERT_FUZZY_TITLE_GATE_ENABLED=False`.

The title fingerprint is computed **on demand** for the bounded candidate set. No schema change, no
migration, and no backfill. If performance ever becomes a problem, a stored fingerprint is a follow-up.

---

## 2. Goals

- Reduce FIND-05 false positives: different events with similar payloads but different titles must not
  appear as similar.
- Preserve recall for the same event with volatile title tokens (timestamps, UUIDs, numeric IDs).
- Make the behavior configurable and document the precision/recall trade-off in the deployment runbook.
- Keep tenant scoping, viewer gating, the Similar tab contract, and the API unchanged.
- Correct the stale threshold comments that claim the payload default is 80.

## 3. Non-goals

- No change to `payload_ssdeep` inputs or the payload hash algorithm.
- No stored title hash/fingerprint, no migration, no backfill.
- No new API fields, no wire-contract change.
- No UI redesign; the Similar tab still shows the payload similarity percentage.
- No automatic bundling/escalation from fuzzy matches (still §12.3-fu1).
- No change to the 500-candidate / 1000-case-signal caps.

---

## 4. Architecture

### 4.1 Title normalization and similarity

New module: `alerts/title_similarity.py`.

```python
def normalize_title_tokens(title: str) -> frozenset[str]: ...
def title_similarity(left: str, right: str) -> int | None: ...
```

`normalize_title_tokens`:

1. Unicode NFKC normalize, then `casefold()`.
2. Remove UUIDs, ISO-8601 timestamps, and hex tokens of length ≥ 8 that contain a digit.
3. Replace hyphens with spaces, strip punctuation, and tokenize with a simple ASCII-safe pattern that
   keeps dots inside domains/IPs (`[a-z0-9][a-z0-9._]*`).
4. Drop empty and purely numeric tokens (so `ALERT-123` and `ALERT-99` normalize to `{alert, ...}`).
5. Return a `frozenset`. Order is irrelevant; duplicate words do not inflate the score.

`title_similarity`:

- Returns `None` if either normalized title is empty.
- Otherwise returns `round(100 * |A ∩ B| / |A ∪ B|)` (Jaccard, 0–100).

Rationale: token Jaccard is deterministic, cheap on short titles, robust to word-order and volatile numeric
tokens, and easier to explain than SSDEEP on very short strings.

### 4.2 Correlation integration

`cases/correlation.py::alerts_fuzzy_similar_to_case`:

- Read case signals as `(payload_ssdeep, title)` pairs, still capped by `MAX_CORRELATION_OBS`.
- Precompute each case title's token set once per request.
- For each candidate, compute the payload score first. Only if it is at/above the payload threshold,
  evaluate the title gate for that same case signal.
- A candidate qualifies if **any single case signal** passes both thresholds. This avoids mixing a high
  payload score from one alert with a high title score from another.
- Report `SimilarAlert.similarity` as the maximum payload score among qualifying pairs (unchanged meaning).
- If the gate is disabled, behavior is exactly the current payload-only path.
- If either title is empty after normalization, treat the title gate as **pass** (payload-only) and
  document the fallback. Alert titles are required, so this is a legacy/malformed-data edge.

No database schema change. No migration. No backfill.

### 4.3 Settings

`amalthea/settings/base.py`:

```python
# Payload SSDEEP threshold (0-100). The shipped default is 60.
ALERT_FUZZY_SIMILARITY_THRESHOLD = int(os.getenv("AMALTHEA_ALERT_FUZZY_THRESHOLD", "60"))

# Title gate: require both payload and normalized-title similarity.
ALERT_FUZZY_TITLE_GATE_ENABLED = (
    os.getenv("AMALTHEA_ALERT_FUZZY_TITLE_GATE", "true").lower() == "true"
)
ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD = int(
    os.getenv("AMALTHEA_ALERT_FUZZY_TITLE_THRESHOLD", "70")
)
```

Correct the stale comments that describe 80 as the default.

---

## 5. Data model, API, UI

- **Data model:** none.
- **API:** none. `payload_ssdeep` remains internal; no title score is exposed.
- **UI:** no template change. The Similar tab’s existing “N% similar” remains the payload score. The
  title gate changes which rows qualify, not the rendered columns.

---

## 6. Phases and acceptance criteria

### Phase 1 — Title similarity primitive (`django-backend`)

| # | Task | AC |
|---|---|---|
| T1 | Add `alerts/title_similarity.py` with `normalize_title_tokens()` and `title_similarity()`. | Unit tests cover casefolding, UUIDs, ISO timestamps, hex IDs, numeric IDs, domains/IPs, empty titles, and duplicate tokens. |
| T2 | Add the two settings and correct the payload-threshold comments. | Defaults are `True` and `70`; env overrides work; payload default comment says 60. |
| T3 | Keep functions total and exception-safe. | No `RecursionError`/exception path; titles are already capped at 500 chars. |

### Phase 2 — Correlation gate (`django-backend`)

| # | Task | AC |
|---|---|---|
| C1 | Read case titles alongside hashes and precompute token sets. | No extra query per candidate; case-signal query remains capped. |
| C2 | Apply the pairwise gate: payload and title must pass against the same case alert. | A candidate with payload match to A and title match to B is excluded. |
| C3 | Keep the disabled-gate path byte-for-byte behaviorally equivalent to today. | Existing fuzzy tests pass with the gate disabled; default tests reflect the new gate. |
| C4 | Empty normalized title falls back to payload-only. | Test records the documented fallback. |
| C5 | Keep `SimilarAlert.similarity` as the payload score. | UI/tests still see 0–100 payload similarity. |

### Phase 3 — Tests (`qa-tester`)

| # | Task | AC |
|---|---|---|
| Q1 | Add title-normalization unit tests. | Volatile tokens do not change the token set; semantic tokens do. |
| Q2 | Add correlation tests: same payload + different title excluded; same event + timestamp/ID noise included; gate disabled restores old behavior; threshold boundary. | Each test is falsifiable against the gate. |
| Q3 | Add the cross-pair trap test. | High payload to A + high title to B does **not** qualify. |
| Q4 | Run the fuzzy suite, adjacent tenant/UI suites, `make check-fast`; then the full suite. | Green, with the full-suite result recorded. |

### Phase 4 — Documentation (`planner` / `django-backend`)

| # | Task | AC |
|---|---|---|
| D1 | Create `docs/runbook.md` with an “Alert fuzzy similarity” section. | Documents payload hash semantics, title gate, both env vars, the 60/70 defaults, empty-title fallback, and how to tune/disable. |
| D2 | Add a follow-up note to `PLAN-2026-10-20-alert-fuzzy-similarity.md`. | Records Option B and supersedes the FIND-05 recommendation. |
| D3 | Update `TODO.md` §12.12-fu1 after verification. | Closed with links to the plan, runbook, and verification report. |

---

## 7. Security

- Titles are already stored and rendered with Django auto-escaping; this change adds no new output path.
- Normalization uses simple regexes over bounded 500-char strings; no ReDoS-prone patterns.
- The title gate only removes candidates; tenant/org scoping is unchanged.
- The gate is applied after the payload score, so CPU work is bounded by candidates already passing the
  payload threshold.
- No new dependency.

## 8. Testing

- Unit: `normalize_title_tokens`, `title_similarity`.
- Conformance: `alerts_fuzzy_similar_to_case` with gate on/off, cross-pair trap, empty title, threshold
  boundary, cross-org isolation unchanged.
- Existing `TestTitleExcludedDecisionA` stays valid for **payload hashing**; add a correlation-level test
  proving the title gate distinguishes the two alerts.
- `make check-fast`; then the full suite (the repo standard after the 2026-10-15 lesson).

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Title gate causes false negatives when titles genuinely differ | Medium | Medium | Default threshold 70; env override; gate can be disabled. |
| 70 is an arbitrary default | Medium | Low | Document; tune from observed data; no rehash needed. |
| On-demand title comparison adds CPU to the Similar tab | Low | Low | Only runs for payload-qualified candidates; bounded by 500×1000; optimize with a stored fingerprint later if measured. |
| Empty-title fallback weakens precision | Low | Low | Alert titles are required; document and test the fallback. |
| Operators enable the gate on legacy data with noisy titles | Low | Medium | Runbook explains the trade-off and the disable switch. |

## 10. Open questions

1. Default title threshold: plan proposes **70**. Confirm or choose another value.
2. Empty-title behavior: plan proposes **fail-open** (payload-only). Confirm or choose fail-closed.
3. Should the Similar tab eventually show payload and title scores separately, or keep the single payload
   percentage? Plan keeps the current UI.

---

## 11. Approval

- [x] User approves the Option B design (on-demand title fingerprint, no migration/backfill).
- [x] User confirms the 70 title threshold and empty-title fallback.
- [x] Handoff to `django-backend` + `qa-tester`; verified in
  `docs/verification/VERIFY-2026-11-13-alert-fuzzy-title-gate.md`.
