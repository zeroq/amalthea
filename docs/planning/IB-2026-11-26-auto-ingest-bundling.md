# Implementation Brief — 2026-11-26 auto ingest-time bundling (12.3-fu1)

**Plan:** [`PLAN-2026-11-26-auto-ingest-bundling.md`](./PLAN-2026-11-26-auto-ingest-bundling.md)
**Status:** Approved by user 2026-11-26.
**Scope:** implement §12.3-fu1 only. Do not close `TODO.md` until verification is green.

## Global constraints

- Do not commit or push.
- Do not run the full `pytest` suite unless explicitly asked; full suite is user-owned.
- Never put secrets, `.env` contents, raw payloads, or exception text in code/tests/docs/audit details/API warnings.
- Dark-theme, semantic HTML, no inline JS, no native `confirm()`/`alert()`.
- Progress by section/task name in final report, including any deviation.

---

## 1. Backend implementation brief (`django-backend`)

### Files

- `cases/models.py`
- `cases/migrations/0029_correlation_rule.py`
- `cases/correlation.py`
- `alerts/escalation.py` or `alerts/models.py` only if actor typing compatibility requires it
- `ingest/views.py`
- `docs/spec/data-model.md`
- `docs/spec/api.md`
- `docs/spec/deviations.md`
- `tests/conformance/test_auto_ingest_bundling.py` (new)
- Existing focused suites:
  - `tests/conformance/test_correlation_key_derivation.py`
  - `tests/conformance/test_correlation_similar_cases.py`
  - `tests/conformance/test_indexes.py`
  - `tests/conformance/test_ingest_pipeline.py`
  - `tests/conformance/test_ingest_alert_observables.py`
  - `tests/conformance/test_webhook_hardening.py`
  - `tests/conformance/test_mvp_loop.py`
  - `tests/conformance/test_mvp_loop_automation.py`

### Tasks

1. **P1 — Model/migration**
   - Add `CorrelationRule` exactly per plan §5.1.
   - Defaults: disabled, `correlation_key`, 10 minutes.
   - Constraints: valid match mode, window 1..60, one row per org.
   - Update `docs/spec/data-model.md`.

2. **P3 — Service**
   - Add constants/dataclass/functions per plan §6.1.
   - Implement correlation-key seek per §6.2; must drive from `Alert` and use `alert_corrkey_date_idx`.
   - Implement shared-observable seek per §6.3 using linked `AlertObservable` rows; no raw payload re-extraction.
   - Reuse `PICKER_PAGE_SIZE` and `MAX_CORRELATION_OBS`; no new numeric cap constant.
   - Enforce all skips/failures and reason strings in §6.1/§6.4.
   - Atomic bundle: transaction, re-fetch with `select_for_update()` where supported, re-check closed/cross-org/already linked, then `merge_alert_into_case()` + `record_alert_bundled()`.
   - Update `record_alert_bundled()` actor typing to `User | None` without introducing a direct `TimelineEvent.objects.create` call site.

3. **P4 — Ingest integration**
   - Call service after `store_ingested_alert()` in `ingest/views.py`.
   - Only evaluate when `stored.created=True`; pass `token.organisation_id`.
   - Keep existing status codes and fields.
   - Add structured `auto_bundle` response per plan §7.1.
   - Add generic warning lines for skipped/failed; never expose exception text/raw payload.
   - Update `docs/spec/api.md`.

4. **P5 — Backend tests**
   - Add `tests/conformance/test_auto_ingest_bundling.py`.
   - Cover plan AC-P5.1 through AC-P5.6.
   - Explicitly assert:
     - disabled by default / source flag kill switch
     - in-window, out-of-window, blank key
     - closed and cross-org/shared case exclusion
     - replay never bundles
     - deterministic tie-break and cap
     - shared observable path and `ignore_similarity`
     - exactly one `correlation-bundle`, one `case.alert_bundled`, one `alert-merged`
     - case room event and org refresh
     - no partial rows on refusal
     - no direct ledger writer check remains green

### Backend acceptance criteria

- Plan AC-P1.1, AC-P1.2, AC-P3.1–AC-P3.5, AC-P4.1–AC-P4.4, AC-P5.1–AC-P5.6.
- Focused backend suites pass.
- `make check` passes.

---

## 2. Frontend implementation brief (`django-frontend`)

### Files

- `ui/views/correlation.py` (new)
- `ui/views/__init__.py`
- `ui/urls.py`
- `ui/templates/ui/settings_correlation.html` (new)
- `ui/templates/ui/_settings_nav.html`
- `ui/templates/ui/source_detail.html`
- `ui/templates/ui/sources_list.html`
- `tests/conformance/test_auto_ingest_bundling_settings.py` (new)

### Tasks

1. **P2 — Settings UI**
   - Add routes/views/template per plan §8.1.
   - Mirror `ui/views/autonomy.py` authorization:
     - admin: own org only
     - superAdmin: explicit org from URL, audited as break-glass
     - analyst/viewer/cross-org admin: 404
   - Lazy-create a disabled rule with `get_or_create`.
   - Form fields: `enabled`, `match_mode`, `window_minutes`.
   - Server-side validation 1..60; invalid input writes nothing.
   - Warning banner when enabled.

2. **Audit config changes**
   - `AuditLog(action="case.correlation_rule_changed")` on actual changes only.
   - Details contain old/new `enabled`, `match_mode`, `window_minutes`; no secrets/payload.

3. **Docs/help text**
   - Update source pages to say `correlation_enabled` is the per-feed kill switch for automatic bundling.

4. **UI tests**
   - Cover defaults, valid update, invalid window, role/org guards, no-op no-audit, change-audit, help text.

### Frontend acceptance criteria

- Plan AC-P2.1, AC-P2.2, AC-P2.3.
- New UI tests pass.
- Existing settings/source UI suites stay green.

---

## 3. Database benchmark handoff (`db-postgres`)

After backend lands:

- Add `cases/management/commands/benchmark_auto_bundle_candidates.py` per plan P6.
- Refuse non-Postgres settings.
- Seed deterministic alerts/cases/observables including hot/rare/cold keys, closed/cross-org cases, duplicates, boundary timestamps, shared observable mode.
- Reuse `core.db_benchmark` helpers and JSON schema.
- Capture `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`.
- Run if local Postgres is available; otherwise report the exact command and blocker.
- Write `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling-performance.md` only with real evidence.

Acceptance: AC-P6.1–AC-P6.4.

---

## 4. Verification handoff

After implementation and focused tests:

1. `qa-tester` — end-to-end loop and regression coverage.
2. `db-postgres` — benchmark and index proof if not already complete.
3. `verifier` — grade every AC in the plan; write `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling.md`.
4. `security-auditor` — tenant isolation, token-derived org, closed/cross-org refusal, caps, audit secrecy, config authorization; write `docs/reviews/REVIEW-2026-11-26-auto-ingest-bundling.md`.
5. Resolve findings; update `docs/spec/deviations.md` for any deviation.
6. Do not update `TODO.md`/`COMPLETED.md` until all gates are green.
