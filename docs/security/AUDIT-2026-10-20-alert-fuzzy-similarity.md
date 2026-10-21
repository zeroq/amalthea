# Security Audit Report: Alert Fuzzy Similarity Feature

**Date:** 2026-10-20  
**Auditor:** security-auditor  
**Scope:** New `ppdeep` dependency, `alerts/hashing.py`, `Alert.payload_ssdeep` field, ingest wiring, correlation helper `alerts_fuzzy_similar_to_case`, backfill command, and UI integration.  
**Verdict:** **APPROVE WITH NOTES**

---

## Summary

The fuzzy similarity feature adds SSDEEP-compatible hashing of alert payloads to enable detection of near-duplicate alerts across cases. The implementation is well-structured with appropriate bounds checking, tenant isolation, and failure-safe defaults. The primary third-party dependency `ppdeep==20260221` is correctly pinned with a verified SHA-256 hash. No critical vulnerabilities were found. Several medium-severity findings and one high-severity finding related to canonicalization collision risk warrant attention before production deployment.

---

## Scope

| Component | Files |
|-----------|-------|
| Dependency | `requirements/base.txt` (ppdeep), `requirements/dev.txt`, `.github/workflows/ci.yml` |
| Hashing logic | `alerts/hashing.py` |
| Model & migration | `alerts/models.py`, `alerts/migrations/0017_alert_payload_ssdeep.py` |
| Ingest paths | `ingest/pipeline.py::store_ingested_alert`, `alerts/views.py::_create_alert` |
| Correlation | `cases/correlation.py::alerts_fuzzy_similar_to_case` |
| Backfill | `alerts/management/commands/backfill_alert_ssdeep.py` |
| UI wiring | `ui/views.py::case_detail` (Similar tab), `ui/views.py::case_alert_link` |
| Tests | `tests/conformance/test_alert_fuzzy_similarity.py`, `tests/conformance/test_tenant_scoping.py` |
| Settings | `amalthea/settings/base.py` |
| Plan reference | `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md` |

---

## Findings

### FIND-01: Supply Chain — `ppdeep` hash verified, no transitive deps
**Severity:** Info  
**Evidence:** `requirements/base.txt:603-605`

The dependency `ppdeep==20260221` is pinned with exact version and a single SHA-256 hash:
```
ppdeep==20260221 \
    --hash=sha256:e46d5130afeb0c73155568df9fcab41e2ff1ff04e95a29970b3da5b129f0ec34
```

CI installs via `pip install --require-hashes -r requirements/dev.txt` (`.github/workflows/ci.yml:66`). `ppdeep` is a pure-Python package with no transitive dependencies (verified via `pip show ppdeep` and PyPI metadata). The hash is present in `base.txt` (runtime deps) and inherited by `dev.txt` via `-r base.txt`. No separate lock file exists; the hashed requirements file serves as the lock.

**Recommendation:** None required. Supply chain hygiene is satisfied.

---

### FIND-02: DoS — Recursive canonicalization and depth check lack iteration guards
**Severity:** Medium  
**Evidence:** `alerts/hashing.py:65-92` (`_canonicalize`), `alerts/hashing.py:129-137` (`_depth`)

Both `_canonicalize` and `_depth` use unbounded recursion on attacker-controlled JSON structures. While `_check_bounds` (called before hashing) enforces `WEBHOOK_MAX_DEPTH` (default 30), the depth check itself is recursive and could hit `RecursionError` on a pathologically deep structure *before* the bound is enforced. The `compute_payload_ssdeep` wrapper catches `RecursionError` and returns `""`, preventing a 500, but the CPU cost of the deep recursion is still incurred per request.

Similarly, `_canonicalize` recurses on all nested dicts/lists without an explicit depth limit during the canonicalization pass (the bound check runs first, but a structure exactly at the depth limit will still recurse 30+ levels).

**Recommendation:** 
- Convert `_depth` to the iterative implementation already used in `cases/correlation.py:_json_depth` (lines 62-75).
- Add an explicit recursion-depth guard in `_canonicalize` (e.g., pass `current_depth` and bail out at `max_depth + 5` margin) to avoid redundant traversal on overdeep inputs.

---

### FIND-03: DoS — `ppdeep.hash()` / `ppdeep.compare()` CPU unbounded on large canonical text
**Severity:** Medium  
**Evidence:** `alerts/hashing.py:183` (`ppdeep.hash(full_text)`), `cases/correlation.py:425` (`ppdeep.compare`)

`ppdeep` (SSDEEP) computes rolling hashes over the entire input text. The canonical text length is bounded by `WEBHOOK_MAX_BODY_SIZE` (default 5 MiB), but a 5 MiB input to `ppdeep.hash()` is computationally expensive (O(n) with a large constant). The same applies to `ppdeep.compare()` in the correlation loop: up to `MAX_FUZZY_COMPARISONS=500` candidates × up to `MAX_CORRELATION_OBS=1000` case hashes = 500,000 comparisons worst-case, though in practice case hashes are far fewer.

The correlation helper is only invoked when the "Similar" tab is rendered (`ui/views.py:1084-1105`), not on the ingest hot path. However, a malicious superAdmin or analyst could craft a case with many attached alerts (each with large payloads) and trigger the Similar tab, causing significant CPU load.

**Recommendation:**
- Add a configurable `ALERT_FUZZY_MAX_TEXT_SIZE` (e.g., 256 KiB) to truncate canonical text before hashing.
- In `alerts_fuzzy_similar_to_case`, cap `case_hashes` at a lower bound (e.g., 50) and document the effective comparison ceiling.
- Consider moving the correlation computation to a background Celery task with a cached result for the Similar tab.

---

### FIND-04: Cross-Tenant Leakage — `case_alert_link` correctly scoped, but superAdmin bypass untested
**Severity:** Low  
**Evidence:** `ui/views.py:554-579` (`case_alert_link`), `identity/scoping.py:223-251` (`resolve_alert`)

The `case_alert_link` view uses `resolve_alert(request.user, alert_id, write=True)` which enforces org scoping via `org_q`. For superAdmin, `org_q` returns `Q()` (no filter), allowing access to any org's alerts. This is by design (ADR-004: superAdmin sees all, audited). The audit log records `actor_org` and `target_org` for cross-org reads.

However, the conformance test `test_cross_org_link_post_is_404_and_leaves_alert_unlinked` only tests a regular analyst. There is no test verifying that a superAdmin *can* link a cross-org alert (expected behavior) and that the audit log captures the cross-org action.

**Recommendation:** Add a test case for superAdmin cross-org link flow to confirm audit logging works as intended.

---

### FIND-05: Canonicalization Correctness — Title exclusion (Decision A) widens false-positive risk
**Severity:** **High**  
**Evidence:** `alerts/hashing.py:145-186` (`compute_payload_ssdeep`), `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md:83-92` (Decision A)

Per Decision (A), `title` is intentionally excluded from the hash because SIEM/EDR titles often embed per-event tokens (timestamps, IDs) that collapse SSDEEP scores below 80. The plan documents measured scores:
- Same title + volatile-only payload diff → **100**
- Title with timestamp → **33**
- Title with ID → **22**
- Different title, identical payload → **22**

**Risk:** Two *different* events with similar payloads but different titles will now hash identically (score 100) and be falsely linked. The 80 threshold is the only guard. For example, two phishing emails with different sender/URL but same template structure (common in campaigns) could score ≥80 and be incorrectly associated.

The test `TestTitleExcludedDecisionA` confirms identical summary+payload → identical hash regardless of title, but there is no test measuring the false-positive rate against real-world payload distributions.

**Recommendation:**
- **Short term:** Add a configuration flag `ALERT_FUZZY_INCLUDE_TITLE` (default `False` to preserve Decision A) so orgs can opt-in to title inclusion after evaluating their false-positive rate.
- **Medium term:** Implement a "title fingerprint" — hash the title separately and require *both* payload similarity ≥80 AND title similarity ≥threshold (or title exact-match) for a match. This preserves the payload signal while adding a title discriminator.
- **Documentation:** Clearly document the false-positive trade-off in the deployment runbook.

**Update (2026-10-21):** The operator lowered the default `ALERT_FUZZY_SIMILARITY_THRESHOLD`
from `80` to `60` in `amalthea/settings/base.py` after manual evaluation ("working fine"). This is an
**accepted, deliberate product decision** on the FIND-05 trade-off: the recall/precision balance is
moved toward recall (more candidate matches surfaced), so the "80 threshold is the only guard"
statement above is now "the configured threshold (default 60) is the only guard". The recommended
opt-in `ALERT_FUZZY_INCLUDE_TITLE` flag and title-fingerprint work remain the correct hardening
path and are unchanged; the follow-up tracking in `TODO.md` §12.12-fu1 stands.

---

### FIND-06: Canonicalization Correctness — Volatile key list may be incomplete for some sources
**Severity:** Medium  
**Evidence:** `alerts/hashing.py:27-57` (`VOLATILE_KEYS`)

`VOLATILE_KEYS` covers common timestamp/ID fields but is a static list. Uncommon or vendor-specific volatile fields (e.g., `event_id`, `flow_id`, `session_id`, `transaction_id`, `correlation_id`, `message_id`, `log_id`, `sequence`, `epoch`, `clock`, `uptime`, `counter`, `nonce`, `token`, `session`, `request_id`) may not be covered, causing same-event alerts to produce different hashes and miss matches (false negatives).

**Recommendation:** 
- Make `VOLATILE_KEYS` extensible via a Django setting (e.g., `ALERT_FUZZY_EXTRA_VOLATILE_KEYS`) so orgs can add vendor-specific keys without forking.
- Consider a heuristic: drop any key whose value matches a UUID/timestamp/epoch regex pattern, in addition to the static list.

---

### FIND-07: Backfill Command — `--force` does not blank hashes on recomputation failure (deviation)
**Severity:** Low  
**Evidence:** `alerts/management/commands/backfill_alert_ssdeep.py:98-108`, `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md:294-296` (Deviation §11.2)

When `--force` is used and `compute_payload_ssdeep` returns `""` (unhashable), the existing hash is preserved rather than being blanked. This is a conservative deviation documented in the plan. The test `TestBackfillCommand` verifies this behavior.

**Risk:** If a previously hashable alert becomes unhashable (e.g., due to a settings change lowering `WEBHOOK_MAX_BODY_SIZE`), the stale hash remains and may produce incorrect similarity scores.

**Recommendation:** Document this behavior prominently in the command's help text and the runbook. Consider adding a `--clear-on-failure` flag for explicit opt-in.

---

### FIND-08: Information Exposure — `payload_ssdeep` not exposed in API, similarity score only in scoped UI
**Severity:** Info  
**Evidence:** `core/serializers.py:54-80` (`alert_json`), `ui/views.py:1084-1105` (`case_detail` Similar tab)

The `payload_ssdeep` field is **not** included in `alert_json` (the DRF serializer for `GET /api/v1/alert/{id}` and list endpoints). The similarity score (0–100) is only rendered in the "Similar" tab of the case detail UI, which is scoped via `case_visible_q` and `org_q`. The `case_alert_link` POST endpoint uses `resolve_alert` with `write=True`, enforcing org scoping.

No sensitive payload content is leaked through the hash (SSDEEP is not reversible) or the similarity score.

**Recommendation:** None required. Current exposure is appropriate.

---

### FIND-09: OWASP — No SQL injection, deserialization, or authz bypass in new code
**Severity:** Info  
**Evidence:** All new query paths use Django ORM (`filter`, `exclude`, `select_related`). No raw SQL. JSON deserialization is handled by DRF/request.data before reaching the hashing logic. All views use `resolve_alert`, `resolve_case`, `org_q`, `case_visible_q` from `identity.scoping` for authorization.

**Recommendation:** None required. Secure coding practices followed.

---

### FIND-10: DoS — Backfill command uses per-row `save()` instead of `bulk_update`
**Severity:** Low  
**Evidence:** `alerts/management/commands/backfill_alert_ssdeep.py:116-118`, `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md:291-293` (Deviation §11.1)

The backfill command iterates with `.iterator()` and calls `alert.save(update_fields=["payload_ssdeep", "updated_at"])` per row. For large datasets (100k+ alerts), this is significantly slower than batched `bulk_update`. The plan acknowledges this as a deliberate trade-off for a one-time command.

**Recommendation:** None required for security, but consider optimizing for operational efficiency if backfilling large production datasets.

---

### FIND-11: Supply Chain — No `base.in` / lock recipe; manual hash maintenance required
**Severity:** Low  
**Evidence:** `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md:55-61` (§3.1), `requirements/base.txt` header

The plan notes: "The pinned requirements have no `base.in` and no `lock` target recipe (see TODO: `make hooks`/`make lock` are empty `.PHONY` targets)." The `ppdeep` hash was added manually to `base.txt`. Future dependency updates require manual `pip-compile` or manual hash addition, risking drift.

**Recommendation:** Implement the `make lock` target (using `pip-compile --generate-hashes`) to automate hash maintenance and prevent drift. This is a supply-chain hygiene improvement, not a vulnerability.

---

### FIND-12: Canonicalization — Scalar list sorting may reorder semantically significant arrays
**Severity:** Low  
**Evidence:** `alerts/hashing.py:82-90` (`_canonicalize`)

Lists of scalars are sorted (`sorted(canonicalized)`). If a source system emits an array where order is semantically meaningful (e.g., `["step1", "step2", "step3"]` vs `["step3", "step2", "step1"]` representing different attack chains), sorting will collapse them to the same canonical form, causing false positives.

**Recommendation:** Document this behavior. Consider making list sorting configurable per ingestion source (via `IngestionSource.mapping_config`) in a future iteration.

---

---

## Risk Verdict

### **APPROVE WITH NOTES**

The feature is fundamentally sound with proper tenant isolation, bounded resource usage, and secure defaults. The **High** finding (FIND-05) is a design trade-off documented in the plan with a mitigating threshold; it should be addressed via the recommended configuration flag and title-fingerprint follow-up before enabling broadly in production. The **Medium** findings (FIND-02, FIND-03, FIND-06) are operational hardening items that should be resolved in the next sprint.

---

## Remediation Checklist (High + Medium)

| ID | Finding | Remediation | Target |
|----|---------|-------------|--------|
| FIND-05 | Title exclusion → false positives | Add `ALERT_FUZZY_INCLUDE_TITLE` setting; prototype title-fingerprint matching | Before production enablement |
| FIND-02 | Recursive depth check | Replace `_depth` with iterative `_json_depth`; add depth guard in `_canonicalize` | Next sprint |
| FIND-03 | `ppdeep` CPU on large inputs | Add `ALERT_FUZZY_MAX_TEXT_SIZE` truncation; lower `case_hashes` cap; consider async | Next sprint |
| FIND-06 | Volatile key list incomplete | Add `ALERT_FUZZY_EXTRA_VOLATILE_KEYS` setting; consider regex heuristic | Next sprint |

---

## Report Path

`docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md`
