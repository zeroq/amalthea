# PLAN-2026-11-26 — Automatic ingest-time alert bundling (12.3-fu1)

**Status:** Approved — implementation handoff authorized 2026-11-26
**Planner:** planner
**Date:** 2026-11-26
**Tracker item:** `TODO.md` §12.3-fu1, priority row 1
**Supersedes / extends:** deferred P3 in `docs/planning/PLAN-2026-10-18-correlation-similar-cases.md` §5.3/§11

## 1. Summary

Implement the deferred automatic ingest-time bundling path: a per-organisation, disabled-by-default
`CorrelationRule` decides whether a newly ingested alert is synchronously attached to an existing open
case. The default rule matches an alert's `correlation_key` to another alert's key within a bounded
window using `alert_corrkey_date_idx`; an optional mode matches shared observables already linked to
the alert. Every automatic bundle must be explainable: it writes the existing `alert-merged` ledger
event, a dedicated `correlation-bundle` ledger event naming the signal/window, and one
`case.alert_bundled` audit row. It must never attach across organisations, into a closed case, or from
a replayed webhook delivery.

## 2. Goals

1. Add a minimal per-org `CorrelationRule` with `enabled=False`, `window_minutes=10`, and a
   configurable match mode defaulting to `correlation_key`.
2. Provide an admin-only settings UI so the rule is configurable and therefore not an inert model.
3. Evaluate the rule synchronously in `POST /api/v1/alerts/webhook/` for newly created alerts only.
4. Use the existing `alert_corrkey_date_idx` seek for the default correlation-key mode.
5. Support the pre-settled shared-observable mode with existing `alertobs_obs_alert_idx` /
   `caseobs_obs_case_idx` indexes and existing hard caps.
6. Reuse `record_alert_bundled()` so each automatic bundle emits exactly one `correlation-bundle`
   ledger row and one `case.alert_bundled` audit row.
7. Keep ingest fail-soft for rule-level refusals: the alert remains stored and the response explains
   why bundling was skipped or failed, without exposing raw payload or exception text.
8. Prove the candidate query with a Postgres benchmark and `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`.
9. Update docs, verification/review artifacts, `TODO.md`, and `COMPLETED.md` only after verification.

## 3. Non-goals

- No automatic bundling from SSDEEP fuzzy similarity (`12.12` remains separate).
- No automatic case-to-case merge.
- No case creation from this feature; ingest-time bundling only attaches to an existing case.
- No new playbook trigger and no `AutomationRun` changes.
- No public API endpoint for reading or editing `CorrelationRule`; configuration is UI-only.
- No new global/header search or UI surface outside Settings → Correlation.
- No new database index unless the Postgres benchmark shows a breach or a non-seek plan.
- No default enablement: all organisations start disabled.
- No commit or push unless explicitly requested after verification.

## 4. Architecture and binding decisions

### 4.1 Stack and ADR alignment

- Django 5.x + DRF-style JSON responses, Celery/Redis for asynchronous work, Django Channels for live
  updates. This feature is deliberately synchronous because the pre-settled Q5a decision requires
  bounded evaluation inside the ingest request.
- `ADR-004-tenant-isolation.md`: organisation is mandatory, token-routed, and never taken from the
  payload. Shared cases are sanctioned **read** surfaces but are not valid auto-bundle targets.
- `ADR-008-webhook-routing.md`: the token identifies source and organisation; the webhook URL carries
  no source slug.
- `ADR-009 / ingest.models.IngestionSource`: sources are org-owned mapping profiles.
- `AGENTS.md` §2 Modules B/C/D: this is the missing automatic half of alert-to-case correlation and
  must remain explainable in the ledger.

### 4.2 Pre-settled decisions carried forward

| ID | Settled decision | Implementation effect |
|---|---|---|
| Q1b | Previous wave shipped Similar tab + manual audited attach; auto-bundling deferred. | This plan is the deferred wave. |
| Q3c | Rule matches `correlation_key` **or** shared observable; configurable; default `correlation_key`. | `match_mode` choices: `correlation_key`, `shared_observable`; default `correlation_key`. No third "both" mode. |
| Q4a | Minimal per-org `CorrelationRule`, `enabled=False`, `window_minutes=10`, capped. | One rule per org, disabled by default, window capped 1..60, candidate cap reuses `PICKER_PAGE_SIZE` (200). |
| Q5a | Synchronous, bounded, behind `enabled=False`; seek uses `alert_corrkey_date_idx`. | Evaluate in webhook view after storage; DB-side `LIMIT`; default query drives from `Alert`. |
| Q8b | Ledger kind `correlation-bundle`; audit action `case.alert_bundled`. | Reuse `record_alert_bundled()` unchanged except actor typing. |
| D2 | No named recorder in `identity/audit.py` for case-owned attach paths. | Use direct `AuditLog.objects.create` idiom already in `cases/correlation.py`. |

### 4.3 Planner decisions resolving implementation ambiguities

These are planner defaults and are the main items to approve with this plan.

| ID | Decision | Rationale |
|---|---|---|
| P3-D1 | Both gates apply: `IngestionSource.correlation_enabled` is a per-feed kill switch and `CorrelationRule.enabled` is the per-org master switch. | The existing source flag is documented/displayed but inert; making it authoritative avoids a misleading UI and preserves older ADR language without weakening Q4a. |
| P3-D2 | The window anchor is server time (`timezone.now()`), not payload `date`. | Payload timestamps are attacker-controlled; this prevents shifting the window with forged dates. |
| P3-D3 | The system actor is `None`; do not invent a service user. | `AuditLog.actor` is nullable; `record_alert_bundled()` already tolerates `actor=None` at runtime. Update its type hint to `User | None`. |
| P3-D4 | Known rule refusals are non-fatal to ingest: store the alert, return `201`/`200`, add a structured `auto_bundle` status and a generic warning. Unexpected exceptions are caught, logged with `logger.exception`, and produce a generic warning; the bundle transaction rolls back. | Ingest availability should not depend on an heuristics side-effect; "never silent" is satisfied by a response warning for skips/failures and ledger/audit rows for successful bundles. |
| P3-D5 | Replays (`stored.created=False`) never auto-bundle. | Duplicate delivery must not become a silent mutation trigger. |
| P3-D6 | Candidate cases must have `owner_org_id == alert.owner_org_id` and be open by both `closed_date IS NULL` and `status.stage in ("New", "InProgress")`. | `record_alert_bundled()` refuses closed/cross-org; filter before selection so the request is not driven into predictable refusals. |
| P3-D7 | Do not use `case_visible_q()` for auto-bundle selection. | It includes shared cases; auto-bundling must not write into another org's case. |
| P3-D8 | Correlation-key mode picks newest correlated alert (`-date`, `-id`), then first distinct case, bounded by `PICKER_PAGE_SIZE`. Shared-observable mode ranks by shared count desc, newest matched `CaseObservable.created_at`, then `case.number`. | Explainable and deterministic; no opaque scoring. |
| P3-D9 | No shadow mode. | Disabled-by-default plus mandatory ledger/audit is the agreed safety model; shadow mode would be scope creep. |
| P3-D10 | Rule configuration changes write `AuditLog(action="case.correlation_rule_changed")` with old/new values only. | Enabling automatic case mutation is security-relevant and should be auditable. |

## 5. Data model and migrations

### 5.1 New model: `cases.CorrelationRule`

Add to `cases/models.py`; migration `cases/migrations/0029_correlation_rule.py`.

```python
class CorrelationRule(UUIDModel, TimeStampedModel):
    organisation = models.OneToOneField(
        "identity.Organisation",
        on_delete=models.CASCADE,
        related_name="correlation_rule",
    )
    enabled = models.BooleanField(default=False)
    match_mode = models.CharField(
        max_length=32,
        choices=[
            ("correlation_key", "Correlation key"),
            ("shared_observable", "Shared observable"),
        ],
        default="correlation_key",
    )
    window_minutes = models.PositiveSmallIntegerField(default=10)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="correlation_rule_updates",
    )

    class Meta:
        db_table = "case_correlation_rule"
        constraints = [
            in_values(
                "correlation_rule_match_mode_valid",
                "match_mode",
                ("correlation_key", "shared_observable"),
            ),
            in_range("correlation_rule_window_range", "window_minutes", 1, 60),
        ]
```

Notes:

- One row per org via `OneToOneField`.
- Ingest reads the rule with `.filter(organisation_id=org_id).first()`; missing row means disabled.
- Settings lazily creates a disabled row with `get_or_create`, mirroring `McpOrgPolicy`.
- No new index is needed for the rule table; the one-to-one unique constraint is sufficient.
- No changes to `Alert`, `Case`, `AlertObservable`, or `CaseObservable` schema.

### 5.2 Existing indexes used

- `alert_corrkey_date_idx` on `Alert(correlation_key, date)` — default candidate seek.
- `alertobs_obs_alert_idx` on `AlertObservable(observable, alert)` — fetch the new alert's linked artifacts.
- `caseobs_obs_case_idx` on `CaseObservable(observable, case)` — shared-observable mode.
- Existing PK/FK lookups for `Case` and `CaseStatus`.

Do not add an index unless `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling-performance.md`
shows a p95 breach or a plan without the intended index. If needed, evaluate
`(owner_org_id, correlation_key, -date, -id)` as a separate, evidence-backed deviation.

## 6. Service design

### 6.1 New service surface in `cases/correlation.py`

Add:

```python
AUTO_BUNDLE_SOURCE = "ingest:auto-bundle"
AUTO_BUNDLE_WINDOW_MAX_MINUTES = 60


@dataclass(frozen=True)
class AutoBundleDecision:
    status: str  # "bundled" | "skipped" | "failed"
    reason: str
    mode: str = ""
    case_id: UUID | None = None
    window_minutes: int | None = None
    signal: str = ""


def find_auto_bundle_case(
    alert: Alert,
    *,
    rule: CorrelationRule,
    org_id: UUID,
) -> Case | None: ...


def auto_bundle_ingested_alert(
    alert: Alert,
    *,
    source: IngestionSource,
    org_id: UUID,
    created: bool,
) -> AutoBundleDecision: ...
```

Implementation contract:

1. Skip when `created=False` (`reason="replay"`).
2. Skip when `alert.case_id` is already set (`reason="already_linked"`).
3. Skip when `alert.owner_org_id != org_id` (`reason="tenant_mismatch"`); this is a defensive invariant check.
4. Load `CorrelationRule` for `org_id`; skip if missing or disabled (`reason="rule_disabled"`).
5. Skip when `source.correlation_enabled=False` (`reason="source_correlation_disabled"`).
6. Clamp `window_minutes` to 1..60 and use `PICKER_PAGE_SIZE` as the candidate cap.
7. Choose a case using the selected mode.
8. Inside one `transaction.atomic()`:
   - re-fetch the target case with `select_for_update()` where supported;
   - re-check owner, open status, and that the alert is still unlinked;
   - call `merge_alert_into_case(alert, case, actor=None)`;
   - call `record_alert_bundled(case, alert, actor=None, reason=..., source=AUTO_BUNDLE_SOURCE)`.
9. On `PermissionDenied`, `ValidationError`, or a missing target after re-check, return a skipped/failed
   decision without raising. On unexpected exceptions, log and return `failed` with a generic reason.

### 6.2 Correlation-key candidate seek

Default mode. Query shape:

```python
anchor = timezone.now()
window_start = anchor - timedelta(minutes=rule.window_minutes)

candidate_alerts = (
    Alert.objects.filter(
        owner_org_id=org_id,
        correlation_key=alert.correlation_key,
        date__gt=window_start,
        date__lte=anchor,
        case__isnull=False,
        case__owner_org_id=org_id,
        case__closed_date__isnull=True,
        case__status__stage__in=("New", "InProgress"),
    )
    .exclude(pk=alert.pk)
    .select_related("case", "case__status")
    .order_by("-date", "-id")[:PICKER_PAGE_SIZE]
)
```

Pick the first distinct `case_id`. Blank `correlation_key` skips this mode with
`reason="no_correlation_key"`.

### 6.3 Shared-observable candidate seek

Optional mode. Use already-linked `AlertObservable` rows, not a fresh raw-payload extraction:

```python
observable_ids = list(
    AlertObservable.objects.filter(alert=alert).values_list("observable_id", flat=True)[
        :MAX_CORRELATION_OBS
    ]
)

matches = (
    CaseObservable.objects.filter(
        observable_id__in=observable_ids,
        observable__ignore_similarity=False,
        case__owner_org_id=org_id,
        case__closed_date__isnull=True,
        case__status__stage__in=("New", "InProgress"),
        created_at__gt=window_start,
        created_at__lte=anchor,
    )
    .values("case_id")
    .annotate(shared_count=Count("id"), latest_match=Max("created_at"))
    .order_by("-shared_count", "-latest_match", "case_id")[:PICKER_PAGE_SIZE]
)
```

Pick the first `case_id` and fetch with `select_for_update()`. Empty observable list skips with
`reason="no_observables"`.

### 6.4 Reason strings

Reason strings are short, non-secret, and include the explainability data required by the pre-settled
rules:

- Success: `correlation_key='<key>' within <N> minutes` or
  `shared observable '<type>:<value>' within <N> minutes (count=<M>)`.
- Truncation/cap: append `; candidate cap <cap> reached`.
- Skips/failures: `replay`, `already_linked`, `rule_disabled`, `source_correlation_disabled`,
  `no_correlation_key`, `no_observables`, `no_open_candidate`, `tenant_mismatch`,
  `closed_target_recheck`, `cross_org_target_recheck`, `bundle_failed`.

Never include raw payload, secret material, or exception text in a reason returned to a client or
written to audit details.

## 7. API contract

### 7.1 Webhook response addition

`POST /api/v1/alerts/webhook/` keeps current status codes and all existing fields. Add:

```json
"auto_bundle": {
  "status": "bundled | skipped | failed",
  "reason": "short machine-readable reason",
  "mode": "correlation_key | shared_observable",
  "window_minutes": 10,
  "case_id": "uuid-or-null",
  "signal": "short non-secret signal description"
}
```

- `case_id` is a string only when `status == "bundled"`.
- `warnings` gains one generic line for `skipped`/`failed` decisions, e.g.
  `auto_bundle_skipped: rule_disabled`.
- No new endpoint is added.

Update `docs/spec/api.md` with this response field and the contract above.

### 7.2 Automation interaction

- `alert.ingested` still fires on `Alert` creation, before auto-bundle evaluation.
- Auto-bundling via `merge_alert_into_case()` copies alert observables and dispatches
  `observable.created` for new case links, so the existing feedback loop remains intact.
- Do not add `alert.bundled` as a playbook trigger in this wave.

## 8. UI/UX

### 8.1 Settings page

Add Settings → Correlation for admin/superAdmin, mirroring the Agent autonomy pattern:

- `ui/views/correlation.py`
  - `settings_correlation(request)` — resolves the org and redirects to the org-specific URL.
  - `settings_correlation_for_org(request, organisation_id)` — GET form.
  - `update_correlation_rule(request, organisation_id)` — POST update.
- Routes in `ui/urls.py`:
  - `settings/correlation` → `ui-settings-correlation`
  - `settings/correlation/<uuid:organisation_id>` → `ui-settings-correlation-org`
  - `settings/correlation/<uuid:organisation_id>/update` → `ui-settings-correlation-update`
- Template `ui/templates/ui/settings_correlation.html`, plus `ui/_settings_nav.html` entry.
- Admin may only edit their own org; superAdmin may edit any explicit org and is audited as break-glass.
- Analysts/viewers and cross-org admins receive 404, matching `ui/views/autonomy.py`.

Form controls:

- `enabled` checkbox, default unchecked.
- `match_mode` radio group or select with visible labels.
- `window_minutes` numeric input with server-side validation 1..60.
- Warning banner when enabled: automatic case mutation is active.
- Plain POST fallback; no JS or inline scripts; CSP-safe.

Update `ui/templates/ui/source_detail.html` and `sources_list.html` help text to state that
`correlation_enabled` is the per-feed kill switch for automatic bundling.

## 9. Observability and realtime

- Successful bundle writes:
  - `alert-merged` from `merge_alert_into_case()`;
  - `correlation-bundle` from `record_alert_bundled()`;
  - one `case.alert_bundled` audit row.
- `append_timeline_event()` publishes `correlation-bundle` to the case room. `alert-merged` already
  emits the org `case-updated` refresh, so no change to `reason_map` is required.
- Configuration changes write `case.correlation_rule_changed` audit rows with old/new values.
- Ingest logs one structured line: `auto_bundle status=<status> reason=<reason> org=<id> alert=<id>`.
- No raw payload or secret is logged by the new path.

## 10. Phases and acceptance criteria

### P1 — Config model and migration

| Task | Acceptance criteria |
|---|---|
| P1.1 Add `CorrelationRule` and migration. | **AC-P1.1** One row per org; defaults `enabled=False`, `match_mode="correlation_key"`, `window_minutes=10`; DB constraints reject invalid mode/window; `make check` migration check passes. |
| P1.2 Document model. | **AC-P1.2** `docs/spec/data-model.md` documents the new table, defaults, constraints, and relationship to `IngestionSource.correlation_enabled`. |

### P2 — Settings UI

| Task | Acceptance criteria |
|---|---|
| P2.1 Add views/routes/template/nav. | **AC-P2.1** Admin/superAdmin can view and update their permitted org; analysts/viewers and cross-org admins get 404; invalid `window_minutes` shows a form error and writes nothing. |
| P2.2 Audit config changes. | **AC-P2.2** Each actual change writes one `case.correlation_rule_changed` audit row with non-secret old/new values; no-op saves write no audit row. |
| P2.3 Source flag help text. | **AC-P2.3** Source pages state that `correlation_enabled` disables automatic bundling for that feed. |

### P3 — Correlation service

| Task | Acceptance criteria |
|---|---|
| P3.1 Add `AutoBundleDecision`, `find_auto_bundle_case`, `auto_bundle_ingested_alert`. | **AC-P3.1** Decisions are deterministic and non-secret; service skips before querying when replay, already linked, rule/source disabled, tenant mismatch, or missing signal. |
| P3.2 Correlation-key seek. | **AC-P3.2** Query drives from `Alert`, filters owner/org/window/open case/shared-case exclusions, orders `-date,-id`, applies DB-side `PICKER_PAGE_SIZE`, and on Postgres uses `alert_corrkey_date_idx` in the captured plan. |
| P3.3 Shared-observable seek. | **AC-P3.3** Uses `AlertObservable` links and `caseobs_obs_case_idx`, honours `MAX_CORRELATION_OBS` and `PICKER_PAGE_SIZE`, excludes `ignore_similarity`, and never crosses orgs. |
| P3.4 Atomic bundle write. | **AC-P3.4** Re-checks target under transaction, calls `merge_alert_into_case()` and `record_alert_bundled()`; closed/cross-org refusals roll back link, ledger, audit, and observable copies together. |
| P3.5 Actor typing. | **AC-P3.5** `record_alert_bundled(actor: User | None)` remains the only `TimelineEvent` writer and passes the choke-point scan. |

### P4 — Ingest integration and API response

| Task | Acceptance criteria |
|---|---|
| P4.1 Call service after `store_ingested_alert()`. | **AC-P4.1** Only newly created alerts are evaluated; token org is passed as `org_id`; payload org is never consulted; existing webhook status codes and fields are unchanged. |
| P4.2 Extend response and warnings. | **AC-P4.2** Every webhook response includes `auto_bundle`; skipped/failed decisions add one generic warning; no raw payload or exception text is exposed. |
| P4.3 Failure containment. | **AC-P4.3** A known refusal or unexpected service exception does not prevent the stored alert from being returned as accepted/replayed; the bundle transaction leaves no partial rows. |
| P4.4 Docs. | **AC-P4.4** `docs/spec/api.md` documents the response contract and skip/failure semantics. |

### P5 — Tests

| Task | Acceptance criteria |
|---|---|
| P5.1 Model/config tests. | **AC-P5.1** Defaults, constraints, one-per-org, admin role guards, cross-org 404, invalid input, and audit-on-change are covered. |
| P5.2 Correlation-key behavior. | **AC-P5.2** Tests cover in-window match, outside-window ignore, blank key, closed case, cross-org/shared case, unlinked alert, duplicate alerts, deterministic tie-break, cap, and index guard. |
| P5.3 Shared-observable behavior. | **AC-P5.3** Tests cover shared match, no observable skip, `ignore_similarity` exclusion, cross-org exclusion, cap, and stale artifact window. |
| P5.4 Bundle trail. | **AC-P5.4** A successful automatic bundle yields exactly one `correlation-bundle` event, one `case.alert_bundled` audit row, one `alert-merged` event, linked alert, and copied observables. |
| P5.5 Replay/idempotence. | **AC-P5.5** Replaying the same webhook never re-bundles; a manual retry after a refusal does not create duplicate rows. |
| P5.6 Failure/realtime. | **AC-P5.6** Known refusal returns a warning and no partial rows; case room receives the bundle event; org refresh arrives via `alert-merged`; no other org receives the event. |

### P6 — Postgres benchmark and database proof

| Task | Acceptance criteria |
|---|---|
| P6.1 Add benchmark command. | **AC-P6.1** `cases/management/commands/benchmark_auto_bundle_candidates.py` refuses non-Postgres settings, seeds deterministic alerts/cases/observables, measures the exact service query shapes, and writes `/tmp` JSON only. |
| P6.2 Plan proof. | **AC-P6.2** Correlation-key plan uses `alert_corrkey_date_idx` with no `Seq Scan` on `alert`; shared-observable plan uses `caseobs_obs_case_idx`/`alertobs_obs_alert_idx`. |
| P6.3 Threshold. | **AC-P6.3** Reuse the §12.1-fu1 rule: every measured p95 must be `<= 500 ms` with `--repeat 10`, or the feature remains disabled by default and a follow-up index/decision is required. |
| P6.4 Verification doc. | **AC-P6.4** `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling-performance.md` records environment, dataset, commands, p95 table, plan shapes, decision, and deviations. |

### P7 — Verification, security, closeout

| Task | Acceptance criteria |
|---|---|
| P7.1 Focused tests and static gates. | **AC-P7.1** Focused conformance suites pass on SQLite; `make check` passes; Postgres-focused suites pass with `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg --create-db`. |
| P7.2 Independent verifier. | **AC-P7.2** `verifier` grades every AC in this plan and writes a verification report with any deviation recorded in `docs/spec/deviations.md`. |
| P7.3 Security review. | **AC-P7.3** `security-auditor` reviews tenant isolation, token-derived org, closed/cross-org refusal, audit secrecy, caps, and config authorization; no High/Medium finding remains unresolved. |
| P7.4 QA. | **AC-P7.4** `qa-tester` confirms the end-to-end ingest → bundle → ledger → audit → realtime loop and replay behavior. |
| P7.5 Tracker closeout. | **AC-P7.5** After green verification, move §12.3-fu1 from `TODO.md` to `COMPLETED.md` with evidence and commit hash. Full suite remains a user-owned explicit gate; do not run or claim it unless requested. |

## 11. Security requirements

| ID | Requirement |
|---|---|
| S1 | `org_id` is always `token.organisation_id` / `alert.owner_org_id`; never read from payload. |
| S2 | Candidate cases are same-org only; shared cases and cross-org cases cannot be selected. |
| S3 | Closed cases are excluded by `closed_date` and `status.stage`, and re-checked after locking. |
| S4 | Ingest remains authenticated and throttled; auto-bundling runs only after successful token auth and rate checks. |
| S5 | All queries are DB-side capped (`PICKER_PAGE_SIZE`, `MAX_CORRELATION_OBS`); no unbounded payload fan-out. |
| S6 | Audit and response reasons contain no secrets, raw payload, full raw values, or exception text. |
| S7 | Rule configuration is admin/superadmin-only and org-scoped; superAdmin cross-org changes are audited. |
| S8 | Successful automatic mutation writes both ledger and audit rows in the same transaction as the link. |
| S9 | Replays cannot trigger new automatic mutations. |
| S10 | The ledger choke-point test remains green; no direct `TimelineEvent.objects.create` call site is introduced. |

## 12. Testing plan

### 12.1 Focused suites

```bash
.venv/bin/pytest -q \
  tests/conformance/test_correlation_key_derivation.py \
  tests/conformance/test_correlation_similar_cases.py \
  tests/conformance/test_indexes.py \
  tests/conformance/test_ingest_pipeline.py \
  tests/conformance/test_ingest_alert_observables.py \
  tests/conformance/test_webhook_hardening.py \
  tests/conformance/test_mvp_loop.py \
  tests/conformance/test_mvp_loop_automation.py
```

Add a dedicated suite:

```bash
.venv/bin/pytest -q tests/conformance/test_auto_ingest_bundling.py
```

### 12.2 Broader regression surface

```bash
.venv/bin/pytest -q \
  tests/conformance/test_tenant_scoping.py::TestWebhookTokenRouting \
  tests/conformance/test_alert_fuzzy_similarity.py \
  tests/conformance/test_ledger_keyset.py \
  tests/conformance/test_realtime.py \
  tests/conformance/test_realtime_org.py \
  tests/conformance/test_settings_ingestion_tokens.py \
  tests/conformance/test_settings_audit_log.py
```

### 12.3 Postgres command

```bash
docker compose up -d postgres
DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/pytest -q --create-db \
  tests/conformance/test_auto_ingest_bundling.py \
  tests/conformance/test_indexes.py \
  tests/conformance/test_correlation_similar_cases.py \
  tests/conformance/test_webhook_hardening.py \
  tests/conformance/test_mvp_loop.py
```

### 12.4 Benchmark command

```bash
DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
  .venv/bin/python manage.py benchmark_auto_bundle_candidates \
    --seed --repeat 10 --threshold-ms 500.0 \
    --output /tmp/auto-bundle-benchmark.json
```

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hot `correlation_key` causes a large candidate set. | DB-side `LIMIT PICKER_PAGE_SIZE`, 10-minute default window, benchmark hot key, no unbounded Python scan. |
| Payload date used to shift window. | Use `timezone.now()` anchor; payload date remains alert metadata only. |
| Shared case accidentally selected. | Explicit `case__owner_org_id=org_id`; do not use `case_visible_q()`; test cross-org shared case. |
| Closed target selected between query and write. | Query filters both closed markers; `select_for_update()` and `record_alert_bundled()` re-check; failure is non-fatal with warning. |
| Alert silently bundled on replay. | Only `created=True`; replay response status `skipped.replay`. |
| Webhook latency increases for every org once enabled. | Disabled by default; single indexed seek; bounded cap; benchmark gate; source kill switch. |
| Ledger/audit drift. | Reuse `merge_alert_into_case()` and `record_alert_bundled()`; choke-point test; exactly-one-row assertions. |
| Config UI becomes an accidental global switch. | Org-scoped route, admin own-org guard, superadmin audit, disabled default. |
| Existing `IngestionSource.correlation_enabled` remains confusing. | Make it authoritative as per-feed kill switch and update source help text. |
| New index needed after rollout. | Do not add speculatively; benchmark evidence first; record deviation if a new index is introduced. |

## 14. Documentation updates

- `docs/spec/data-model.md` — `CorrelationRule`.
- `docs/spec/api.md` — webhook `auto_bundle` response.
- `docs/spec/realtime.md` — note `correlation-bundle` reaches the case room and org refresh is already
  emitted by `alert-merged`.
- `docs/spec/deviations.md` — record P3-D1..P3-D10 and any implementation deviation.
- `docs/verification/VERIFY-2026-11-26-auto-ingest-bundling-performance.md` — benchmark proof.
- `TODO.md` / `COMPLETED.md` — close only after P7 verification.

## 15. Open questions

None blocking. The planner defaults in §4.3 need explicit approval with this plan:

1. Approve the dual gate (`source.correlation_enabled` plus per-org `CorrelationRule.enabled`).
2. Approve webhook response extension with `auto_bundle`.
3. Approve fail-soft ingest semantics for rule skips/failures.
4. Approve the minimal Settings UI and audit action for configuration changes.

If any is rejected, update this plan before implementation handoff.

## 16. References

- `AGENTS.md` §2 Modules B/C/D, §5 Secret Hygiene, §6 Design & Style.
- `TODO.md:22`, `TODO.md:274-287`.
- `docs/planning/PLAN-2026-10-18-correlation-similar-cases.md` §5.3, §11, §13.
- `docs/verification/VERIFY-2026-11-26-observable-count-performance.md` — 500 ms benchmark discipline.
- `docs/reviews/REVIEW-2026-10-18-correlation-similar-cases.md` — prior T-DB1 gap and S1–S5 requirements.
- `docs/decisions/ADR-004-tenant-isolation.md`, `ADR-008-webhook-routing.md`, `ADR-009` source ownership.
- `cases/correlation.py`, `cases/ledger.py`, `alerts/escalation.py`, `ingest/views.py`, `ingest/pipeline.py`.
