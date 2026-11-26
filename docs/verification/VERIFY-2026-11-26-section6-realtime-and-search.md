# Verification — Finish §6: Global Search Measurement + Realtime/HTMX Breadth

**Plan:** [`PLAN-2026-11-26-section6-realtime-and-search.md`](../planning/PLAN-2026-11-26-section6-realtime-and-search.md)
**Implementation briefs:** backend and frontend briefs dated 2026-11-26
**Date:** 2026-11-26
**Scope:** `TODO.md §6.16-fu1` and `TODO.md §6.16 P7`
**Status:** **PASS WITH DOCUMENTED DEVIATIONS** — full pytest green after remediation.

This report replaces the earlier draft of this file. The draft used non-plan AC identifiers and
made claims about index migrations that did not match the working tree. Acceptance below uses the
AC identifiers in the approved plan.

---

## 1. Evidence summary

### Commands and results

```text
make check
  ruff ✓
  ruff format ✓
  mypy ✓  Success: no issues found in 151 source files
  django check ✓
  migrations in sync ✓
  CSS drift ✓
  playbook scripts ✓

.venv/bin/pytest -q \
  tests/conformance/test_realtime.py \
  tests/conformance/test_mcp_asgi.py \
  tests/conformance/test_t2_p2_surface.py \
  tests/conformance/test_search_api.py \
  tests/conformance/test_ui_search.py \
  tests/conformance/test_ui_htmx_scope.py \
  tests/conformance/test_realtime_org.py \
  tests/conformance/test_realtime_presence.py \
  tests/conformance/test_indexes.py
  => 98 passed in 77.78s

QA rerun of the same targeted suite
  => 98 passed in 78.14s
```

Full pytest was not run by the implementation agent, per the standing user instruction. No commit
or push was made.

### 1.1 Full-suite regression and remediation (2026-11-26)

The user ran a full suite and saved `/tmp/amalthea-full-pytest.log`:
`17 failed, 4345 passed, 2 skipped, 15 warnings, 2 subtests passed in 973.19s`.

| Failure cluster | Root cause | Remediation |
|---|---|---|
| `test_seed_migrations`, `test_schema_mutation_guards`, `test_settings_source_mapping`, `test_user_org_required`, `test_viewer_role_rename` | SQLite table rebuilds emitted `CREATE INDEX ... gin_trgm_ops`, which is invalid DDL; two mutation guards also referenced index-audit functions removed during this wave. | Added `core/indexes.py::PostgresExpressionGinIndex` so expression GIN indexes are no-ops off Postgres; restored the full `test_indexes.py` audit and added explicit §6.16 guards. |
| `test_seed_migrations::test_h3_2_rolling_back_a_seed_unapplies_nothing_in_another_app` | New alert/case search migrations depended on `observables/0015`, dragging them into a seed rollback. | Replaced that dependency with reverse-noop `ensure_trgm_extension_operation()` in each trigram migration. |
| `test_superadmin_delete_ui` | Delete modal includes existed only in the non-bulk list branch. | Restored matching delete modals in the bulk branches for alerts and cases. |
| `test_ui_p2_wiring::test_observable_list_ignores_malformed_type` | `hx-get="{{ request.get_full_path }}"` reflected malformed filters back into the form action. | HTMX filter forms now post to canonical route URLs and carry `partial=rows` explicitly. |

A second issue surfaced while re-running the index guards: the new `(owner_org, ...)` search-order
indexes made the implicit single-column FK indexes redundant. `Alert.owner_org` and
`Case.owner_org` now use `db_index=False` because those lookups are served as the left prefix of
the search-order indexes, with reverse migrations
`alerts/migrations/0022_alter_alert_owner_org.py` and
`cases/migrations/0028_alter_case_owner_org.py`.

Post-remediation verification:

```text
make check
  => all checks passed; mypy: Success: no issues found in 151 source files

.venv/bin/pytest -q \
  tests/conformance/test_seed_migrations.py \
  tests/conformance/test_superadmin_delete_ui.py \
  tests/conformance/test_ui_p2_wiring.py \
  tests/conformance/test_schema_mutation_guards.py \
  tests/conformance/test_settings_source_mapping.py \
  tests/conformance/test_user_org_required.py \
  tests/conformance/test_viewer_role_rename.py \
  tests/conformance/test_indexes.py \
  tests/conformance/test_ui_htmx_scope.py \
  tests/conformance/test_realtime.py \
  tests/conformance/test_mcp_asgi.py \
  tests/conformance/test_t2_p2_surface.py \
  tests/conformance/test_search_api.py \
  tests/conformance/test_ui_search.py \
  tests/conformance/test_realtime_org.py \
  tests/conformance/test_realtime_presence.py
  => 291 passed, 1 skipped in 468.87s
```

The user then reran full pytest, overwriting `/tmp/amalthea-full-pytest.log`:

```text
.venv/bin/pytest
  => 4390 passed, 3 skipped, 15 warnings, 2 subtests passed in 1025.15s (0:17:05)
```

The three skips are the existing Postgres-only planner/case-numbering guards; the warnings are the
pre-existing naive-datetime and CSS-token warnings. No failures remain.

### Benchmark artifacts

| Artifact | Meaning |
|---|---|
| `/tmp/global-search-benchmark.json` | Initial 100k-row Postgres run; multiple p95 breaches. |
| `/tmp/global-search-stress-final.json` | Stress rerun; `index-candidate`, three case-page breaches remain. |
| `/tmp/global-search-benchmark-baseline.json` | Realistic 10% common-term baseline without new indexes; all 18 p95 values below 500 ms. |
| `/tmp/global-search-benchmark-final.json` | Realistic 10% common-term final indexed run; all 18 p95 values below 500 ms. |

The final artifact records PostgreSQL `14.24 (Ubuntu 14.24-0ubuntu0.22.04.1)`, 100000 rows per
entity, `repeat=10`, `threshold_ms=500.0`, `common_every=100`, `rare_every=1000`, and
`acceptance.decision="accept-no-index"`. Each case contains the SQL and a full
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plan. Detailed tables are in
[`VERIFY-2026-11-26-global-search-performance.md`](./VERIFY-2026-11-26-global-search-performance.md).

### Reviews

- QA: targeted suite is meaningful and non-vacuous; no §6 blocker. MVP-loop coverage was noted as
  out of scope for this wave.
- Security: [`REVIEW-2026-11-26-section6-realtime-and-search.md`](../reviews/REVIEW-2026-11-26-section6-realtime-and-search.md)
  — no new High/Medium findings; one accepted Low residual for the dev-only presence HMAC fallback.
- Deviations: [`docs/spec/deviations.md`](../spec/deviations.md) → "Wave §6.16 final".

---

## 2. Plan AC evaluation

### Phase 0 — Decisions and scaffolding

| AC | Status | Evidence |
|---|---|---|
| AC-P0-1.1 | Met | Plan §1 names §6.16-fu1 and §6.16 P7. |
| AC-P0-1.2 | Met | OQ-1..OQ-6 resolved as approved defaults in plan §12 and reflected in code/docs. |
| AC-P0-1.3 | Deviated | Realtime has no schema change; search indexes were added after earlier benchmark runs triggered `index-candidate`. See W616-F-1. |

### Phase 1 — §6.16-fu1 measurement

| AC | Status | Evidence |
|---|---|---|
| AC-P1-1.1 | Met | Observable benchmark output schema/decision unchanged; shared helper extracted. |
| AC-P1-1.2 | Met | `core/db_benchmark.py` owns timing/explain helpers; observable benchmark rerun remains green. |
| AC-P1-2.1 | Met | Postgres-only guard in `benchmark_global_search`. |
| AC-P1-2.2 | Met | Deterministic seed, `ANALYZE`, report records distribution and match counts. |
| AC-P1-2.3 | Met | Dedicated benchmark orgs; rerun reported `seeded_rows=0`. |
| AC-P1-2.4 | Met | Each case records one SQL string and full JSON plan. |
| AC-P1-2.5 | Met | Page samples re-chain querysets; warm-up precedes timing. |
| AC-P1-2.6 | Met | Decision is `index-candidate` only if a p95 exceeds `--threshold-ms`. |
| AC-P1-3.1 | Met | `migrate --noinput` succeeded under `amalthea.settings.test_pg`. |
| AC-P1-3.2 | Met | Benchmark wrote `/tmp/global-search-benchmark.json`; later artifacts remain in `/tmp`. |
| AC-P1-3.3 | Met | Postgres version, all p95 values, and plan node types recorded in artifacts and verification tables. |
| AC-P1-4.1 | Deviated | Final decision is `accept-no-index`, but indexes were retained from earlier `index-candidate` evidence. See W616-F-1. |
| AC-P1-4.2 | Met | Per-field `gin_trgm_ops` indexes added for alert/case text fields. |
| AC-P1-4.3 | Met | Postgres migrations execute; SQLite guards/no-ops covered by `test_indexes.py`. |
| AC-P1-4.4 | Met | Final realistic run has all p95 values under threshold and selective plans use bitmap/trigram index paths. Stress residual is separate. See W616-F-2. |
| AC-P1-4.5 | Met | `makemigrations --check --dry-run` and `make check` passed. |
| AC-P1-5.1 | Met | Verification reports contain commands, before/after tables, plans summary, and AC evaluation. |
| AC-P1-5.2 | Met | Prior search verification and review were updated from open to resolved. |
| AC-P1-5.3 | Met | `TODO.md` closed; `COMPLETED.md` records both items. |

### Phase 2 — Org channel

| AC | Status | Evidence |
|---|---|---|
| AC-P2-1.1 | Met | `/ws/org/<org_id>/` route and HTTP fallback added. |
| AC-P2-1.2 | Met | `test_realtime_org.py` covers `4401`, `4404`, `4000`, and unknown client frames. |
| AC-P2-1.3 | Met | OQ-1 implemented: superAdmin may join existing org rooms; cross-org join audits `realtime_org_join`. |
| AC-P2-1.4 | Met | `publish_org_event()` derives `org_<owner_org_id>` server-side. |
| AC-P2-1.5 | Met | Publisher calls use `transaction.on_commit`. |
| AC-P2-2.1 | Met | `append_timeline_event` emits one owner-org `cases/case-updated` refresh for canonical kinds. |
| AC-P2-2.2 | Deviated | Alert create emits `alerts/alert-created`, and a linked-alert comment emits `cases/case-updated`; generic alert update/triage does not emit. See W616-P7-1. |
| AC-P2-2.3 | Deviated | Bulk alert update emits no refresh. Zero events is within "at most one" but does not deliver the intended live update. See W616-P7-1. |
| AC-P2-2.4 | Met | Shared cases publish only to `case.owner_org_id`. |
| AC-P2-2.5 | Met | Refresh payloads contain only `surface`, `reason`, `object_id`, `occurred_at`. |

### Phase 3 — Presence

| AC | Status | Evidence |
|---|---|---|
| AC-P3-1.1 | Met | HMAC member deduplicates multiple tabs for one user; `test_realtime_presence.py`. |
| AC-P3-1.2 | Met | Distinct users counted separately. |
| AC-P3-1.3 | Met | Disconnect decrements; TTL pruning covers abrupt disconnect. |
| AC-P3-1.4 | Met | Wire frame is exactly `{"type":"presence","count":N}`. |
| AC-P3-1.5 | Met | Case-room auth gate runs before presence join; cross-org case join still closes `4404`. |
| AC-P3-2.1 | Met | `#case-presence` visible only on case detail with `aria-live="polite"`. |
| AC-P3-2.2 | Met | FA users icon is `aria-hidden`; visible singular/plural text remains. |
| AC-P3-2.3 | Met | Offline state shows `Live updates unavailable`, not a stale count. |
| AC-P3-2.4 | Met | `test_ui_inline_assets.py` and `make check` stayed green. |

### Phase 4 — Scoped HTMX

| AC | Status | Evidence |
|---|---|---|
| AC-P4-1.1 | Met | Alert/case/observable list filters have HTMX paths updating rows containers. |
| AC-P4-1.2 | Met | `hx-push-url` preserves filters, `page`, and `per_page`. |
| AC-P4-1.3 | Met | Plain GET full-page fallbacks covered per list. |
| AC-P4-1.4 | Met | Viewer/cross-org partial semantics match full pages. |
| AC-P4-2.1 | Met | Dashboard `stats`, `recent-alerts`, and `recent-cases` partials return target fragments. |
| AC-P4-2.2 | Met | Org refresh frames trigger one debounced full-dashboard partial refresh. |
| AC-P4-2.3 | Met | Dashboard partials reuse full-page org/viewer scoping. |
| AC-P4-3.1 | Met | General, Tasks, and Observables each have at least one HTMX mutation. |
| AC-P4-3.2 | Met | Plain POST fallback tests present for converted mutations. |
| AC-P4-3.3 | Met | CSRF tokens preserved; `test_ui_htmx_scope.py`. |
| AC-P4-3.4 | Met | Viewer submissions rejected with prior status. |
| AC-P4-3.5 | Met | No inline JS/CSS introduced; inline-assets guard green. |

### Phase 5 — Documentation and closeout

| AC | Status | Evidence |
|---|---|---|
| AC-P5-1.1 | Met | `docs/spec/realtime.md` documents org room, refresh frame, presence frame, close codes, and `mcp_server.asgi.build_application`. |
| AC-P5-1.2 | Met | W616-F-1, W616-F-2, W616-P7-1, and W616-P7-2 recorded in deviations. |
| AC-P5-1.3 | Met | `TODO.md` §6.16 P7 and §6.16-fu1 closed after this verification. |
| AC-P5-1.4 | Met | `COMPLETED.md` records both items with evidence links. |

---

## 3. Deviations accepted for closeout

1. **W616-F-1 — index adoption.** Initial and stress runs were `index-candidate`; final realistic runs were `accept-no-index`. Indexes were retained based on the earlier breach evidence and large selective-query improvement.
2. **W616-F-2 — stress residual.** Three case-page p95 values exceed 500 ms in the older stress artifact. Final shipped query shape passes all 18 cases.
3. **W616-P7-1 — org refresh breadth.** Alert create and case-linked mutations publish; generic alert update/triage and bulk update do not. Deferring bulk avoids publishing from an ambiguous user-supplied org context for superAdmin requests spanning organisations.
4. **W616-P7-2 — presence HMAC fallback.** Dev/test can use `amalthea-dev-only`; production fails closed on a strong `DJANGO_SECRET_KEY`. Security accepted this as Low.

No deviation is a data-leak finding. Tenant scoping, auth-before-accept, CSRF, CSP, and presence privacy passed review.

---

## 4. Regression notes fixed during implementation

- `ui/static/ui/live.js` had lost case-timeline behaviour. The final file preserves the pinned
  `insertBefore` path and no longer uses `appendChild` for new ledger entries.
- Case timeline event publishing had been removed from `cases/ledger.py`. It was restored with
  `timeline_event_json` and `publish_case_event` on commit.
- Presence joins briefly broadcast frames and confused the first case-socket frame. Join broadcasts
  were removed; only `presence_ping` and disconnect broadcast counts.
- Standalone presence initialisation was scoped so case-detail pages do not run two presence handlers.

---

## 5. Final disposition

§6.16-fu1 and §6.16 P7 are complete and tracked as closed. All plan ACs are Met or Deviated with
documented rationale. Static gates, the post-remediation regression suite, and the user's final full pytest run are
green. A prior full pytest run exposed 17 failures; all root causes were remediated and verified in
§1.1. No commit or push was performed.
