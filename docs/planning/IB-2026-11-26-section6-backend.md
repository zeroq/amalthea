# Implementation Brief — Section 6 Backend: Search Measurement + Realtime Protocol

**Plan:** [`PLAN-2026-11-26-section6-realtime-and-search.md`](./PLAN-2026-11-26-section6-realtime-and-search.md)
**Status:** Approved — handoff to `django-backend`
**Scope:** §6.16-fu1 backend work and §6.16 P7 server-side realtime work
**Approved defaults:** OQ-1 through OQ-6 in the plan are accepted.

## Ownership

### You own
- `core/db_benchmark.py` (new shared helpers)
- `observables/management/commands/benchmark_observable_count.py` (refactor only; preserve behaviour/output)
- `query/management/commands/benchmark_global_search.py`
- `query/management/__init__.py`, `query/management/commands/__init__.py`
- `alerts/models.py`, `cases/models.py` and new migrations **only if benchmark triggers indexes**
- `realtime/publisher.py`, `realtime/consumers.py`, `realtime/routing.py`, `realtime/views.py`
- `realtime/presence.py` (new)
- server-side event emission points: `cases/ledger.py`, alert/escalation/triage service modules, selected UI mutation service modules that do not belong to the frontend brief
- backend tests: `tests/conformance/test_realtime.py`, `test_realtime_org.py`, `test_realtime_presence.py`, `test_t2_p2_surface.py`, `test_indexes.py`, `test_search_api.py`, `test_mcp_asgi.py`
- `docs/verification/VERIFY-2026-11-26-global-search-performance.md`
- `docs/verification/VERIFY-2026-11-01-global-search.md`, `docs/reviews/REVIEW-2026-11-13-global-search.md` (evidence/status rows only)
- `docs/spec/realtime.md`

### Do not touch
- `ui/templates/**`, `ui/static/**`, `ui/views/**` except `realtime/views.py::websocket_fallback`
- `TODO.md`, `COMPLETED.md` — verifier closes items after AC review
- `.env`, CI config, secret-scan scripts, git hooks
- git history; do not commit or push

## Constraints

- Do **not** run full pytest unless the user explicitly asks.
- Do **not** commit or push.
- Use the gitignored local `.env` for the Postgres endpoint; never print credentials.
- Benchmark output JSON stays in `/tmp` and must not be committed.
- Any deviation from the approved plan must be recorded in `docs/spec/deviations.md`.

## Tasks and acceptance criteria

### B1 — Extract reusable DB benchmark helpers
1. Create `core/db_benchmark.py` with `timings`, `explain`, and plan-node helpers.
2. Refactor `benchmark_observable_count.py` to use them without changing its JSON schema or decision logic.

AC-B1.1: Observable benchmark still reports baseline/final p95 and `accept-no-index` with `obs_data_upper_trgm_idx` evidence.
AC-B1.2: Helpers handle parameterized page SQL and raw count SQL.
AC-B1.3: `make check` passes.

### B2 — Add global search benchmark command
Create `query/management/commands/benchmark_global_search.py` with:

```text
--count 100000
--batch-size 2000
--repeat 10
--threshold-ms 500.0
--seed
--output /tmp/global-search-benchmark.json
--org-name global-search-benchmark
```

Measure at minimum:

- `case_text_count`, `case_text_page`
- `case_digit_count`, `case_digit_page`
- `alert_text_count`, `alert_text_page`
- `observable_text_count`, `observable_text_page`
- `case_text_page_200`, `alert_text_page_200`
- `case_text_deep_page`, `alert_text_deep_page`

AC-B2.1: Command refuses non-PostgreSQL engines with a clear `CommandError`.
AC-B2.2: Seed is deterministic and idempotent; rerun reports rows already present.
AC-B2.3: `ANALYZE` runs after seeding and when rows already exist.
AC-B2.4: Every case captures one SQL statement plus `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`.
AC-B2.5: Page timings re-chain querysets so no sample measures Django’s result cache.
AC-B2.6: Decision is `index-candidate` only when p95 exceeds `--threshold-ms`.

### B3 — Run benchmark on PostgreSQL and decide indexes
Use the existing user-provided endpoint from `.env` under `amalthea.settings.test_pg`.

AC-B3.1: `migrate --noinput` succeeds.
AC-B3.2: Benchmark completes at 100k rows per entity and writes `/tmp/global-search-benchmark.json`.
AC-B3.3: Verification report records Postgres version, commands, p95 table, plans, and AC decisions.
AC-B3.4: If all p95 values are under threshold, no model/migration change is made.
AC-B3.5: If any p95 breaches threshold, add only the per-field trigram indexes needed for that entity:
- alerts: `alert_title_upper_trgm_idx`, `alert_description_upper_trgm_idx`, `alert_summary_upper_trgm_idx`
- cases: `case_title_upper_trgm_idx`, `case_description_upper_trgm_idx`, `case_summary_upper_trgm_idx`

AC-B3.6: New index migrations use `core.pg_indexes.gin_expression_operation` and model `GinIndex(OpClass(Upper(Cast(...)), name="gin_trgm_ops"))`.
AC-B3.7: Migrations are Postgres-only DDL and SQLite no-ops.
AC-B3.8: `makemigrations --check --dry-run` passes under default and `test_pg` settings.
AC-B3.9: If indexes are added, rerun benchmark and show all final p95 under threshold plus at least one plan using a new index.

### B4 — Add org WebSocket channel
Extend `realtime/routing.py`, `realtime/consumers.py`, `realtime/views.py`, and `realtime/publisher.py`.

Route: `/ws/org/<org_id>/`

AC-B4.1: Anonymous closes `4401` before `accept()`.
AC-B4.2: Invalid UUID closes `4000`.
AC-B4.3: Foreign org and non-existent org close `4404` uniformly.
AC-B4.4: superAdmin may join any existing org room and each cross-org connect writes an `AuditLog` row.
AC-B4.5: Unknown client frame closes `4000`.
AC-B4.6: Publisher is `publish_org_event(org_id, event_type, payload)` and sends to `org_<org_id>`.
AC-B4.7: Org id is derived from server-side object owner, never request data.
AC-B4.8: Publish calls are inside `transaction.on_commit`.
AC-B4.9: HTTP fallback remains available and unchanged for non-upgrade clients.

Frame contract:

```json
{
  "type": "event",
  "event_type": "refresh",
  "payload": {
    "surface": "alerts | cases | dashboard",
    "reason": "alert-created | alert-updated | case-created | case-updated | case-closed",
    "object_id": "<uuid-or-null>",
    "occurred_at": "<iso8601>"
  }
}
```

### B5 — Emit bounded org refresh events
AC-B5.1: Case ledger events emit one owner-org refresh with `surface` in `cases`/`dashboard`.
AC-B5.2: Alert create/update/triage emits one owner-org refresh with `surface` in `alerts`/`dashboard`.
AC-B5.3: Bulk operations emit at most one org refresh per request.
AC-B5.4: Shared cases do not emit into share-holder org rooms.
AC-B5.5: Event payloads contain no event bodies, comments, raw payloads, or user identities.

### B6 — Add counts-only presence
Create `realtime/presence.py` and extend `CaseConsumer`.

Store contract:

```text
join(case_id, user) -> int
touch(case_id, user) -> int
leave(case_id, user) -> int
```

AC-B6.1: Production store uses Redis sorted sets with TTL and HMAC user members.
AC-B6.2: Dev/test store is in-memory with the same contract.
AC-B6.3: Two tabs for the same user count once.
AC-B6.4: Two distinct users count twice.
AC-B6.5: Disconnect decrements; TTL handles abrupt disconnect.
AC-B6.6: Client `{"type":"presence_ping"}` refreshes TTL; unknown frames still close `4000`.
AC-B6.7: Broadcast frame is exactly `{"type":"presence","count":N}` with no identity fields.
AC-B6.8: Cross-org case room join remains refused before presence join.

### B7 — Backend tests and docs
AC-B7.1: Add/extend realtime auth, publisher, presence, ASGI, and index tests listed in ownership.
AC-B7.2: `make check` passes.
AC-B7.3: Targeted conformance tests pass.
AC-B7.4: `docs/spec/realtime.md` documents org room, refresh frames, presence frames, close codes, and the actual ASGI builder path.
AC-B7.5: No full pytest run.
AC-B7.6: Return a final report listing files changed, commands run, results, benchmark decision, and any deviations.

## Suggested verification commands

```bash
make check
.venv/bin/pytest -q \
  tests/conformance/test_realtime.py \
  tests/conformance/test_realtime_org.py \
  tests/conformance/test_realtime_presence.py \
  tests/conformance/test_t2_p2_surface.py \
  tests/conformance/test_search_api.py \
  tests/conformance/test_indexes.py \
  tests/conformance/test_mcp_asgi.py
```

Benchmark command is run only with the approved local Postgres configuration.
