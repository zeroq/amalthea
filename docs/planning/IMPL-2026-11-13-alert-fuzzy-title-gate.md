# Implementation Brief — Alert fuzzy title gate

**Plan:** `docs/planning/PLAN-2026-11-13-alert-fuzzy-title-gate.md`
**Decision:** Option B — pairwise payload + normalized-title gate, computed on demand.
**Handoff to:** `django-backend` (implementation + tests + runbook); `qa-tester`/`verifier` after.

## Tasks

1. **`alerts/title_similarity.py` (new)**
   - `normalize_title_tokens(title: str) -> frozenset[str]`
   - `title_similarity(left: str, right: str) -> int | None`
   - Normalize: NFKC + casefold; strip UUIDs, ISO timestamps, hex ≥8 with a digit; hyphens to spaces;
     tokenize `[a-z0-9][a-z0-9._]*`; drop purely numeric/empty tokens; return a frozenset.
   - Similarity: Jaccard percentage, `round(100 * |A∩B| / |A∪B|)`; `None` if either set is empty.

2. **`amalthea/settings/base.py`**
   - Keep `ALERT_FUZZY_SIMILARITY_THRESHOLD` default 60; fix comments that claim 80.
   - Add:
     - `ALERT_FUZZY_TITLE_GATE_ENABLED` default `True` (`AMALTHEA_ALERT_FUZZY_TITLE_GATE`)
     - `ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD` default `70` (`AMALTHEA_ALERT_FUZZY_TITLE_THRESHOLD`)

3. **`cases/correlation.py::alerts_fuzzy_similar_to_case`**
   - Read case signals as `(payload_ssdeep, title)`; precompute case title token sets once.
   - Evaluate title only after the payload score passes.
   - Require both thresholds against the **same** case signal; no cross-pair mixing.
   - Empty normalized title on either side = title gate passes (payload-only fallback).
   - Gate disabled = current behavior unchanged.
   - `SimilarAlert.similarity` stays the payload score.

4. **Tests — `tests/conformance/test_alert_fuzzy_similarity.py`**
   - Normalization/unit cases: UUID, timestamp, hex, numeric IDs, domains/IPs, empty, duplicates.
   - Same payload + different titles excluded with gate on; included with gate off.
   - Same event with volatile title tokens included.
   - Cross-pair trap: payload matches A, title matches B → excluded.
   - Threshold boundary at 70; empty-title fallback.
   - Existing payload-hash Decision A tests stay valid.

5. **Docs**
   - Create `docs/runbook.md` with an "Alert fuzzy similarity" section: 60/70 defaults, env vars,
     title gate behavior, empty-title fallback, tuning/disable, no backfill needed.
   - Add a short note to `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md` superseding the
     FIND-05 recommendation with Option B.
   - Do **not** edit `TODO.md`/`COMPLETED.md`; closure happens after verification.

## Constraints

- No schema/migration/backfill; no API/UI contract change.
- No new dependency.
- Keep tenant scoping and `MAX_FUZZY_COMPARISONS` / `MAX_CORRELATION_OBS` caps intact.
- Run focused fuzzy/tenant/UI tests and `make check-fast`; record results. Do not run the full suite unless asked.

## Acceptance

- Different-title/same-payload alerts no longer appear as similar by default.
- Volatile title tokens do not cause false negatives.
- Gate can be disabled via env without code changes.
- No cross-org behavior change.
- Runbook documents the precision/recall trade-off.
