# Runbook — Alert fuzzy similarity

This runbook documents the alert fuzzy similarity feature, including the payload SSDEEP hash,
the normalized-title gate, environment configuration, and operational tuning.

## 1. Overview

The "Similar alerts" card uses SSDEEP fuzzy hashing to surface unlinked alerts in the same
organisation that are semantically similar to the case's attached alerts. A normalized-title gate
was added to reduce false positives where two different events with similar payloads but different
titles score highly.

## 2. Payload SSDEEP hash semantics

- **When:** Computed once at ingest time, stored on the `Alert.payload_ssdeep` field.
- **What:** `compute_payload_ssdeep(summary, payload)` produces an SSDEEP-compatible hash
  from `summary` + canonicalized payload (volatile keys dropped, deterministic JSON sort).
- **Title exclusion:** `title` is intentionally excluded from the hash (Decision A, 2026-10-20).
  SIEM/EDR titles commonly embed per-event tokens (timestamps, IDs, UUIDs) that collapse
  the score far below the threshold for the "same event, different noise" case the feature
  exists to catch.
- **Empty hash:** If the payload cannot be hashed (oversize, overdeep, exception), the hash
  is stored as `""` and the alert is excluded from the fuzzy candidate set.
- **Backfill:** A one-time `manage.py backfill_alert_ssdeep` command populates empty hashes
  for pre-existing alerts. After the initial backfill, no further migration is needed.

## 3. Title gate configuration

The title gate requires **both** conditions to pass for a candidate alert to appear as similar:

| Setting | Env var | Default | Description |
|---|---|---|---|
| `ALERT_FUZZY_SIMILARITY_THRESHOLD` | `AMALTHEA_ALERT_FUZZY_THRESHOLD` | **60** | Payload SSDEEP score threshold (0–100). The shipped default was lowered from 80 to 60 after manual evaluation (more recall, more candidate matches). |
| `ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD` | `AMALTHEA_ALERT_FUZZY_TITLE_THRESHOLD` | **70** | Normalized title Jaccard similarity threshold (0–100). A candidate must score at least this high on title similarity against the **same** case alert. |
| `ALERT_FUZZY_TITLE_GATE_ENABLED` | `AMALTHEA_ALERT_FUZZY_TITLE_GATE` | **true** | Toggle the title gate on or off. Set to `false` to restore payload-only behavior. |

### How the gate works

For each candidate alert:

1. Compute the max `ppdeep.compare` score against all of the case's attached alerts.
2. If the max payload score >= `ALERT_FUZZY_SIMILARITY_THRESHOLD` (60), evaluate the title gate:
   - For **each** case alert that contributed to the payload score, compute
     `title_similarity(candidate_title, case_alert_title)` using Jaccard on normalized token sets.
   - If **any single case alert** passes **both** the payload threshold AND the title threshold,
     the candidate qualifies.
   - This "same-case-signal" requirement prevents mixing a high payload score from one alert with
     a high title score from another alert.
3. If the title gate is disabled (`ALERT_FUZZY_TITLE_GATE_ENABLED=False`), the candidate qualifies
   if the max payload score alone >= threshold (restoring the original payload-only behavior).
4. If either title normalizes to empty after the token-stripping process, the title gate is treated
   as **pass** (payload-only fallback). Alert titles are required fields, so this is a legacy/malformed-data edge case.

### SimilarAlert.similarity field

The `similarity` value on each returned `SimilarAlert` is the **maximum ppdeep.compare score among
the case alerts that pass both gates** for that candidate. It is **not** a combined "payload + title"
score. With the title gate disabled it reduces to the old maximum across all case alerts.

### Tuning the thresholds in production

```bash
# Lower the payload threshold (more recalls, more candidates)
export AMALTHEA_ALERT_FUZZY_THRESHOLD=50

# Raise the title threshold (more precision, fewer candidates)
export AMALTHEA_ALERT_FUZZY_TITLE_THRESHOLD=80

# Disable the title gate entirely (restore original behavior)
export AMALTHEA_ALERT_FUZZY_TITLE_GATE=false
```

After changing any setting, restart the application (Django settings are read at startup).

### No backfill required

The title gate computes token sets on demand from existing `title` fields. No database schema
change, no migration, and no backfill are needed. If performance becomes a concern in the future,
a stored fingerprint column is a follow-up design decision.

## 4. Security & operational notes

- **No new output paths:** Titles are already stored and rendered with Django auto-escaping; this
  change adds no new user-visible output.
- **ReDoS-safe:** Normalization uses simple regexes over bounded 500-char strings; no
  ReDoS-prone patterns.
- **Tenant isolation:** The title gate is applied after org scoping; cross-org behavior is
  unchanged.
- **CPU bounded:** Title comparison only runs for candidates that already pass the payload
  threshold, so CPU work is bounded by the existing `MAX_FUZZY_COMPARISONS × |case hashes|` cap.