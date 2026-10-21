# BRIEF — Fuzzy (SSDEEP) alert similarity for the case "Similar" tab

**Plan:** `docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md` (the plan is authoritative; this
brief is the handoff). Read the plan's §3 (decisions), §4 (model), §7 (phases + ACs), §9 (locked Q1–Q8)
before starting.

**Locked decisions:** Q1 `ppdeep`; Q2 canonicalized projection; Q3 global threshold, **default 80**;
Q4 **replace** the exact-signal basis of the "Similar alerts" card; Q5 batched idempotent management
command backfill; Q6 both ingest paths via one helper; Q7 `MAX_FUZZY_COMPARISONS = 500`; Q8 `Alert` only.

---

## Ground truth (verified this session)
- `Alert` (`alerts/models.py:53`) extends `UUIDModel, TimeStampedModel`; **no custom `save()`**; has
  `raw_payload` (JSONField), `title`, `summary`, `correlation_key`, `owner_org`, `case` (FK nullable).
  Latest migration `alerts/migrations/0016_seed_closed_alert_status.py` → new one is **0017**.
- Ingest seam: `ingest/pipeline.py::store_alert` — `Alert.objects.get_or_create(..., defaults={...})`
  around line 117. Set the hash inside `defaults` (only on create).
- API create seam: `alerts/views.py` ~line 282 `Alert.objects.create(...)`.
- `observables/extractor.py::json_leaves(payload)` flattens JSON to newline-joined text;
  `_walk` is the internal walker. Reuse the *concept*; canonicalization is new code.
- Candidate-scoping precedents: `cases/correlation.py::candidate_cases_for_alert` bounds payloads by
  `settings.WEBHOOK_MAX_BODY_SIZE` / `WEBHOOK_MAX_DEPTH` and catches `(Exception, RecursionError,
  MemoryError)`. `MAX_CORRELATION_OBS = 1000` already exists there.
- Settings pattern: `amalthea/settings/base.py` near lines 191–198 (`WEBHOOK_MAX_*`, all
  `os.getenv(...)`).
- **No management commands exist yet** — this is the first; create
  `alerts/management/__init__.py`, `alerts/management/commands/__init__.py`,
  `alerts/management/commands/backfill_alert_ssdeep.py`.
- Requirements: `requirements/base.txt` is **hash-pinned**, installed with
  `pip install --require-hashes -r requirements/dev.txt` (CI `.github/workflows/ci.yml:66`). There is
  **no `base.in` and no `lock` recipe** — add `ppdeep` + its `--hash=sha256:…` lines to `base.txt`
  (runtime dep) and mirror into `dev.txt` if dev installs resolve through it. CI's `--require-hashes`
  MUST still pass.
- mypy pinned scope already includes `alerts`, `cases`, `observables`, `ingest` (see
  `pyproject.toml` `[tool.mypy] files`), so new modules there are type-checked.
- ruff formats Python fences in Markdown → run `ruff format` on any `.md` you edit.

---

## Phase A — model + hashing primitive (django-backend)

### A1 — `Alert.payload_ssdeep` + migration 0017
- Add to `alerts/models.py::Alert`:
  ```python
  payload_ssdeep = models.CharField(max_length=160, blank=True, default="")
  ```
  Place it near `correlation_key`/`raw_payload`. Add a short comment: SSDEEP-compatible hash of the
  canonicalized payload; internal only (not on the wire); `""` = not fuzzy-comparable.
- Generate `alerts/migrations/0017_alert_payload_ssdeep.py` (additive field, keep default="").
- **AC-A1:** `manage.py makemigrations --check --dry-run` clean; `manage.py check` clean.

### A2 — `alerts/hashing.py`
New module with:
- `VOLATILE_KEYS: frozenset[str]` — case-insensitive set, e.g. `_id, id, uuid, date, timestamp, time,
  "@timestamp", created, createdat, updatedat, starttime, endtime, eventid, event_id, requestid,
  request_id, traceid, trace_id, pid, processid, process_id, ingestiontime, ingestedat, hash, signature,
  sourceref, source_ref`. Compare on `key.casefold()`.
- `canonicalize_payload(payload: Any) -> str`:
  - Recursively rebuild the structure: drop keys whose casefolded name is in `VOLATILE_KEYS`;
    sort dict keys; **sort lists whose elements are all scalars**; recurse into lists of containers.
  - Serialize `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
- `compute_payload_ssdeep(summary: str, payload: Any) -> str` → returns `ppdeep.hash(canonical_text)`;
  **returns `""`** if empty canonical text or on any failure.
  - Bound inputs using `settings.WEBHOOK_MAX_BODY_SIZE` / `WEBHOOK_MAX_DEPTH`: an oversize/overdeep
    payload → `""`. Catch `(Exception, RecursionError, MemoryError)` → `""`. Never raise.
  - Include **`summary`** in the hashed text. **`title` is EXCLUDED** (decision (A), 2026-10-20; plan
    §3.2) — per-event title tokens (timestamps/IDs) otherwise collapse the score below the threshold.
- **AC-A2:** deterministic; volatile-only differences → identical canonical text; oversize/overdeep/
  exception → `""` without raising; two payloads with *different titles but identical summary+payload*
  now produce the **same** hash.

### A3 — wire both ingest paths
- `ingest/pipeline.py::store_alert`: compute once and add `"payload_ssdeep": <hash>` to `defaults`.
- `alerts/views.py` create path: set `payload_ssdeep=<hash>` on the created `Alert`.
- One shared entry point (the `hashing.py` helper); no `save()` hook (per plan §3.6).
- **AC-A3:** a freshly ingested alert and a freshly API-created alert each have non-empty
  `payload_ssdeep` for a hashable payload.

### A4 — setting
- `amalthea/settings/base.py`:
  `ALERT_FUZZY_SIMILARITY_THRESHOLD = int(os.getenv("AMALTHEA_ALERT_FUZZY_THRESHOLD", "80"))`,
  with a comment (range 0–100; 80 = strict/high-precision default).
- **AC-A4:** present, env-overridable, default 80.

### A5 — backfill command
- `alerts/management/commands/backfill_alert_ssdeep.py`:
  - Flags: `--batch-size` (default 500), `--org <slug_or_id>` (optional), `--dry-run`, `--force`.
  - Default selection: `Alert.objects.filter(payload_ssdeep="")`; with `--force`, recompute all.
  - `.iterator(chunk_size=batch_size)`; compute via the shared helper; `bulk_update` per batch.
  - Report processed/updated/skipped; idempotent (2nd run → 0 updated).
- **AC-A5:** idempotent; `--dry-run` writes nothing; batched via `.iterator()`.

---

## Phase B — fuzzy comparison helper (django-backend)

### B1 — `alerts_fuzzy_similar_to_case`
In `cases/correlation.py` (alongside the existing helpers):
```python
def alerts_fuzzy_similar_to_case(
    case: Case, *, user: User, threshold: int | None = None, limit: int | None = None
) -> tuple[list[SimilarAlert], int]:
```
- `threshold = threshold if threshold is not None else settings.ALERT_FUZZY_SIMILARITY_THRESHOLD`.
- Case signals: hashes of the case's attached alerts (`Alert.objects.filter(case=case)`), non-empty,
  sliced to `MAX_CORRELATION_OBS`.
- Candidates: `Alert.objects.filter(case__isnull=True, payload_ssdeep__gt="")` +
  `org_q("owner_org", user)`, `.select_related("status", "owner_org")`, ordered `-date`,
  sliced to `MAX_FUZZY_COMPARISONS`.
- Score each candidate = **max** `ppdeep.compare(candidate_hash, h)` over the case hashes; keep if
  `score >= threshold`.
- Update `SimilarAlert` to carry `.similarity: int` (0–100) replacing `.shared_count`/`.shared`
  (remove the now-unused exact-overlap fields).
- Rank: `similarity` desc, then `-date`; return `(rows[:limit], total_matching)`.
- Add `MAX_FUZZY_COMPARISONS = 500` module constant.
- **AC-B1:** identical-payload unlinked same-org alert scores ≥ threshold and is returned; disjoint
  excluded; cross-org never returned; attached never returned; bounded; exception-safe.

### B2 — retire the exact-overlap alert path from the card
- In `ui/views.py::_case_detail` similar block, replace the `alerts_similar_to_case(case, ...)` call with
  `alerts_fuzzy_similar_to_case(case, ...)`. Keep tab-gating.
- Delete `alerts_similar_to_case` (or leave a one-line deprecation docstring — decide at
  implementation; the card must not use it). If deleted, remove its dead tests (Phase D).
- **AC-B2:** the card's `similar_alerts`/`similar_alerts_total` come solely from the fuzzy helper;
  `active_tab == "similar"` gating preserved.

---

## Phase C — UI (django-frontend)
- `ui/templates/ui/_tab_similar.html`, "Similar alerts" card:
  - Add a **Similarity** column: `<td>{{ sa.similarity }}%</td>` (reuse `mono dim`).
  - Reword the no-match empty state to "No unlinked alerts are similar enough right now."
    Keep "This case has no alerts to compare against." when `alerts` is empty.
  - **Unchanged:** viewer gating (`{% if can_mutate %}`), hidden `tab=similar`, one-click
    `ui-case-alert-link` form, `similar_alerts_total` truncation line.
- **AC-C1:** rendered card shows `%`; both empty states correct; viewer sees no Link button; hidden
  `tab=similar` intact. No CSS change unless a class is genuinely missing (then `make css`).

---

## Phase D — tests (qa-tester)
`tests/conformance/test_alert_fuzzy_similarity.py`:
1. determinism (same payload → same hash).
2. volatile-key insensitivity (payloads differing only in `date`/`id`/`pid` → identical canonical text
   and equal hashes).
3. threshold boundary (a crafted near-duplicate just above vs below the threshold — assert include/
   exclude).
4. cross-org exclusion **+ falsifiability**.
5. attached-alert exclusion **+ falsifiability** (remove `case__isnull=True` → test fails).
6. threshold gate **+ falsifiability** (remove the `>= threshold` check → a below-threshold alert leaks).
7. volatile-drop **+ falsifiability** (hash the raw blob → the volatile-insensitivity test fails).
8. viewer read-only (no Link button; POST refused).
9. link-flow idempotence (carried over from `test_ui_similar_alerts.py`; adapt/relocate as needed).
10. backfill idempotence (`call_command`; 2nd run → 0 updated).
11. oversize/exception payload → `payload_ssdeep == ""`.
- Update/remove the exact-overlap alert tests in `tests/conformance/test_ui_similar_alerts.py` per B2.
- **AC-D1:** all pass; falsifiability proven by temporary sabotage, then restore (`git diff` clean).

## Phase E — verification + security
- `verifier`: independent AC report → `docs/verification/VERIFY-2026-10-20-alert-fuzzy-similarity.md`.
- `security-auditor`: new dependency supply-chain; canonicalization never persists more than already
  stored; no new tenant/auth holes.

## Phase F — planner
- `COMPLETED.md` entry (incl. the manual dependency-add procedure + backfill runbook), `TODO.md` note.

---

## Ordering & constraints
- **A → B → C → D → E → F.** A and B both touch `cases/correlation.py` → run **sequentially**.
- Do **not** commit or push without explicit user approval. Do not run the full pytest suite; targeted
  modules only (user directive). `make check` is allowed.
- Every new/edit `.md`/`.py` → `ruff format` before `make check`.
- Deviations must be recorded in plan §11.
