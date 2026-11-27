"""Shared correlation caps and auto-bundle constants."""

# Hard cap on observable IDs fetched before the CaseObservable join (M2).
# Reuses existing cap constants - no new constant introduced.
# 5 MiB payload can hold thousands of small observables; 1000 bounds memory/CPU.
MAX_CORRELATION_OBS = 1000

# Hard cap on candidate unlinked alerts compared for fuzzy similarity (Phase B).
# Candidate set: unlinked + same-org + non-empty hash, ordered -date, sliced here.
# Comparison cost: len(case_hashes) * len(candidates) ppdeep.compare calls.
MAX_FUZZY_COMPARISONS = 500


# --- Auto-bundle service constants ---

AUTO_BUNDLE_SOURCE = "ingest:auto-bundle"
AUTO_BUNDLE_WINDOW_MAX_MINUTES = 60


# Maximum number of similar alerts to return from the helper.
SIMILAR_CLOSED_ALERTS_LIMIT = 20
