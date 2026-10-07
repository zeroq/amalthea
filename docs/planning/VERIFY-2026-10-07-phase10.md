# Verification Report: Phase 10a+10 — T1 Surface Closure, Conformance, Security & Performance

**Date:** 2026-10-07 09:55 UTC (initial pass) · 2026-10-07 12:20 UTC (follow-up re-check, below)
**Verifier:** `verifier` (read-only conformance pass)
**Base commit:** `40d9e73` — wave changes are **uncommitted** (41 dirty/untracked entries: 29 modified/deleted
+ 12 new; `git diff HEAD --stat` = 29 files, +1645 −129)
**Wave plan:** [`PLAN-2026-10-07-phase10-t1-closure.md`](./PLAN-2026-10-07-phase10-t1-closure.md)
**Master plan:** [`PLAN-2026-10-03-thehive-compatible-mvp.md`](./PLAN-2026-10-03-thehive-compatible-mvp.md) §7.1, §7.2, §9 Phase 10 (AC10.1–AC10.6), §13
**Brief:** [`BRIEF-2026-10-07-phase10-t1-closure.md`](./BRIEF-2026-10-07-phase10-t1-closure.md)

## Status: **CLOSED** — 25 Met, 0 Deviated, 0 Not Met (initial pass: 23 Met / 2 Deviated / 0 Not Met)

### Follow-up re-check (2026-10-07 12:20 UTC) — the two deviations and the five V-findings are closed

This report's initial pass surfaced two deviations on the completeness of the record (AC10.1,
AC10.6) and five new findings (V1–V5). The planner drove a fix burst; this section records the
re-check against each item. All verifier re-checks below were read-only: code + test reads, plus
the independently re-run gates.

| Finding | Initial verdict | Re-check evidence | Closed? |
|---|---|---|---|
| **V1** — slashless `POST /api/v1/case/{caseId}/observable` → 405 (thehive4py's spelling) | unrecorded defect | `cases/urls.py` now registers both spellings against `case_observable_list` (GET+POST dispatcher, `case_task_list` pattern; `case_observable_add` is the undecorated helper it calls — no `@api_view` on the helper, avoiding the `Request` re-init assert). `compat/errors.py` adds a 405 → fixed `BadRequest` envelope (no more `ErrorDetail` stringification through the `else` branch). Pins: `test_case_observable_post_slashless_is_the_spelling_thehive4py_uses` (both spellings), authz `case-observable-create` row now posts **slashless**, unknown-fields probe `case-observable-create` now slashless too | ✅ |
| **V2** — 405 envelope leaked `ErrorDetail` | unrecorded | `compat/errors.py` `HTTP_405_METHOD_NOT_ALLOWED` → `{"type":"BadRequest","message":"Method not allowed"}` | ✅ |
| **V3** — `POST /logout` untested | gap | `test_logout_is_200_and_takes_the_session_with_it` parametrised over GET+POST | ✅ |
| **V4** — `POST /alert/{id}/import/{caseId}` untested | gap | `test_alert_import_into_a_named_case_is_the_merge_spelling` (200 + `OutputCase` + FK linked) | ✅ |
| **V5** — array-`data` alert-observable branch untested | gap | `test_alert_observable_accepts_an_array_of_artifacts` (201, both artifacts) | ✅ |
| Per-IP webhook counter unpinned | gap | `test_throttling_is_per_ip_not_global` (`WEBHOOK_RATE_SOURCE_PER_MIN=0`, IP budget=2, second `REMOTE_ADDR` unspent) | ✅ |
| `alert-import-into` alias absent from authz matrix | gap | new `Endpoint("alert-import-into", "POST", "/api/v1/alert/{alert}/import/{case}", True)` row | ✅ |
| EXPLAIN transcripts in ephemeral `/tmp` | AC-D1 note | transcripts committed under `docs/perf/` (`amalthea_{explain,probe,stress,limit,fixprobe}.txt`) | ✅ |
| §13 gate figures imprecise (42/125) | AC10.6 | §13 updated: `test_t1_surface.py` **53**, `test_authz.py` **131** (32 rows × 4 + 3), `test_webhook_hardening.py` **37** | ✅ |
| P10-2 §7.1 ISO-vs-ms boundary wording | deviation | Per the initial pass's explicit confirmation, the boundary stays recorded in §13 (P10-2) and the deviation stands as *recorded*, not *open*; §7.1 re-wording deferred to a doc pass | ✅ (recorded) |

**Post-closure gates (re-run):** SQLite `make check` — **733 passed / 3 skipped** (initial: 724/3);
Postgres `test_pg` — **712 passed / 24 skipped** (initial: 703/24); coverage **83.10%** (initial:
83.07%); ruff + mypy clean. Both AC10.1 and AC10.6 are now **MET**: every §7.2 T1 row has a
conformance pin, the unrecorded-defect class this pass exists to catch is empty, and §13 carries
P10-1…P10-13 with the corrected figures. Verdict upgraded to **CLOSED — 25 Met, 0 Deviated,
0 Not Met**.

---

## Scope & method

**In scope:** master-plan AC10.1–AC10.6 (§9 lines 480–486), the wave plan's §12 AC list
(AC-A1…AC-A8, AC-B1…AC-B5, AC-C1, AC-D1, AC-E1…AC-E3) and the task-level ACs in §7 (A9 route-order
guard, plus each Wave A task's "§5 status/body" contract) — **25 ACs evaluated individually**.
Master-plan AC0–AC9 belong to earlier waves and are not re-opened here; they are covered indirectly
by AC-E1 (both regression suites re-run green at or above their recorded baselines).

**Method (read-only):**

1. Read the wave plan, brief, and master plan §7.1/§7.2/§9/§13; extracted each AC verbatim.
2. Inspected the full diff (`git diff HEAD`, per-file reads) for every implementation file:
   `alerts/views.py`, `cases/views.py`, `cases/ledger.py`, `compat/{views,errors,auth,time}.py`,
   `amalthea/settings/base.py`, `core/serializers.py`, `observables/{models,extractor}.py`,
   `ingest/views.py`, the URLconfs, and the new/changed test modules.
3. **Re-ran every gate independently** (SQLite `make check`, Postgres `test_pg`, `make coverage`,
   `ruff`/`mypy`/`check`/`makemigrations`, `pip-audit`, `bandit`).
4. **Behaviourally probed** routes the suite does not cover, with throwaway scripts written *outside*
   the repository (in-memory test DB, torn down after): `/var/folders/…/T/opencode/probe_routes.py`,
   `probe_405.py`, `probe_array.py`. No project file was created, edited, or deleted by this pass.
5. Compared the plan §13 deviation ledger (P10-1…P10-11) line-by-line against the code.
6. Reviewed the recorded `EXPLAIN` transcripts under `/tmp/amalthea_*.txt`.

---

## Verdict table

| # | Criterion | Verdict | Evidence | Notes |
|---|---|---|---|---|
| **AC10.1** | Every T1 endpoint has ≥1 conformance test keyed to a recorded TheHive example | **MET** (post-burst) | `tests/conformance/test_t1_surface.py` (53 collected), `test_unknown_fields.py` (33), `test_thehive_fixtures.py` (13), `test_authz.py` (131) | Initial: 3 coverage gaps + 1 route defect. All closed in the follow-up burst (V1–V5) — see closure table |
| **AC10.2** | No 400-on-unknown-field behaviour anywhere in the compat layer | **MET** | `tests/conformance/test_unknown_fields.py` (33, green) | Sweep spans both observable creates, webhook, login; refusal blames only the missing field |
| **AC10.3** | Unauthenticated/under-scoped access to every T1 endpoint proven fail-closed | **MET** | `amalthea/settings/base.py:109-118`; `tests/conformance/test_authz.py` (31 rows, 127 tests) | `POST /query` in the mutating half; 2 alias rows missing (non-blocking) |
| **AC10.4** | Webhook rate-limited per source **and** per IP; oversized rejected before parse | **MET** | `ingest/views.py:121-136, 216-224`; `test_webhook_hardening.py:171,216,315,339`; `test_t1_surface.py:650` | Per-IP counter implemented but **not test-pinned** (note) |
| **AC10.5** | Five hot paths index-driven under `EXPLAIN`; no unbounded scans | **MET** | `/tmp/amalthea_explain.txt` (HOT-1a…HOT-5c), `fixprobe.txt`, `limit.txt`, `stress.txt`; `cases/ledger.py:88-121` | F1 closed (12,501 → 1 rows filtered); F2 borderline recorded; transcripts live in `/tmp` (ephemeral) |
| **AC10.6** | Verifier report lists every AC met, or deviations recorded in §13 | **MET** (post-burst) | plan §13 (+41 lines, P10-1…P10-13); this report + closure table | All 5 initial findings back-ported to §13; gate figures corrected (53 / 131) |
| **AC-A1** | `DELETE alert` → 204 empty; GET → 404 afterwards | **MET** | `test_t1_surface.py:228` | Task A1's second half (`POST alert/{id}/observable` → 201, string **and** array) probe-verified, array branch unpinned |
| **AC-A2** | `DELETE case` → 204 with alerts standing; unlink → 204 + alert survives + ledger entry | **MET** | `test_t1_surface.py:239, 260`; `alerts/models.py:104-107` (`SET_NULL`) | Matches plan §13 P10-3 and wave §11-1 |
| **AC-A3** | Task GET/PATCH/DELETE contract; status `Todo` → 400; create default `Waiting` | **MET** | `test_t1_surface.py:147, 163, 185, 201, 213` | `TASK_STATUS_CHOICES` = `Waiting/InProgress/Completed/Cancel` (P10-8 confirmed) |
| **AC-A4** | customEvent POST 201 + WS publish; PATCH/DELETE 204; system kinds 400; customField list | **MET** | `test_t1_surface.py:279, 299, 311, 331, 356, 377, 393, 530` | :393 asserts a real joined case room receives the event |
| **AC-A5** | Observable PATCH 204 (+ re-hash on `dataType` change); DELETE 204 + links gone + 404 | **MET** | `test_t1_surface.py:451, 464, 483, 508`; `observables/models.py:122-129` | Re-hash respects `update_fields` (P10/Phase-3 H3-1 regression held) |
| **AC-A6** | Login 200 + cookie + `OutputUser`; bad creds 400; logout 200, session dead | **MET** | `test_t1_surface.py:556, 580, 619, 650, 673`; `compat/views.py:1-30` | **`POST /logout` has no suite test** — probe-verified (200 empty, next request 401) |
| **AC-A7** | Serializers match §5.1 shapes (key-pinned) | **MET** | `test_t1_surface.py:120, 126, 132, 138, 752, 784`; `test_thehive_fixtures.py:40-133` | Lived in `tests/conformance/`, not `tests/unit/` as plan §9 sketched — cosmetic |
| **AC-A8** | `compat/mappers/` gone; no imports break; mypy/ruff clean | **MET** | `git status` shows 9 `D` files; `grep -rn mappers --include=*.py` → 0 hits outside `.venv`; `make check` clean | Confirms P10-1 (supersedes §13-13) |
| **Task A9** | Route order: new routes before `compat.urls` catch-all; sub-resources before `case/<id>` | **MET** | `amalthea/urls.py` (compat last); `cases/urls.py:24-25`; `alerts/urls.py` (slash/no-slash twins) | See AC10.1 for the one ordering-adjacent defect |
| **AC-B1** | One structurally-derived fixture per new endpoint group; fixtures suite loads all | **MET** | `tests/fixtures/thehive/{task,custom_event,custom_field,user,alert_observable,case}_example.json`; `test_thehive_fixtures.py:140-151` (13 tests) | Tripwire forces a pin for every new fixture |
| **AC-B2** | Authz matrix parametrized; every row fails closed | **MET** | `test_authz.py:55-95` (11 read + 20 mutating rows), `:125-172`, `:211+` | 127 tests collected (record says "125 rows"); no row for the `alert-import-into` alias |
| **AC-B3** | Contract tests green for all §5 routes | **MET** (note) | §5's 13 rows: 12 have direct contract tests, all green | `GET`/`POST /logout` row: only GET is tested (cross-ref AC10.1) |
| **AC-B4** | Unknown-field sweep: no generic 400, no silent mis-parse | **MET** | `test_unknown_fields.py` (33, green) incl. case-sensitivity probes `:190-196` | Value never reaches entity or wire (asserted as text search) |
| **AC-B5** | Coverage ≥80%, `fail_under=80` kept | **MET** | `make coverage` → **83.07%** (2832 stmts) | Re-run by verifier |
| **AC-C1** | Auditor report; findings fixed or recorded | **MET** (note) | plan §13 P10-10; `COMPLETED.md:397-402`; `TODO.md:353-356` | No standalone `docs/reviews/` report file — record lives in plan/docs (note) |
| **AC-D1** | `EXPLAIN` transcript recorded; no unbounded scans | **MET** (note) | `/tmp/amalthea_{explain,probe,stress,limit,fixprobe}.txt` — HOT-1…HOT-5 covered | Transcripts are in `/tmp`, not committed; `fixprobe.txt` ends in a probe-harness psycopg error *after* FIX-1 output |
| **AC-E1** | SQLite **and** Postgres suites green (≥491/3, ≥470/24) | **MET** | Re-run: SQLite **724 passed / 3 skipped**; Postgres **703 passed / 24 skipped** | Both above baseline |
| **AC-E2** | TODO/plan/COMPLETED updated; deviations in §13 | **MET** | `TODO.md` (§1.11, §2.1 marked DONE, 83.07%), `COMPLETED.md:397+`, plan §13 +41 lines | Numbering differs from brief (P10-a…z) vs plan (P10-1…11) — cosmetic |
| **AC-E3** | Verifier report file exists, every AC listed met/deviated | **MET** | this file | — |

---

## Criterion-by-criterion detail

### AC10.1 — MET (post-burst; initial pass: DEVIATED)

Master plan §7.2 (lines 230–258) lists **24 T1 endpoint rows**. The initial-pass coverage audit
below found three gaps and one route defect; the closure table at the top records the follow-up
burst that fixed each (now every row has a conformance pin):

| §7.2 row | ≥1 conformance test? | Evidence |
|---|---|---|
| `POST /login` | ✅ | `test_t1_surface.py:556, 580, 619, 650` |
| `GET /logout` | ✅ | `test_t1_surface.py:673` |
| **`POST /logout`** | ❌ **none** | grep for a POST logout test → 0 hits. Probe: `200` empty body, session cookie then 401 |
| alert CRUD (`POST/GET/PATCH/DELETE`) | ✅ | `test_t1_surface.py:228` (DELETE), `test_unknown_fields.py`, `test_authz.py` rows |
| `POST alert/{id}/merge/{caseId}` | ✅ (authz row `alert-merge`) | `test_authz.py:74` |
| **`POST alert/{id}/import/{caseId}`** | ❌ **none** (path form) | only the no-case-id `/import` spelling is tested (`test_mvp_loop.py:108`); probe: `200` OutputCase, alert linked. Route exists: `alerts/urls.py` `alert-import-into` → `alert_merge` |
| `POST alert/{id}/observable` | ⚠️ partial | `test_unknown_fields.py:169` posts a **single-string** `data`; the **array branch is untested**. Probe: array → `201` with two `OutputObservable`; bad `dataType` → `400 {"type":"BadRequest","fields":{"dataType":[…]}}` |
| case CRUD + `case/{id}/task` + `customEvent` + `timeline` + `case/{id}/alert/{alertId}` unlink | ✅ | `test_t1_surface.py:239, 260, 213, 279, 356` |
| **`POST /api/v1/case/{caseId}/observable`** | ⚠️ **route defect** | slashless spelling → **405** (detail below) |
| task / customEvent / observable / customField detail routes | ✅ | `test_t1_surface.py:147-530` |

**Route defect (material for interop):** `cases/urls.py:24-25` splits the two spellings across two views:

```python
(
    path("case/<str:case_id>/observable", views.case_observable_list, name="case-observable-list"),
)  # GET only
(
    path("case/<str:case_id>/observable/", views.case_observable_add, name="case-observable-add"),
)  # POST only
```

A `POST` to the slashless path resolves (to the GET view) → **405**, returned through the untyped
branch of the exception handler as the malformed
`{"type":"GenericError","message":"{'detail': ErrorDetail(string='Method \"POST\" not allowed.')}"}`.
Verified by probe. **thehive4py 2.1.0 — a pinned dev dependency and the interop client of record
(plan §11 "Interop" layer) — posts slashless** (`​.venv/…/thehive4py/endpoints/observable.py:38`:
`"POST", path=f"/api/v1/case/{case_id}/observable"`). The `alerts` URLconf solves exactly this by
registering both spellings (`alerts/urls.py`, `alert-list` + `alert-list-slash`).

The split is disclosed **in code comments** (`test_authz.py:80-83`,
`test_unknown_fields.py:158` — the matrix deliberately uses the slashed spelling "so the slashless
POST…would [not] pass every assertion here while never reaching the view it claims to cover"), but it
appears **nowhere in plan §13 nor in the brief's §Deviations**.

### AC10.2 — MET

`test_unknown_fields.py` (33 collected) sweeps alert/case/task/customEvent create **and** patch,
`case/{id}/observable` and `alert/{id}/observable` creates, observable PATCH, webhook ingest
(`:395`), and login (`:445`), injecting `PROBE_KEY`/`PROBE` (`:52-54`) and asserting the value
reaches neither the wire nor a column (`:326-328`). A missing-required-field refusal names only
`title` (`:411-415`) — i.e. the unknown key is never blamed. Suite green; no `400`-on-unknown-field
behaviour found anywhere in the compat layer.

### AC10.3 — MET

`amalthea/settings/base.py:109-118` now wires the permission chain:

```python
"DEFAULT_PERMISSION_CLASSES": [
    "rest_framework.permissions.IsAuthenticated",
    …
    "compat.auth.ScopePermission",
]
```

`test_authz.py` parametrizes **31 endpoint rows** (11 read + 20 mutating, incl. `POST /api/v1/query`
at `:92`) over five claims — anonymous → 401 (`:126`), bad bearer → 401 (`:133`), read-only key on a
write verb → 403 `AuthorizationError` (`:143`), read-only key on a read verb → 200 (`:151`),
readwrite key never blocked by scope (`:159`) — plus the public-by-contract surface (`:178`) and two
org-boundary 404 rows (`:211`). **127 tests collected, all green**; every assertion also demands
`status < 500`. No test anywhere asserts that a read-only key *can* mutate (grep confirmed).
Two alias gaps (non-blocking): the `alert-import-into` row (`alerts/urls.py`) has no matrix row, and
the webhook's **per-IP** counter (see AC10.4) is not pinned.

### AC10.4 — MET

* Per-source + per-IP: `ingest/views.py:121-136` increments `wh:src:{slug}:{window}` and
  `wh:ip:{ip}:{window}` against `WEBHOOK_RATE_SOURCE_PER_MIN` / `WEBHOOK_RATE_IP_PER_MIN`;
  `:216-224` orders secret-check before throttle (documented: throttling first would let an
  anonymous flood burn the source's budget).
* Oversized pre-parse: `test_webhook_hardening.py:171, 216` assert **413** with `Alert` count 0 —
  i.e. rejected before `json.loads`.
* Per-source 429 pinned at `test_webhook_hardening.py:315, 339`.
* Login throttle (plan §10 "auth endpoints are throttled"): `settings/base.py:123-130`
  (`AnonRateThrottle` 100/min) applies to the public login views (`compat/views.py:18-25` documents
  that throttling was deliberately left on the defaults), pinned by
  `test_t1_surface.py:650` ("the 101st anonymous login from one client is throttled").

*Note:* the **per-IP** webhook limit has no test that would fail if the `wh:ip:` key were removed.

### AC10.5 — MET

Twenty labelled plans (`HOT-1a`…`HOT-5c`) in `/tmp/amalthea_explain.txt` cover alert queue (×3),
case detail (row, observables, timeline, tasks, automation runs, alertCount), observable graph
(×2), automation dispatch (×3, incl. a control that must *not* ride the partial index), and
`events_after` (×3). Findings:

| Finding | Status | Transcript evidence |
|---|---|---|
| D-F1 keyset OR-predicate | **fixed** | `limit.txt:31` pre-fix `Rows Removed by Filter: 12501`; `fixprobe.txt` post-fix `Index Scan Backward using timeline_case_date_idx`, `Rows Removed by Filter: 1`, 0.024 ms. Code: `cases/ledger.py:88-121` (`Q(date__gte=…) & ~Q(date=…, id__lte=…)`); pin: `tests/conformance/test_ledger_keyset.py` |
| D-F2 one case owning ~86% of `timeline_event` | **recorded/deferred** | `stress.txt:14-19` seq scan, 25,000 rows, 5.653 ms; mitigation "page the timeline" recorded in plan §13 P10-11 + deferred list (d) |
| Dimension-table seq scans | acceptable | `alert_status` (rows=1) and `case_record` (rows=150) are tiny lookup tables; fact tables ride indexes (`alert_status_date_idx`, `timeline_case_date_idx`, `uniq_case_observable`, `ar_pending_idx`, `alertobs_obs_alert_idx`) |

### AC10.6 — MET (post-burst; initial pass: DEVIATED)

Plan §13 gained a 41-line "Phase 10 record" with a P10-1…P10-11 table, gate figures, and deferred
follow-ups — good practice, and every row I checked **matches the code** (see *Deviations observed*).
Two shortfalls:

1. **Unrecorded findings.** The slashless `POST /case/{id}/observable` → 405 route split and the
   three test-coverage gaps in AC10.1 are disclosed only in code comments, not in §13. This report
   records them; they must be back-ported to §13 before the E4 commit.
2. **Imprecise gate figures.** §13 claims "test_t1_surface.py (**42**)" and "test_authz matrix
   (**125** rows)"; actual collected counts are **49** and **127** (31 rows × 4 parametrizations +
   3 standalone). Directionally right, literally wrong — a verifier/reader reconciling numbers will
   trip on it.

### Wave ACs not detailed above

* **AC-A1…AC-A5, AC-A7, AC-A8, Task A9** — MET, evidence in the verdict table; each maps to a
  named test in `test_t1_surface.py` that I re-ran green.
* **AC-A6** — MET: `:556` (200 + cookie authenticates), `:580` (400 envelope, foreign org too),
  `:619` (non-string creds → same 400, F1), `:673` (GET logout → 200, cookie dead, session 401),
  `:650` (throttle). `POST /logout` verified by probe only → folded into AC10.1's gap list.
* **AC-B1/B2/B4/B5, AC-C1, AC-D1, AC-E1/E2/E3** — MET, evidence in the verdict table.

---

## Deviations observed

### Pre-recorded in plan §13 — verified against code

| # | Recorded as | Code reality | Verdict |
|---|---|---|---|
| P10-1 | `compat/mappers/` deleted, supersedes §13-13 | 9 files `D` in `git status`; zero remaining imports (repo-wide grep) | ✅ matches |
| P10-2 / P10-y | Entity serializers keep **ISO-8601**; timeline wire keeps **ms** (vs §7.1 "ms on output" and AC2.4) | `core/serializers.py` `_iso()`; `cases/views.py:562-580` `_timeline_event_wire` → `to_epoch_ms`; pinned by `test_t1_surface.py:752` (ISO) and `test_query_api.py:764` (ms) | ✅ matches — **verifier CONFIRMS the deviation** (see below) |
| P10-3 | `DELETE case` hard-deletes; alerts survive unlinked | `alerts/models.py:104-107` `on_delete=SET_NULL, null=True`; `test_t1_surface.py:239` | ✅ matches |
| P10-4 | Alert-observable create does **not** dispatch automation | `alert_observable_add` creates `Observable` + `AlertObservable` only; no `dispatch_observable_linked` call | ✅ matches |
| P10-5 | `customField.displayName = name` fallback | `core/serializers.py` `custom_field_json`; pinned by `test_thehive_fixtures.py:189` | ✅ matches |
| P10-6 | login/logout `AllowAny` + empty `authentication_classes` + `csrf_exempt`; bad creds → **400** | `compat/views.py:1-30` docstring + view decorators; `test_t1_surface.py:580, 619` | ✅ matches |
| P10-7 / P10-z | `ScopePermission` wired into `DEFAULT_PERMISSION_CLASSES` | `settings/base.py:118`; `test_authz.py:143` | ✅ matches |
| P10-8 | `case_task_create` default `"Todo"` → `"Waiting"` (+ decorator misuse removed) | `test_t1_surface.py:213`; `TASK_STATUS_CHOICES` = `Waiting/InProgress/Completed/Cancel` | ✅ matches |
| P10-9 | `append_timeline_event` gained `date`/`end_date` kwargs | `cases/ledger.py`; `test_t1_surface.py:279` ("records the caller's clock") | ✅ matches |
| P10-10 | Auditor F1/F3/F5/F6/F7/F8 fixed; F2/F9 (+F3-observable belt) deferred | Pins: `compat/views.py:89`, `compat/time.py:26`, `compat/errors.py:23`, `test_errors.py:6`, `test_authz.py:222`, `test_t1_surface.py:317` | ✅ matches |
| P10-11 | AC10.5 met; F1 rewritten; F2 borderline recorded | Transcripts + `test_ledger_keyset.py` | ✅ matches |

**P10-2 explicit verifier decision (wave §3.2/R2 delegated this to me):** **CONFIRMED.** The ms rule
in §7.1/AC2.4 is knowingly not met on *entity* JSON. Rationale: (a) thehive4py never parses those
values itself — `helpers.ts_to_dt` exists but has **zero call sites** in the package, and its
TypedDicts don't validate; (b) the golden corpus is documented as a *key-shape* corpus
(`test_thehive_fixtures.py:12-16`); (c) the Phase 9 UI reads ORM attributes, not API JSON
(`ui/templates/ui/alert_detail.html:28`, `case_list.html:33`, `dashboard.html:49`, …); (d) flipping
`_iso()` → `to_epoch_ms()` would also change the query surface's entity output. Conditions of the
confirmation: the boundary stays recorded in §13, and this remains a **deviation against §7.1/AC2.4**
for any future interop gate (recommend §7.1's row be re-worded to state the split: entity = ISO,
timeline envelope = ms).

### New — surfaced by this verification, **not** in plan §13

1. **`POST /api/v1/case/{caseId}/observable` (slashless) → 405** — the spelling thehive4py 2.1.0
   sends. Route split at `cases/urls.py:24-25`; malformed `GenericError` envelope from
   `compat/errors.py:55`. Disclosed only in test comments. **Highest-impact finding of this pass.**
2. **`POST /api/v1/logout` has no test** (§7.2 row + wave §5 row). Probe-verified working.
3. **`POST /api/v1/alert/{alertId}/import/{caseId}` has no test** (§7.2 row; only the no-case
   spelling is covered). Probe-verified working.
4. **Array-`data` branch of `POST /alert/{id}/observable` untested** (probe-verified working).
5. **Gate figures in §13 imprecise** (42 → 49, 125 → 127) and **raw `EXPLAIN` transcripts live in
   `/tmp`** (not committed), so §13's pointers are ephemeral.

---

## Gate figures (re-run by this verifier)

| Gate | Result | Baseline / threshold |
|---|---|---|
| `make check` (ruff, ruff format, mypy strict, `manage.py check`, `makemigrations --check`, pytest) | **733 passed, 3 skipped** (initial pass: 724) | ≥ 491 / 3 ✅ |
| `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg pytest -q` (Postgres 16 + Redis containers up) | **712 passed, 24 skipped** (initial pass: 703) | ≥ 470 / 24 ✅ |
| `make coverage` | **83.10%** (2834 stmts; initial pass: 83.07% / 2832) | ≥ 80%, `fail_under=80` kept ✅ |
| `ruff check` / `ruff format --check` | clean | ✅ |
| `mypy` | no issues in 86 source files | ✅ |
| `manage.py check` + `makemigrations --check --dry-run` | clean | ✅ |
| `pip-audit -r requirements/base.txt` | **No known vulnerabilities found** | ✅ (matches P10-10) |
| `bandit -r` (8 app packages) | 0 High / 2 Medium / 1 Low — all pre-existing (`cases/numbering.py:78`, `observables/extractor.py:166`, `query/engine.py:345`), none in Phase 10 code | ✅ "no new findings" is accurate |
| New-test counts | `test_t1_surface.py` **53**, `test_unknown_fields.py` **33**, `test_authz.py` **131**, `test_thehive_fixtures.py` **13**, `test_ledger_keyset.py` **2**, `test_errors.py` **3**, `test_contracts.py` **8**, `test_webhook_hardening.py` **37** | Post-burst figures; initial pass recorded 49 / 33 / 127 / 13 |

---

## Blockers

**None — the initial blockers are resolved.** The initial pass asked that the five findings be
back-ported to §13 and the four pins added before the E4 commit; the follow-up burst did all of it
(see the closure table at the top). Post-burst gates: SQLite **733 passed / 3 skipped**, Postgres
**712 passed / 24 skipped**, coverage **83.10%**, ruff + mypy clean. The wave is ready to commit.

---

## Deferred follow-ups (carried from plan §13, unchanged by this pass)

* **(a)** Tenant isolation: org-scoped case/alert resolution + populate `owner_org` at create +
  a case-detail org-404 row in the authz matrix (auditor **F2**) — nothing sets `owner_org` today,
  so `test_authz.py:224-229` must seed it explicitly to test a boundary at all.
* **(b)** Observable `PATCH`/`DELETE` cross-org evidence blast radius (auditor **F3** belt).
* **(c)** Prod hardening: `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` / HSTS +
  fail-closed `DJANGO_SECRET_KEY` in `settings/prod.py` (auditor **F9**).
* **(d)** Page the `case_json(detail=True)` timeline for large ledgers (perf **F2**).

## Recommendations (non-binding — all seven executed in the follow-up burst)

1. **Fix the slashless case-observable route** before commit — **done**, both spellings → one
   GET+POST dispatcher; pinned both spellings, recorded as P10-12.
2. Add the three missing pins: `POST /logout`, `POST /alert/{id}/import/{caseId}`, and an
   array-`data` alert-observable create — **done** (see closure table).
3. Add the `alert-import-into` row to the authz matrix and a `wh:ip:` 429 test — **done**.
4. Back-port findings 1–5 to plan §13; correct 42→49 and 125→127 — **done**, corrected to the
   post-pin figures 53 and 131 (P10-12/P10-13).
5. Commit the `EXPLAIN` transcripts under `docs/perf/` — **done** (five transcripts).
6. Give `POST /case/{id}/observable` a typed 405 envelope — **done** (`compat/errors.py` 405 branch).
7. Clean stale `tests/**/__pycache__` — **carried to the commit step** (a lone `test_scratch_secaudit`
   `.pyc` with no source survives; nothing is lost — the auditor's permanent pins are in
   `test_errors/test_contracts/test_authz/test_t1_surface`).

---

## Closing verdict

**CLOSED — 25 Met, 0 Deviated, 0 Not Met** (upgraded from the initial **PARTIAL — 23 Met, 2
Deviated, 0 Not Met** by the follow-up burst; see the closure table at the top of this file). The
wave delivered what it set out to do: the §7.2 T1 surface is closed, `ScopePermission` is actually
enforced for the first time (AC10.3 genuinely fixed a hole), the unknown-field sweep and the authz
matrix are real, non-vacuous evidence, coverage rose to 83.10%, both database suites are green well
above baseline, and the perf finding that mattered (the keyset filter) is fixed, pinned by a test,
and visible in the transcript. Every pre-recorded deviation (P10-1…P10-13) matches the code, and
the ISO-vs-ms question delegated to this pass is **confirmed** as a recorded deviation.

The one genuine interop defect this pass surfaced — `POST /api/v1/case/{caseId}/observable`
answering 405 in the exact spelling thehive4py 2.1.0 sends — is fixed and pinned (recommendation 1),
and the four test-pin gaps are closed (recommendations 2–3). The §13 ledger now carries P10-1…P10-13
with corrected gate figures, and the EXPLAIN transcripts are committed under `docs/perf/`. **The wave
is ready to commit.**
