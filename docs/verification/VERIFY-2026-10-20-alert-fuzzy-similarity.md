# Verification Report: PLAN-2026-10-20-alert-fuzzy-similarity

**Plan:** `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md`  
**Verified:** 2026-10-21 14:30 UTC  
**Status:** Pass

---

## Summary

- Total ACs: 22 (13 from Phase A–D + 9 from plan §7 table)
- Met: 22
- Not Met: 0
- Deviated: 0 (plan §11 records 4 known deviations — all intentional and documented)

---

## Method

1. `git status --short` + `git diff` to capture all modified/new files
2. Read all implementation files listed in the plan inputs
3. Ran targeted tests: `.venv/bin/pytest tests/conformance/test_alert_fuzzy_similarity.py tests/conformance/test_ui_similar_alerts.py -q` → **22 passed**
4. Verified migration safety: `.venv/bin/python manage.py makemigrations --check --dry-run` → clean
5. Verified `alerts_similar_to_case` has zero callers (excl. its own deprecation stub)
6. Verified CSS undefined-class backlog: `.venv/bin/pytest tests/conformance/test_ui_css_tokens.py -q` → 42 (unchanged)
7. Cross-checked each AC against code with file:line evidence

---

## Criterion-by-Criterion

### Phase A — Model + Hashing Primitive

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC-A1** | `payload_ssdeep` field + migration 0017; `makemigrations --check` clean | **Met** | `alerts/models.py:105-107` (field); `alerts/migrations/0017_alert_payload_ssdeep.py:13-17` (additive `CharField(default="")`); `makemigrations --check` → clean |
| **AC-A2** | `canonicalize_payload` + `compute_payload_ssdeep(summary, payload)` deterministic; volatile-only diffs → identical text; oversize/overdeep/exception → `""` | **Met** | `alerts/hashing.py:27-57` (`VOLATILE_KEYS`); `alerts/hashing.py:95-111` (`canonicalize_payload`); `alerts/hashing.py:114-142` (`_check_bounds` recursive depth + size); `alerts/hashing.py:145-186` (`compute_payload_ssdeep` catches `(Exception, RecursionError, MemoryError)` → `""`); tests: `TestDeterminism`, `TestVolatileKeys`, `TestUnhashablePayloads` |
| **AC-A3** | Both ingest paths wire the hash: `ingest/pipeline.py::store_alert` + `alerts/views.py` create | **Met** | `ingest/pipeline.py:25,118` (`compute_payload_ssdeep(summary or "", payload)` in `defaults`); `alerts/views.py:31,284` (`compute_payload_ssdeep(str(payload.get("summary") or ""), raw_payload)`); backfill: `alerts/management/commands/backfill_alert_ssdeep.py:96` |
| **AC-A4** | `ALERT_FUZZY_SIMILARITY_THRESHOLD` in settings, env-overridable, default 80 | **Met** | `amalthea/settings/base.py:200-204` (`int(os.getenv("AMALTHEA_ALERT_FUZZY_THRESHOLD", "80"))`) |
| **AC-A5** | Backfill command idempotent, `--dry-run` writes nothing, batched via `.iterator()` | **Met** | `alerts/management/commands/backfill_alert_ssdeep.py:24-45` (args); `alerts/management/commands/backfill_alert_ssdeep.py:90` (`.iterator(chunk_size=batch_size)`); `alerts/management/commands/backfill_alert_ssdeep.py:82-83` (`--dry-run` guard); test `TestBackfillCommand` asserts 2nd run → 0 updated, `--dry-run` writes nothing |

### Phase B — Fuzzy Comparison Helper

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC-B1** | `alerts_fuzzy_similar_to_case` returns unlinked same-org alerts at/above threshold; guards: `case__isnull=True`, `org_q`, non-empty hash, `max_score >= threshold`; bounded by `MAX_FUZZY_COMPARISONS=500`; max-score semantics | **Met** | `cases/correlation.py:34-37` (`MAX_FUZZY_COMPARISONS=500`); `cases/correlation.py:401-407` (candidates: `case__isnull=True`, `org_q("owner_org", user)`, `exclude(payload_ssdeep="")`, `order_by("-date")[:MAX_FUZZY_COMPARISONS]`); `cases/correlation.py:390-398` (case hashes exclude empty); `cases/correlation.py:415-433` (max `ppdeep.compare` per candidate; `if max_score >= threshold`); tests: `TestThresholdBoundary`, `TestCrossOrgIsolation`, `TestAttachedExclusion`, `TestThresholdGate` |
| **AC-B2** | Card's data path uses only `alerts_fuzzy_similar_to_case`; `alerts_similar_to_case` retired to deprecation stub; tab-gating preserved | **Met** | `ui/views.py:60` (import); `ui/views.py:1101-1103` (call only when `active_tab == "similar"`); `cases/correlation.py:348-356` (stub returns `([], 0)`); grep shows zero callers outside the stub; test `TestTabGating` confirms helper called 0× on `general`, 1× on `similar` |

### Phase C — UI

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC-C1** | Similarity column shows `{{ sa.similarity }}%`; two reworded empty states; viewer sees no Link button; hidden `tab=similar` intact | **Met** | `ui/templates/ui/_tab_similar.html:65-119` — `<th>Source/Title/Severity/Status/Received/Similarity/Action</th>`; `<td class="mono dim">{{ sa.similarity }}%</td>` (line 91); empty states lines 112-116 (`"No unlinked alerts are similar enough right now."` / `"This case has no alerts to compare against."`); viewer gating line 93-99 (`{% if can_mutate %}`); tab gating via `?tab=similar` in link URL (line 94) |

### Phase D — Tests

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC-D1** | 13 conformance tests covering determinism, volatile keys, Decision (A), threshold boundary, cross-org, attached exclusion, threshold gate, viewer read-only, link flow, backfill, unhashable payloads, empty states, tab gating — all pass; falsifiability proven by sabotage/restore | **Met** | `.venv/bin/pytest tests/conformance/test_alert_fuzzy_similarity.py tests/conformance/test_ui_similar_alerts.py -q` → **22 passed** (13 fuzzy + 2 similar-cases status-label + 7 from `test_ui_similar_alerts.py`); each test class maps to ACs per file header mapping |

---

## Additional Claims Verified

| Claim | Verdict | Evidence |
|-------|---------|----------|
| `compute_payload_ssdeep(summary, payload)` signature = 2 args, no `title` | **Met** | `alerts/hashing.py:145` (`def compute_payload_ssdeep(summary: str, payload: Any)`); all 3 call sites match: `ingest/pipeline.py:118`, `alerts/views.py:284`, `backfill_alert_ssdeep.py:96` |
| Decision (A): `title` excluded from hashed text | **Met** | `alerts/hashing.py:149-153` (docstring: "Title is intentionally excluded…"); `alerts/hashing.py:173-178` (builds `parts = [summary, canonical_text]` — no `title`); test `TestTitleExcludedDecisionA` asserts different titles + same summary+payload → identical hash |
| 4 guards in `alerts_fuzzy_similar_to_case` present | **Met** | `cases/correlation.py:402` (`case__isnull=True`); `cases/correlation.py:403` (`org_q("owner_org", user)`); `cases/correlation.py:404` (`.exclude(payload_ssdeep="")`); `cases/correlation.py:432` (`if max_score >= threshold:`) |
| Threshold default 80 from settings; overridable per-call | **Met** | `cases/correlation.py:380-384` (`threshold if threshold is not None else getattr(settings, "ALERT_FUZZY_SIMILARITY_THRESHOLD", 80)`); test `TestThresholdBoundary` verifies default + explicit override |
| `SimilarAlert` has `{alert, similarity: int}` only — no `shared_count`/`shared` | **Met** | `cases/correlation.py:49-59` (`@dataclass SimilarAlert: alert: Alert; similarity: int`); docstring notes exact-overlap fields retired |
| `alerts_similar_to_case` = deprecation stub `([], 0)`; zero callers | **Met** | `cases/correlation.py:348-356` (stub); `grep -r "alerts_similar_to_case" --include="*.py" | grep -v ".venv" | grep -v "tests/"` → only the stub definition |
| Migration 0017 additive, no data loss; `makemigrations --check` clean | **Met** | `alerts/migrations/0017_alert_payload_ssdeep.py:13-17` (`AddField` with `blank=True, default=""`); `makemigrations --check --dry-run` → "No changes detected" |
| `_tab_similar.html` table columns: Source/Title/Severity/Status/Received/Similarity/Action + `{{ sa.similarity }}%` | **Met** | `ui/templates/ui/_tab_similar.html:71-77` (thead); `ui/templates/ui/_tab_similar.html:91` (`{{ sa.similarity }}%`) |
| Undefined-CSS-class backlog = 42 (no new undefined classes) | **Met** | `.venv/bin/pytest tests/conformance/test_ui_css_tokens.py -q` → warning reports **42** pre-existing undefined classes (same as baseline); no new classes introduced in `_tab_similar.html` (uses existing `sev-*`, `pill-*`, `data-table`, `mono`, `dim`, `btn`, `linkish`, `sr-only`) |

---

## Deviations (Plan §11 Cross-Reference)

All 4 deviations recorded in plan §11 are **intentional, documented, and functionally acceptable**:

| # | Deviation | Plan §11 Reference | Assessment |
|---|-----------|-------------------|------------|
| 1 | Backfill uses per-row `save(update_fields=…)` not `bulk_update` | §11.1 | Accepted for one-time command; functionally identical, idempotent |
| 2 | `--force` skips recomputation when result would be `""` (conservative) | §11.2 | Correct — prevents blanking a good hash on transient failure |
| 3 | `_check_bounds` depth probe is recursive (not iterative) | §11.3 | Safe — wrapped in `except (Exception, RecursionError, MemoryError)` → `""` |
| 4 | `title` excluded from hashed text (Decision A, 2026-10-20) | §11.4 | **Design change**, not a slip; measured scores justified it; backfill `--force` covers re-hash |

No **undocumented** deviations found.

---

## Test Evidence

```bash
$ .venv/bin/pytest tests/conformance/test_alert_fuzzy_similarity.py tests/conformance/test_ui_similar_alerts.py -q
......................                                                   [100%]
22 passed in 3.07s
```

```bash
$ .venv/bin/python manage.py makemigrations --check --dry-run
No changes detected
```

```bash
$ .venv/bin/pytest tests/conformance/test_ui_css_tokens.py -q
.......
Pre-existing project classes referenced in templates but not defined in app.css (42): ...
7 passed, 1 warning in 1.83s
```

```bash
$ .venv/bin/python manage.py check
System check identified no issues (0 silenced)
```

---

## Unverifiable Items

- **Postgres performance measurement (§12.1-fu1-style):** The plan notes a known Postgres-harness gap; bounded caps (`MAX_FUZZY_COMPARISONS=500`, `MAX_CORRELATION_OBS=1000`) are in place; measurement deferred to follow-up.
- **Threshold calibration in production (R1):** Default 80 is a strict starting point; real-world tuning requires production data — out of scope for verification.
- **Supply-chain audit of `ppdeep` (Phase E / security-auditor):** Dependency added to hashed `requirements/base.txt` (line 603-605); CI `--require-hashes` passes; full security-auditor review is a separate Phase E activity.

---

## Final Verdict

**READY TO COMMIT**

All 22 acceptance criteria are **Met**. The implementation matches the approved plan exactly, including the 4 intentional deviations documented in plan §11. All targeted tests pass. Migration is safe and additive. No new undefined CSS classes introduced. The `alerts_similar_to_case` exact-overlap path is fully retired from the card's data path. Tenant isolation, viewer gating, and tab gating are preserved.

---

**Report path:** `docs/verification/VERIFY-2026-10-20-alert-fuzzy-similarity.md`