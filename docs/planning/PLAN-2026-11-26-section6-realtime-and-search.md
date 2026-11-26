# PLAN-2026-11-26 -- Finish §6: Global Search Measurement + Realtime/HTMX Breadth

**Status:** Approved 2026-11-26 -- implementation handoff authorized
**Scope:** TODO §6.16-fu1 and TODO §6.16 P7
**Date:** 2026-11-26
**Approved defaults:** OQ-1 through OQ-6 accepted as written in §12.
**Implementation briefs:** [`IB-2026-11-26-section6-backend.md`](./IB-2026-11-26-section6-backend.md), [`IB-2026-11-26-section6-frontend.md`](./IB-2026-11-26-section6-frontend.md)
**Related plans:** `PLAN-2026-10-11-ui-completeness.md`, `PLAN-2026-11-02-search-page-rail-entry.md`
**ADRs:** ADR-001 (Django/DRF/Channels), ADR-003 (CSP/static assets), ADR-004 (tenant isolation), ADR-006 (viewer semantics)

---

## 1. Summary

TODO §6 has two open items:

1. **§6.16-fu1 -- Global search Postgres measurement.** Measure the shipped `/search` query shapes at realistic volume on PostgreSQL, then add trigram indexes only if the evidence warrants it.
2. **§6.16 P7 -- Realtime breadth + presence + scoped HTMX.** Add a per-organisation WebSocket room so alert list, case list, and dashboard can refresh live; add counts-only case presence; and convert a bounded set of list filters and case-detail mutations to HTMX partial swaps with full POST/GET fallback.

This plan keeps the two items independently shippable. Search measurement can land first because the Postgres endpoint is already available; P7 is larger and gets its own verification pass.

**Interpretation confirmed by this plan:** "two remaining items of §6" means the two checkbox items currently open under `TODO.md §6`: `6.16-fu1` and the P7 portion of `6.16`.

---

## 2. Goals

- Produce a reproducible PostgreSQL benchmark for global search with exact SQL, `EXPLAIN (ANALYZE, BUFFERS)` plans, and p95 timings.
- Decide, from evidence, whether case/alert trigram indexes are needed. Preserve observable search's existing trigram index.
- Publish org-scoped realtime refresh signals for alert list, case list, and dashboard without leaking cross-tenant data.
- Show a case-detail presence count without exposing identities or creating new personal data.
- Adopt HTMX only where it improves the existing interaction: primary list filters and selected case-detail mutations.
- Preserve progressive enhancement: every HTMX path degrades to ordinary GET/POST and every POST keeps CSRF protection.

---

## 3. Non-goals

- No full-text search engine, `tsvector`, or change to `icontains` substring semantics.
- No rewrite of the global search API response contract.
- No global HTMX conversion of settings, taxonomy, playbooks, or audit pages.
- No new relational tables or model fields for presence.
- No identity display in presence: no names, logins, user IDs, or org membership in frames.
- No cross-organisation fan-out for shared cases; org refresh events are published to the object owner org only.
- No new pipeline/asset build changes beyond what existing CSP-compliant static JS requires.
- No full pytest run unless the user explicitly requests one; standing user instruction remains in force.

---

## 4. Architecture

### 4.1 Stack rationale

- **Django 5.x + DRF** remains the single source of truth for auth, org scoping, views, and serializers (ADR-001).
- **Django Channels + Redis channel layer** extends the existing case room with an org room and presence frames. Async consumers continue to perform auth before `accept()`.
- **HTMX** is used as progressive enhancement over server-rendered Django templates. It swaps partials returned by the same views; it does not become a client-side state machine.
- **Tailwind/CSP** constraints remain: no inline `<script>`, `style=`, or `on*=` handlers; all JS lives in vendored/static files (ADR-003).
- **PostgreSQL trigram indexes** are the only schema change, and only if the benchmark's p95 threshold is breached. They preserve the current `UPPER(col) LIKE UPPER(%term%)` semantics.

### 4.2 Decision references

| Decision | Source | Effect on this plan |
|---|---|---|
| Dark, keyboard-driven UI | AGENTS.md §6 | Presence and live indicators use text labels, FA icons with `aria-hidden`, and keep keyboard paths intact. |
| Tenant isolation | ADR-004 | Org room authorization derives from server-side user/org state; cross-org join refused with uniform `4404`. |
| Viewer semantics | ADR-006 | Viewers may subscribe to read rooms and partials but never receive write controls. |
| CSP/no inline assets | ADR-003 | New JS is static; HTMX config remains in `base.html` meta only. |
| Search semantics | `PLAN-2026-11-02-search-page-rail-entry.md` | Keep `icontains`, term AND/field OR, digit-only case number, and org/share scoping. |
| Realtime case room | `realtime/consumers.py` | Extend, do not replace; existing sync protocol stays stable. |

---

## 5. Database schema / migrations

### 5.1 Realtime

No new tables, fields, or migrations. Presence is ephemeral and stored outside relational state.

Presence store contract:

```text
PresenceStore.join(case_id, user) -> int
PresenceStore.touch(case_id, user) -> int
PresenceStore.leave(case_id, user) -> int
```

- Production implementation uses Redis sorted sets keyed `presence:case:<case_id>`, score = expiry timestamp, member = HMAC-SHA256(user.pk, SECRET_KEY)[:16]. Expired members are pruned before counting.
- Dev/test implementation uses a locked in-memory store with the same TTL behaviour.
- Only the integer count is broadcast. The hashed member never leaves the server.

### 5.2 Search indexes -- conditional

The observable table already has `obs_data_upper_trgm_idx` from `observables/0015`. New indexes are added only if benchmark p95 breaches the agreed threshold.

Potential indexes:

```text
alert_title_upper_trgm_idx        GIN (UPPER(title::text)) gin_trgm_ops
alert_description_upper_trgm_idx  GIN (UPPER(description::text)) gin_trgm_ops
alert_summary_upper_trgm_idx      GIN (UPPER(summary::text)) gin_trgm_ops

case_title_upper_trgm_idx         GIN (UPPER(title::text)) gin_trgm_ops
case_description_upper_trgm_idx   GIN (UPPER(description::text)) gin_trgm_ops
case_summary_upper_trgm_idx       GIN (UPPER(summary::text)) gin_trgm_ops
```

Implementation constraints:

- Model state uses `GinIndex(OpClass(Upper(Cast(field, TextField())), name="gin_trgm_ops"))`.
- Database operations use `core.pg_indexes.gin_expression_index_operation()` so SQLite migrations remain no-ops.
- Migrations depend on `observables.0015_observable_data_upper_trgm` for the `pg_trgm` extension.
- `makemigrations --check --dry-run` must report no changes under both default settings and `amalthea.settings.test_pg`.

---

## 6. API contracts

### 6.1 WebSocket `/ws/org/<org_id>/`

Handshake:

| Situation | Result |
|---|---|
| Anonymous | Close `4401` before `accept()` |
| Authenticated, non-superAdmin, `user.org_id != org_id` | Close `4404` |
| Authenticated, org does not exist | Close `4404` |
| Invalid UUID | Close `4000` |
| superAdmin joining another org | Allow only if explicitly decided; write an `AuditLog` cross-org read row |
| Unknown client frame | Close `4000` |

Server frame:

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

- Client does not send frames. This keeps the contract smaller than the case room.
- Publish from server-side mutation choke points using the object's `owner_org_id`, never a user-supplied org id.

### 6.2 WebSocket `/ws/case/<case_id>/` extension

Existing frames stay unchanged. Add:

```json
{"type": "presence", "count": 2}
```

Client may send `{"type": "presence_ping"}` to refresh its TTL. Any other frame still closes `4000`.

### 6.3 HTTP partial responses

Reuse existing URLs; do not add a second routing tree.

| Request | Response when `HX-Request: true` and `partial` set |
|---|---|
| `GET /alerts/?partial=rows&...` | `_alert_list_rows.html` table body |
| `GET /cases/?partial=rows&...` | `_case_list_rows.html` table body |
| `GET /observables/?partial=rows&...` | `_observable_list_rows.html` table body |
| `GET /dashboard/?partial=stats` | dashboard stat cards |
| `GET /dashboard/?partial=recent-alerts` | recent alerts table body |
| `GET /dashboard/?partial=recent-cases` | recent cases table body |

Rules:

- Without `HX-Request`, the view renders the full page exactly as today.
- Partial endpoints are read-only GETs and use the same `org_q`/`case_visible_q` scoping as the full page.
- Case-detail mutation views add an `HX-Request` branch returning a small partial or `204 + HX-Trigger`; plain POST redirects exactly as today.
- CSRF tokens are preserved on all POST forms.

### 6.4 Global search benchmark command

Add `query/management/commands/benchmark_global_search.py`:

```text
--count 100000
--batch-size 2000
--repeat 10
--threshold-ms 500.0
--seed
--output /tmp/global-search-benchmark.json
--org-name global-search-benchmark
```

Report JSON contains: generated at, settings module, Postgres version, target/existing/seeded rows, repeat, threshold, acceptance, and cases. Each case contains `name`, `kind`, `sql`, `plan`, `node_types`, `explain_execution_time_ms`, and `runs_ms/min_ms/median_ms/p95_ms`.

Measured shapes:

- `case_text_count`, `case_text_page`
- `case_digit_count`, `case_digit_page`
- `alert_text_count`, `alert_text_page`
- `observable_text_count`, `observable_text_page`
- `case_text_page_200`, `alert_text_page_200`
- `case_text_deep_page`, `alert_text_deep_page`

Decision rule:

```text
accept-no-index if p95_ms <= threshold_ms for every case
index-candidate otherwise
```

Plan shape is recorded as evidence but the machine decision remains p95-only, matching the §12.1-fu1 precedent.

---

## 7. UI / UX

### 7.1 Live refresh

- Add a compact `Live` status chip on alert list, case list, and dashboard: `Live`, `Reconnecting…`, or `Offline`, with `aria-live="polite"`.
- On `refresh` frames, debounce 250 ms then issue `htmx.ajax('GET', currentUrlWithPartial, {target, swap:'innerHTML'})`.
- Preserve current filters, `page`, and `per_page` in the refresh URL.
- Do not mutate result rows in JS; all rows come from server-rendered partials.
- No auto-refresh when the tab is hidden; resume on visibility change with one refresh.

### 7.2 Presence

- Case detail header shows:

```html
<span id="case-presence" aria-live="polite">
  <i class="fa-solid fa-users" aria-hidden="true"></i>
  2 analysts viewing
</span>
```

- Singular/plural copy: `1 analyst viewing` / `N analysts viewing`.
- If the socket is unavailable, show `Live updates unavailable` rather than a stale count.
- Presence count is not a badge of identity and is never clickable.

### 7.3 HTMX scope

Primary list filters:

- Alert list: `scope`, `severity`, `status`, `q`, `page`, `per_page`.
- Case list: `status`, `severity`, `q`, `page`, `per_page`.
- Observable list: `q`, `type`, `page`, `per_page`.

Case-detail mutations:

- Existing comment form stays HTMX and gains a presence-safe refresh trigger.
- General tab: status, severity, and assignee quick actions.
- Tasks tab: create and toggle task.
- Observables tab: add observable and remove observable link.

Every converted control keeps a visible label, works from the keyboard, and has a plain POST fallback.

---

## 8. Phases, tasks, and acceptance criteria

### Phase 0 -- Decisions and scaffolding

**P0-1. Confirm scope and protocol decisions.**

- AC-P0-1.1: This plan explicitly names §6.16-fu1 and §6.16 P7 as the two items.
- AC-P0-1.2: Open Questions §12 are resolved or explicitly defaulted before implementation starts.
- AC-P0-1.3: No schema change for P7; any search index is benchmark-gated.

### Phase 1 -- §6.16-fu1 measurement

**P1-1. Add shared benchmark helpers.**

Files: `core/db_benchmark.py`, refactor `observables/management/commands/benchmark_observable_count.py`.

- AC-P1-1.1: Existing observable benchmark output schema and decisions remain unchanged.
- AC-P1-1.2: `timings`, `explain`, and node-type helpers are shared and covered by existing observable benchmark rerun.

**P1-2. Add global search benchmark command.**

Files: `query/management/commands/benchmark_global_search.py`, `query/management/__init__.py`, `query/management/commands/__init__.py`.

- AC-P1-2.1: Command refuses non-PostgreSQL engines with a clear `CommandError`.
- AC-P1-2.2: `--seed` creates deterministic case/alert/observable rows and runs `ANALYZE` after seeding and when rows already exist.
- AC-P1-2.3: Seed is idempotent; rerunning with `--seed` reports rows already present and does not duplicate data.
- AC-P1-2.4: Each measured case captures exactly one SQL statement for count/page and writes a full `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plan.
- AC-P1-2.5: Page timings re-chain the queryset so Django's result cache never makes a sample measure zero SQL.
- AC-P1-2.6: Report decision is `index-candidate` only when at least one p95 exceeds `--threshold-ms`.

**P1-3. Run benchmark on the existing user-provided Postgres endpoint.**

- AC-P1-3.1: `migrate --noinput` succeeds under `amalthea.settings.test_pg`.
- AC-P1-3.2: Benchmark runs at 100k rows per entity and writes `/tmp/global-search-benchmark.json` (not committed).
- AC-P1-3.3: Verification report records Postgres version, all p95 values, and all relevant plan node types.

**P1-4. Add indexes only if warranted.**

Files if needed: `alerts/models.py`, `cases/models.py`, new migrations in `alerts/migrations/` and `cases/migrations/`, `tests/conformance/test_indexes.py`.

- AC-P1-4.1: If decision is `accept-no-index`, no model/migration change is made and the report says so.
- AC-P1-4.2: If decision is `index-candidate`, per-field `gin_trgm_ops` indexes are added for the breaching entity.
- AC-P1-4.3: Index migrations execute on Postgres and are no-ops on SQLite.
- AC-P1-4.4: After indexing, every final p95 is below threshold and at least one plan uses the new trigram index for a selective term.
- AC-P1-4.5: `makemigrations --check --dry-run` passes under both default and `test_pg` settings.

**P1-5. Close §6.16-fu1 documentation.**

Files: `docs/verification/VERIFY-2026-11-26-global-search-performance.md`, `docs/verification/VERIFY-2026-11-01-global-search.md`, `docs/reviews/REVIEW-2026-11-13-global-search.md`, `TODO.md`, `COMPLETED.md`.

- AC-P1-5.1: New verification report contains before/after tables, plans, commands, and AC evaluation.
- AC-P1-5.2: Existing search verification's residual-risk row is updated from open to resolved.
- AC-P1-5.3: TODO item is removed from open work and recorded in COMPLETED.

### Phase 2 -- Org channel

**P2-1. Presence-independent org consumer and publisher.**

Files: `realtime/publisher.py`, `realtime/consumers.py`, `realtime/routing.py`, `realtime/views.py`.

- AC-P2-1.1: `/ws/org/<org_id>/` exists with HTTP fallback.
- AC-P2-1.2: Anonymous socket closes `4401`; foreign org closes `4404`; invalid UUID closes `4000`; unknown client frame closes `4000`.
- AC-P2-1.3: superAdmin behavior matches Open Question OQ-1 default and is audited if cross-org.
- AC-P2-1.4: `publish_org_event()` sends to `org_<owner_org_id>` and never to a caller-provided room name.
- AC-P2-1.5: Publisher is called inside `transaction.on_commit`.

**P2-2. Emit bounded org refresh events.**

Files: `cases/ledger.py`, alert/escalation/triage services, selected UI mutation services.

- AC-P2-2.1: Case ledger events emit one `refresh` to the owner org with `surface` in `cases`/`dashboard`.
- AC-P2-2.2: Alert create/update/triage emits one `refresh` with `surface` in `alerts`/`dashboard`.
- AC-P2-2.3: Bulk operations emit at most one org refresh per request.
- AC-P2-2.4: Shared cases do not emit into the share holder's org room.
- AC-P2-2.5: No event payload contains event bodies, comments, raw payloads, or user identities.

### Phase 3 -- Presence

**P3-1. Presence store and case consumer integration.**

Files: `realtime/presence.py`, `realtime/consumers.py`, tests.

- AC-P3-1.1: Two tabs for the same user count as one viewer.
- AC-P3-1.2: Two distinct users count as two viewers.
- AC-P3-1.3: Disconnect decrements the count; TTL handles abrupt disconnect.
- AC-P3-1.4: Presence frames contain only `{"type":"presence","count":N}`.
- AC-P3-1.5: Cross-org case room join is still refused before presence join.

**P3-2. Presence UI.**

Files: `ui/templates/ui/case_detail.html`, `ui/static/ui/live.js`, optional CSS component.

- AC-P3-2.1: Presence indicator is visible only on case detail and has accessible live text.
- AC-P3-2.2: Icon is `aria-hidden="true"`; text label is always present.
- AC-P3-2.3: Offline state does not show a stale count.
- AC-P3-2.4: `test_ui_inline_assets.py` stays green.

### Phase 4 -- Scoped HTMX

**P4-1. List filter partials.**

Files: list views, three list templates, three new row partials, tests.

- AC-P4-1.1: Each scoped list filter/form has an HTMX path updating only the rows container.
- AC-P4-1.2: `hx-push-url` preserves URL state for filters, `page`, and `per_page`.
- AC-P4-1.3: Plain GET renders the full page and is covered by a test per list.
- AC-P4-1.4: Cross-org and viewer-scoped requests return the same 404/partial semantics as full pages.

**P4-2. Dashboard live partials.**

Files: `ui/views/dashboard.py`, `ui/templates/ui/dashboard.html`, dashboard partials, tests.

- AC-P4-2.1: `partial=stats`, `partial=recent-alerts`, and `partial=recent-cases` return only the target fragment for HTMX requests.
- AC-P4-2.2: Org refresh frames refresh all three fragments with one debounced action.
- AC-P4-2.3: Dashboard partials obey org scoping and viewer permissions.

**P4-3. Case-detail mutation partials.**

Files: selected case detail views/templates, tests.

- AC-P4-3.1: At least one mutation in General, Tasks, and Observables uses HTMX with a targeted partial or trigger.
- AC-P4-3.2: Every converted mutation has a plain POST fallback test.
- AC-P4-3.3: CSRF is enforced for every converted POST.
- AC-P4-3.4: Viewer submissions are rejected with the same status as today.
- AC-P4-3.5: No inline JS/CSS is introduced.

### Phase 5 -- Documentation and closeout

**P5-1. Update realtime spec and trackers.**

Files: `docs/spec/realtime.md`, `docs/spec/deviations.md`, `TODO.md`, `COMPLETED.md`, verification docs.

- AC-P5-1.1: `docs/spec/realtime.md` documents org room, refresh frames, presence frames, close codes, and the actual `mcp_server.asgi.build_application` path.
- AC-P5-1.2: Deviations from Q3/Q-OQ defaults are recorded.
- AC-P5-1.3: `TODO.md §6.16 P7` and `§6.16-fu1` are removed only after independent verification.
- AC-P5-1.4: `COMPLETED.md` records both items with evidence links.

---

## 9. Security

- **Auth before accept:** both consumers authenticate and authorize before `accept()`; refused sockets never join groups.
- **No existence oracle:** anonymous yields `4401`; invalid UUID yields `4000`; forbidden and non-existent yield `4404`.
- **Org derivation:** publish uses object owner org, never request data. Shared cases publish only to owner org.
- **superAdmin:** if allowed cross-org (OQ-1), each cross-org join writes an `AuditLog` row and no identity is broadcast.
- **Presence privacy:** only counts are sent; member identifiers are HMACs stored server-side with TTL.
- **XSS:** live JS uses DOM text nodes; HTMX swaps server-rendered escaped templates; no `innerHTML` construction from event data.
- **CSP:** static JS only; no inline scripts/styles or `on*` attributes.
- **CSRF:** all converted POSTs keep Django CSRF middleware and token markup.
- **Rate/load:** client debounce 250 ms; partials are paginated; suppressed refresh while tab hidden.
- **Secrets:** benchmark credentials remain in gitignored `.env`; JSON reports and docs contain no credentials.

---

## 10. Testing

### 10.1 Unit/conformance

New/extend:

- `tests/conformance/test_realtime.py` -- org publisher/consumer, foreign-org refusal, case room regression.
- `tests/conformance/test_realtime_org.py` -- org auth matrix, invalid UUID, frame schema, on-commit publisher.
- `tests/conformance/test_realtime_presence.py` -- unique-user count, duplicate tabs, disconnect, TTL, no identity in frames.
- `tests/conformance/test_t2_p2_surface.py` -- new refresh frames and partial responses.
- `tests/conformance/test_ui_htmx_scope.py` -- partials, no-JS fallback, CSRF, viewer/same-org/cross-org matrix.
- `tests/conformance/test_indexes.py` -- model-level trigram index guards if indexes are added.
- `tests/conformance/test_ui_inline_assets.py` -- must stay green.
- `tests/conformance/test_mcp_asgi.py` -- WebSocket branch regression.

### 10.2 Manual gates

```bash
make check
.venv/bin/pytest -q \
  tests/conformance/test_realtime.py \
  tests/conformance/test_realtime_org.py \
  tests/conformance/test_realtime_presence.py \
  tests/conformance/test_t2_p2_surface.py \
  tests/conformance/test_ui_htmx_scope.py \
  tests/conformance/test_ui_search.py \
  tests/conformance/test_search_api.py \
  tests/conformance/test_indexes.py

DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_global_search --seed \
  --output /tmp/global-search-benchmark.json
```

Full pytest is not run unless explicitly requested by the user.

---

## 11. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Org room widens realtime blast radius | Auth before accept; owner-org-only publish; foreign-org test; no payload bodies. |
| R2 | Presence becomes personal data | Counts-only frames; HMAC members; TTL; no names/IDs in UI or protocol. |
| R3 | Redis presence diverges across workers | Redis sorted-set store in prod; in-memory store dev/test only; document boundary. |
| R4 | Event storm on bulk mutations | One event per request; 250 ms client debounce; hidden-tab suppression. |
| R5 | HTMX breaks no-JS fallback | Every conversion has plain GET/POST test and view branch. |
| R6 | Shared cases leak into share holder org room | Explicit owner-org-only decision and test. |
| R7 | superAdmin has no org context | OQ-1 default documented and audited; no silent cross-org fan-out. |
| R8 | Search indexes add write amplification | Benchmark gate; per-field indexes only for breaching entity; before/after plans recorded. |
| R9 | Benchmark seed collides with real data | Deterministic marker + dedicated benchmark org; idempotent count checks. |
| R10 | Partial endpoints drift from full-page auth | Reuse the exact same queryset builders and scoping helpers. |

---

## 12. Open questions / defaults

Implementation may proceed with these defaults unless the user overrides them:

1. **OQ-1 -- superAdmin org room access.** Default: allow any existing org room but write a cross-org `AuditLog` row on connect. Alternative: restrict to their own org only; this would make superAdmin dashboards less live.
2. **OQ-2 -- presence identity.** Default: unique users via ephemeral HMAC identifiers, count only. Alternative: connection count, simpler but wrong for multiple tabs.
3. **OQ-3 -- HTMX breadth.** Default: three primary lists + General/Tasks/Observables case-detail mutations. Settings/taxonomy/playbooks stay plain.
4. **OQ-4 -- search threshold.** Default: 500 ms p95, same as §12.1-fu1, configurable via `--threshold-ms`.
5. **OQ-5 -- search index shape.** Default: per-field trigram indexes preserving current SQL. Alternative: rewrite search to a concatenated expression/tsvector, which would be a semantic deviation.
6. **OQ-6 -- shared-case org events.** Default: publish only to `case.owner_org_id`; shares are not live-notified across orgs.

---

## 13. Handoff plan after approval

Two implementation briefs will be produced:

1. **Backend/data brief** -- benchmark command, benchmark helpers, conditional migrations, org consumer/publisher, presence store, event emitters, tests.
2. **Frontend brief** -- live.js refactor, list partials/templates, dashboard fragments, case detail presence/HTMX mutations, CSP/no-JS tests.

Suggested agent split:

- `django-backend`: Phase 1 + Phase 2 + Phase 3 server side.
- `qa-tester`: benchmark assertions, realtime/auth matrices, HTMX fallback tests.
- `django-frontend`: Phase 3 UI + Phase 4.
- `security-auditor`: org room/presence privacy/HTMX CSRF review before closeout.
- `verifier`: AC-by-AC check against this plan before tracker closure.
