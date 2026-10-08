# Verification Report: docs/spec/ — implemented-system specification

- Plan: `docs/planning/PLAN-2026-10-07-docs-spec.md`
- Verified: 2026-10-08 08:21 UTC (re-verification after corrective documentation pass); 2026-10-08 08:40 UTC (final label-canonicalization sweep confirmed)
- Status: **Pass** — AC1/AC2/AC3/AC4/AC5 all **Met**; zero residual findings
- Scope: documentation-only wave; no Python code or migrations changed (git status: docs + `docs/spec/` only)

## Summary

- Total ACs: 5
- Met: 5
- Not Met: 0
- Deviated: 0

Key finding: the corrective pass closed every wave-1 blocker. The pending-run sweep fiction is gone
from `automation.md` (no `recover_stale_runs`, `STALE_RUN_GRACE`, `_recover()`, `soft_time_limit`,
`http.request`/`webhook.post`, `ActionResult`, `-- retry`, phantom evidence files); `query-dsl.md`
no longer names `_MAKES_TOTAL`/`_can_match_field`/`_get_case_via_number`/`_as_query_value`/
`_kind_order`; `deviations.md` was rewritten to the canonical plan-§13 IDs (28 rows, every row with a
code pointer + evidence link); `cases/escalation.py` → `alerts/escalation.py` and the realtime `status-change`
kind are now documented; all broken evidence-file references (`test_settings_prod.py`,
`test_schema.py`, `test_observable_hash.py`, `test_automation_trigger_set.py`,
`test_recover_stale_runs.py`, `docs/automation-actions.md`) were removed or re-pointed at files that exist.

Final label-canonicalization sweep (2026-10-08 08:40 UTC) confirmed **zero residual findings**: the
stale deviation-ID labels that remained after the corrective pass (`automation.md:116`, `api.md:72/105/
120`, `data-model.md:74`, `identity-auth.md:30` — the `P10-b`/`P10-x`/`P10-z`/`P10-F3`/`celery_task_id`-
recovery-gap references) are all resolved to canonical plan-§13 IDs. The only letter-carrying ID remaining
in `docs/spec/` is `P10-w` (a real code-only label, `cases/views.py:88,309`) plus the intentional alias
annotations `P10-2 / y` and `P10-7 / z` inside the register itself (`deviations.md:5`), each of which maps
to a canonical numeric register row. All spec-to-spec cross-references resolve (spot-verified per ID:
`P8-1`, `P10-3/4/6/7/8/w`, `P12-1`, `M3/M6/M9/M12/M14`, `F2/F9`); all register code pointers and all 11
evidence files exist.

---

## Criterion-by-Criterion

### AC1 — README index links; every target exists
Status: **Met**

Evidence (unchanged from wave-1 pass):
- `docs/spec/README.md:11-19` — reading-guide table links all seven files
  (`data-model.md`, `api.md`, `realtime.md`, `automation.md`, `query-dsl.md`, `identity-auth.md`,
  `deviations.md`); all seven exist under `docs/spec/`.
- `docs/spec/README.md:23` — conformance statement names `tests/conformance/`; directory exists
  (37 test modules). Cited test files (`test_t1_surface.py`, `test_authz.py`, `test_realtime.py`,
  `test_thehive_fixtures.py`, `test_ledger_keyset.py`) all exist.
- `docs/spec/README.md:37` — master plan link `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md`;
  §7 exists (`:209`), §13 exists (`:543`).
- `docs/spec/README.md:32,38` — `docs/decisions/ADR-002-thehive-api-compatibility.md` exists; ADR-001 also exists.
- `docs/spec/README.md:39` — evidence-map globs `docs/reviews/REVIEW-2026-10-0{3,4,5}-*.md` (three
  files exist) and `docs/planning/VERIFY-2026-10-07-phase{10,11}.md` (both exist).

No broken link targets in `README.md`.

### AC2 — every quoted identifier exists verbatim in source; zero plan-era names
Status: **Met** (was **Not Met** in wave 1 — corrective pass is effective)

Sampling method: ripgrep over `*.py` in the repo (all apps incl. `tests/`), ≥3 identifiers per file.
100% hit rate across all seven files; zero plan-era identifiers that the code lacks.

`automation.md` — Met (re-verified after rewrite; all previously-phantom symbols gone):
- Trigger set: `TRIGGER_EVENTS = ("observable.created","alert.ingested","case.status_changed",
  "task.completed")` — `automation/dispatcher.py:32-37`; domain events exist in `core/events.py`
  (`ObservableCreated(observable_id, case_id)`, `AlertIngested(alert_id, source_id)`,
  `CaseStatusChanged(case_id)`, `TaskCompleted(task_id, case_id)` — field order matches `core/events.py`).
- Wiring: `registry.observable_saved` / `dispatch_observable_linked` / `alert_saved` / `case_saved` —
  `automation/registry.py:52-105,113`.
- Dispatch details: `_event_name` (`dispatcher.py:47-48`), `_idempotent_subject` (`:63-68`),
  `_subject_id` (`:85-88`); idempotency formula `{event_name}:{playbook}:{digest[:32]}` over
  `sha256(f"{playbook}\x1f{event_name}\x1f{subject}")` — `dispatcher.py:59-60` (matches verbatim).
- Worker: `execute_run` `@shared_task(bind=True, max_retries=3, default_retry_delay=5)` —
  `automation/tasks.py:18`; retry-only-on-`DoesNotExist` (`:35-39`); terminal-state idempotency
  `skipped: "already terminal"` (`:41-42`); `Running`/`started_at = started_at or now()`/`celery_task_id`
  (`:44-47`); `record_result` nested `transaction.atomic` (`:61`); `_interpolation_values` keys
  `run_id/playbook/observable/observable_type/case/case_id` (`:72-88`); `_subject_label` (`:91`);
  `UnsafeURLError` → `"blocked by the SSRF guard"` (`:55`); `ActionError` and other-exception
  `{type}: {msg}` (`:56-59`).
- Executor: `run_http_action`/`run_python_action` dispatch (`executor.py:224-231`),
  `MAX_REDIRECTS = 3` (`:34`), `MAX_OUTPUT_BYTES = 64 * 1024` (`:31`), `DEFAULT_TIMEOUT_SECONDS = 10.0`
  (`:37`), `_ACTIONS`/`register_action` (`:44,55`), `ActionError` for unregistered path (`:218`),
  `ALLOWED_SCHEMES = {"http","https"}` (`:40`), `assert_safe_url` (`:97-109`), scheme re-check on
  every redirect hop (`:164-165`), docstring SSRF example `169.254.169.254` (`executor.py:11-15`),
  truncation marker `[truncated at … bytes]` (`:203-209`), `enrichment_probe` entry point (`:253`).
- Feedback loop: terminal stamp + `cases.ledger.append_timeline_event` with title
  `Automation {status.lower()}: {label}` and metadata `{runId, playbook, triggerEvent, status}` —
  `dispatcher.py:150-175`.
- Evidence files (`automation.md:112-114`): `test_mvp_loop_automation.py`, `test_automation_executor.py`,
  `test_indexes.py` — all exist in `tests/conformance/`.

`query-dsl.md` — Met (re-verified; all previously-phantom helpers gone):
- `QueryEngine`, `_START_STEPS` incl. `getCase` (`query/engine.py:175`); entry points
  `listCase/listAlert/listObservable/listAny/getCase` (`:181-215`).
- `link_case_from_identifier` (`engine.py:582`), miss → `Case.objects.none()` → HTTP 200 bare `[]`
  (`:586`); `_require_anchored` (`:543`); `_LEAF_OPS` (`:173`), `_RANGE_OPS` (`:174`),
  `_has` (`:390-394`), `_leaf_q` + `spec is None → _NO_MATCH` (`:307-313,57`), `_resolve` (`:240`),
  `_allowed` (`:589`), `_SPEC_TABLES` (`:167`), `_materialize` (`:531`), `_sort_rows` +
  `functools.cmp_to_key` (`:447-476`), `_filter_rows` (`:608`), `_build_filter` (`:358`),
  `_coerce` (`:211`), `_require` (`:252`), `_get_case` (`:575`).
- `count` must be the final step — `engine.py:508-509` raises `QueryError` otherwise; `want_total`/
  `self.total` (`:684-703`).
- `_between` `_from` inclusive / `_to` exclusive — `engine.py:28-29,279-283`; `_like` →
  `__icontains` (`:342`).
- View layer: `_render` (`query/views.py:40`), `_bad_request` (`:33`), `X-Total`/`engine.total`
  (`views.py:89-91`).
- Evidence: `test_query_api.py` exists.

`data-model.md` — Met (unchanged 35/35 from wave 1; all names in models/migrations). Wave-1 broken
evidence refs fixed: `test_schema.py`/`test_observable_hash.py` no longer cited; `data-model.md:192`
now cites `test_case_numbering.py`, `test_observable_hashing.py`, `test_mutation_standard.py` — all
exist in `tests/conformance/`.

`api.md` — Met: route table 1:1 vs `urls.py` + verb sets match `@api_view` decorators
(`cases/views.py:100-698`, `alerts/views.py:41-301`, `compat/views.py:47-132`). Wave-1 defect fixed:
`cases/escalation.py` → `alerts/escalation.py::link_case_from_identifier` (`api.md:76,95`; the file
`alerts/escalation.py:157-174` exists).

`realtime.md` — Met (route `realtime/routing.py:6`; close codes 4401/4000 `consumers.py:35,38`;
frames `consumers.py:104,114`; `publish_case_event`/group `publisher.py:9-14`; ledger `events_after`
keyset `Q(date__gte=…) & ~Q(date=…, id__lte=…)` `ledger.py:88-127`). Wave-1 incompleteness fixed:
the event-kind table now lists **both** `status-changed` (API, `cases/views.py`) and `status-change`
(the UI spelling, `ui/views.py:305`), mapped via `_TIMELINE_KIND_MAP` (`cases/views.py:569-577`).

`identity-auth.md` — Met — `ApiKeyAuthentication`/`ScopePermission`/`authenticate_header → "Bearer"`
(`compat/auth.py:47-48`), `AuthenticationFailed("Invalid API key")` (`:38`), AllowAny + csrf_exempt
auth views, `ScopePermission` in `DEFAULT_PERMISSION_CLASSES` (`settings/base.py:118-130`),
`AnonRateThrottle`/`UserRateThrottle` 100/1000 per min (`:127-130`). Wave-1 broken ref fixed:
`test_settings_prod.py` → `test_authz.py` (`identity-auth.md:69`; file exists). `P10-z` reference
(`identity-auth.md:30`) still resolves — the register keeps the alias row `P10-7 / z`.

`deviations.md` — Met: evidence targets now exist (`test_observable_hashing.py`, VERIFY-phase10/11,
REVIEW files, ADR-002 all present).

Plan-era drift samples (required by AC2): **clean** — table is `case_record` (`cases/models.py:122`),
task statuses are `Waiting/InProgress/Completed/Cancel` with no `Todo` (`core/enums.py:63-68`).

### AC3 — deviations register ≥15 rows, covering the listed IDs, each with code pointer + evidence link
Status: **Met** (was **Deviated** in wave 1 — register rewritten to canonical IDs)

- Row count: **28 ≥ 15** ✓ (7 data-model + 17 API/wire + 2 automation + 2 deferred security).
- Coverage vs corrected AC3 text (`PLAN-…docs-spec.md:110-113`):
  - M3/M6/M9/M12/M14 — rows `deviations.md:16-20` ✓
  - P8-1 — row `:28` ✓ (P8-2..P8-7 also present, `:29-33`)
  - P10-2…P10-13: P10-2/y `:34` (the timestamp-split item ✓), P10-3 `:35`, P10-4 `:50`
    (alert-observable no-dispatch — **matches plan §13 P10-4 record**, `PLAN-…mvp.md:664`), P10-5 `:36`,
    P10-6 `:37` (the login-400 item ✓), P10-7/z `:38`, P10-8 `:39`, P10-9 `:40`, P10-12 `:41`,
    P10-13 `:42`, **P10-w `:44`** (the code-only label — now present, code pointer
    `cases/views.py:88,309` ✓)
  - Deferred F2/F9 — rows `:59-60`, header names the carrier **P10-10** (`:55`) ✓
  - A3/A5 clarification — header note `:7-10` ("task IDs, not deviations") applied ✓
- Every row carries a code pointer (file or `file:line`) **and** ≥1 evidence link (test, REVIEW,
  plan, or VERIFY) — spot-verified per row (e.g. M3 `alerts/models.py` + REVIEW-…phase3; M6
  `cases/models.py:122` + REVIEW-round2; P10-3 `alerts/models.py:104-107` + `test_t1_surface.py`;
  P10-7/z `compat/auth.py, settings/base.py` + `test_authz.py`; P10-w `cases/views.py:88,309` +
  REVIEW/plan §13; F2/F9 code pointers in the reasoning column).
- All code pointers resolve to real symbols/files in the current tree (no `automation/tasks.py` sweep
  pointer, no `test_observable_hash.py`).

Notes:
- P8-3 (`excludeFields: []`) is a real plan-§13 deviation (`PLAN…mvp.md:600`) but is not required by
  the corrected AC3 letter ("covering … P8-1, P10-2…P10-13 … F2/F9"); omitted from the register —
  observation, not a failure.
- P10-11 (perf Wave D, "Met" per plan §13 `:671`) has no register row — consistent with the register's
  charter (records *departures*; a Met perf record with a borderline note is not a departure).
- Rows with no plan-§13 ID (`uniq_obs_dtype_hash` globality, `Case.number` allocation, task PATCH
  204) are labelled `—` / `— (no number)` and are correctly cross-linked.

### AC4 — named surfaces match the source (sample-verified)
Status: **Met**

- `data-model.md` constraint/index names — Met: all 35 sampled names match models/migrations verbatim
  (see AC2 list); `ar_pending_idx` partial index (`automation/models.py:80-84`) matches the
  planner-level assertion in `test_indexes.py:395-465`.
- `api.md` route table — Met: every row of both tables maps 1:1 onto registered `urlpatterns`
  (`ingest/urls.py`, `alerts/urls.py`, `cases/urls.py:10-55`, `query/urls.py`, `compat/urls.py`,
  `core/urls.py`); `cases/escalation.py` → `alerts/escalation.py` fixed.
- `realtime.md` WS protocol — Met: route/codes/frames/relay/keyset verified (see AC2); `status-change`
  now present in the kind table.
- `automation.md` trigger set — Met: `TRIGGER_EVENTS` matches `automation/dispatcher.py:32-37`
  (4 events); `task.completed` frozen state documented as P12-1.
- `query-dsl.md` operator set — Met: `_LEAF_OPS` byte-identical (`query/engine.py:173`), `_RANGE_OPS`
  and `_and`/`_or`/`_not` combinators match (`:174,366-388`).
- `deviations.md` ID set — Met: now matches plan §13 canonical IDs (see AC3).

### AC5 — TODO §4.4, COMPLETED.md, master plan §13 Phase 12
Status: **Met** (unchanged)

- TODO §4.4 closed with the file list: `TODO.md:298-304` (`[x] 4.4 — docs/spec/ populated …`).
- COMPLETED.md entry: `COMPLETED.md:467` — "2026-10-07 — `docs/spec/`: implemented-system
  specification (TODO §4.4)"; items include eight files, drift policy, register claim.
- Master plan §13 Phase 12 record: `PLAN-2026-10-03-thehive-compatible-mvp.md:719-735` — P12-1
  (`task.completed` registered-but-unemitted) with cause, decision, and register cross-reference.
- README.md Documentation section links `docs/spec/`.

---

## Deviations

**None.** All wave-1 spec-vs-code deviations are closed, and the post-correction residual
docs-internal label drift found at the last verification pass is now **resolved** by the final
label-canonicalization sweep. Verified in this run:

- `automation.md:113-116` — evidence footer now states only `P12-1` (task.completed registered-but-
  unemitted) and `P10-4` (alert-observable links do not dispatch); the stale "`celery_task_id` recovery
  gap" pairing and the dead `P10-b` citation are gone.
- `api.md:72` — `P10-b` → `P10-4` (matches register row `deviations.md:50`).
- `api.md:105` — `P10-x` → "no plan number — see `deviations.md`" (matches the `— (no number)` row,
  `deviations.md:43`).
- `api.md:121` — `A5/P10-F3-deferred` → "global mutation deferred — see F2 in `deviations.md`"
  (matches the F2 row, `deviations.md:59`).
- `data-model.md:74` — `P10-b` → `P10-4`.
- `identity-auth.md:30` — `P10-z` → `P10-7` (matches the `P10-7 / z` row, `deviations.md:38`).
- Zero stale letter-suffix IDs remain anywhere in `docs/spec/` (`rg "P10-[a-wy-z]" ` matches only
  `P10-w` at `deviations.md:5,44` and `api.md:108`; the `P10-2 / y` and `P10-7 / z` strings are
  intentional alias annotations inside the register header only).
- Every spec-to-spec cross-reference resolves: all 15 cited IDs (`P8-1`, `P10-3/4/6/7/8/w`,
  `P12-1`, `M3/M6/M9/M12/M14`, `F2/F9`) map to a register row; all register code pointers and all 11
  evidence files exist (spot-verified; `settings/base.py` = `amalthea/settings/base.py`, present).

## Blockers

None. Wave-1 blockers (AC2 phantom identifiers, AC3 label coverage/link consistency, broken
`cases/escalation.py` and evidence-file citations) and the post-correction residual label drift are
all resolved.

## Recommendations (non-binding)

1. Optional: add a `P8-3` row (`excludeFields: []` nuance, plan §13 `:600`) to the register for
   completeness — not required by AC3's letter.
2. Optional: add `P10-11` (perf, "Met") as a non-deviation note if the register is ever used as a
   plan-§13 *index* rather than a departures log.
3. Recommend re-running AC2's rg sweep once the wave is committed (currently untracked), to hold the
   identifier-verbatim guarantee on the committed tree.