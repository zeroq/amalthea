# Verification Report: PLAN-2026-10-10 — Tenant Isolation (P1 + P2)

Plan: `docs/planning/PLAN-2026-10-10-tenant-isolation.md` (§6, §6b, §7, §9 P1/P2, §12, §15)
ADR: `docs/decisions/ADR-004-tenant-isolation.md`
Verified: 2026-10-10 (working tree, branch `main` @ `4c4c5f8`, **uncommitted**)
Scope: Phases **P1** (foundations) and **P2** (owner_org population), ACs **T1-a…T1-d**, **T2-a…T2-d**, plus the structural guards the change touches (H3-2 seed rollback graph, FK audit, index audit).

**Verdict: PASS (with coverage gaps).** All eight P1/P2 acceptance criteria are implemented and conform to the plan; every trust-boundary behaviour (uniform 404, fail-closed, token-required ingest) is present and exercised. `make check` is green. Four ACs (T1-c, T1-d, T2-a, T2-d) and two sub-cases of T2-c are **implemented but not directly asserted by a test**; none of these is a functional defect. No unrecorded *behavioural* deviation from the plan was found.

## Summary

- Total ACs: 8
- Met: 5 (T1-a, T1-b, T2-a, T2-b, T2-c) — several with named coverage gaps
- Not Met: 0
- Deviated: 0 behavioural; 3 implementation quirks (one unrecorded) noted below
- Automated gate: `make check` → **1031 passed, 3 skipped, 0 failed** (ruff check / ruff format --check / mypy / `manage.py check` / `makemigrations --check` / pytest) — re-run by verifier.

## Criterion-by-Criterion

### T1-a: scoping truth table unit-tested; cross-tenant ≡ non-existent uniform 404
**Status: Met (one equivalence sub-case unaudited)**

Evidence — `tests/conformance/test_tenant_scoping.py` covers all six truth-table rows:
- own org — `TestCaseVisibleQ::test_own_org_visible` (:108), `TestResolveCase::test_own_org_resolves` (:198)
- shared read — `test_shared_read_visible` (:122), `test_shared_read_resolves_for_get` (:211) (`resolve_case(..., write=True) is None` at :221)
- shared write — `test_shared_write_visible` (:136), `test_shared_write_resolves_for_mutate` (:223)
- superAdmin — `test_superadmin_sees_all` (:95), `test_superadmin_resolves_any` (:185)
- org-less — `TestOrgFiltered::test_orgless_denied` (:291), `test_orgless_denied` (:152/:237)
- cross-tenant — `test_cross_tenant_denied` (:169), `test_cross_tenant_denied` (:252)

Mechanism is the single module `identity/scoping.py`: `case_visible_q` (:47), `resolve_case` (:102), `org_filtered` (:81), `org_q` (:66), plus direct-org resolvers `resolve_alert` (:143) / `resolve_observable` (:163). Fail-closed is explicit: org-less/unauth → `Q(pk__in=[])` / `.none()` / `None` (:62, :77, :125, :154, :174).

HTTP uniform-404: `test_retrieve_case_cross_tenant_404` asserts cross-tenant GET → `404` + `resp.json()["type"] == "NotFoundError"` (:346-348). Non-existent resolves to the same code path (`resolve_case` returns `None`, :120/:129/:196/:209) so the response is identical.

**Gap (minor):** no test asserts the cross-tenant and non-existent HTTP responses are *byte-identical* against the same endpoint. A test that issues both GETs to `/api/v1/case/{id}` (one cross-tenant, one random UUID) and asserts equal status + equal body would close it. `resolve_case` guarantees it by construction. (WS `4404` is P5, not in scope here.)

### T1-b: existing full suite stays green
**Status: Met**

`make check` re-run by verifier: ruff check "All checks passed"; mypy "Success: no issues found in 102 source files"; `manage.py check` "no issues"; `makemigrations --check` "No changes detected"; pytest **1031 passed, 3 skipped in 54.97s**.

### T1-c: tenant migration reversible; default org created once
**Status: Met (unaudited — no test executes a reverse or double-run)**

Migrations (per-app, as recorded in §15):
- `identity/0004_tenant_isolation_foundations` — role choices + CHECK (:17-28), `AuditLog` (:29-46), `IngestionToken` (:47-65)
- `identity/0005_tenant_indexes` — drops the three redundant implicit FK indexes (:16-50)
- `identity/0006_create_default_org` — `get_or_create(name=AMALTHEA_DEFAULT_ORG_NAME)` + legacy-secret adoption (:10-30)
- `cases/0015_tenant_owner_org`, `alerts/0013_tenant_owner_org`, `observables/0007_tenant_isolation`

Reversibility is by construction: every `RunPython(..., migrations.RunPython.noop)` (identity/0006:40, cases/0015:28, alerts/0013:27, observables/0007:45) and every `AlterField`/`AddField`/`RemoveConstraint`/`AddConstraint`/`AddIndex` is individually reversible. "Created once" is guaranteed by `Organisation.objects.get_or_create(name=name)` (`identity/migrations/0006:16`); the per-app backfills use `filter(name=name).first() or first()` (cases/0015:15, alerts/0013:14, observables/0007:17).

**Gaps:** (1) no `MigrationExecutor` plan test reverses `identity/0006`, `cases/0015`, `alerts/0013`, or `observables/0007`; (2) no test asserts `identity/0006` is idempotent (a second `migrate` does not duplicate the default org). A parametrised reverse-plan test (analogous to `test_seed_migrations.py::test_h3_2_...`) plus a "run the data function twice, count == 1" test would close both. Also note: **reversing `observables/0007` re-adds the global `uniq_obs_dtype_hash`**, which would fail if two orgs already hold the same value — acceptable on greenfield, but relevant if P3+ ships before prod data is reset.

### T1-d: role choices validated (model choices + DB CHECK + permission classes)
**Status: Met (partial — permission classes untested and unwired)**

- Model choices/default: `core/enums.py:112` `USER_ROLE_CHOICES = superAdmin|admin|analyst|read-only`; `identity/models.py:44-48` `role = CharField(choices=…, default="analyst", blank=False)`.
- DB CHECK: `identity/models.py:61` `in_values("identity_user_role_valid", "role", USER_ROLE_VALUES)`; migration `identity/0004:22-28`. Tested: `tests/conformance/test_enum_contracts.py:70` maps `("identity_user","role") -> "identity_user_role_valid"` and `:333` grades the domain with bad values `("invalid","superuser","")` via `test_h2_every_checked_enum_domain_is_refused_by_the_database`.
- Permission classes: `identity/permissions.py` defines `IsSuperAdmin` (:15), `IsOrgAdminOrAbove` (:33), `IsAuthenticatedAndOrgMember` (:50).

**Gap:** `identity/permissions.py` is imported by **no** view and covered by **no** test (`rg` finds only the definition and the plan/ADR prose). The plan's P1 brief lists the classes but defers their wiring; a unit test over `has_permission` for each class (superAdmin/admin/analyst/read-only/org-less/anonymous) would close T1-d's parenthetical.

### T2-a: alerts/webhooks stamped with the correct `owner_org`
**Status: Met (partial — API alert-create not directly asserted)**

- Webhook: `ingest/views.py:289` `owner_org=token.organisation`; asserted by `test_tenant_scoping.py::TestWebhookTokenRouting::test_webhook_with_valid_token_stamps_org` (`alert.owner_org_id == org1.id`, :523).
- API case create: `cases/views.py:290` `owner_org=org` (403 if the caller is org-less, :274-284); asserted by `test_create_case_stamps_owner_org` (`case.owner_org_id == org1.id`, :400).
- API alert create: `alerts/views.py:290` `owner_org=org` (403 if org-less, :264-275).

**Gap:** no test POSTs `/api/v1/alert` and asserts the created alert's `owner_org_id`. The path is implemented and would 500 on `NOT NULL` if unstamped, but a one-line assertion in `test_t2_p1_surface.py`/`test_tenant_scoping.py` would close it.

### T2-b: escalate-to-case and observable-creation stamp `owner_org`
**Status: Met (partial — no direct org assertion on either path)**

- Escalation: `alerts/escalation.py:94` `import_alert_to_case(..., owner_org=alert.owner_org)`. Exercised by `tests/conformance/test_mvp_loop.py::test_mvp_loop_ingest_escalate_extract` (:109-145) — the escalation succeeds and the case is then readable, which the `NOT NULL` column requires.
- Extraction: `observables/extractor.py:324` `get_or_create(owner_org=org, …)` where `org = case.owner_org` (:315); `add_observable` passes `owner_org=case.owner_org` (:393).
- Alert-observable link: `alerts/views.py:193` `owner_org=alert.owner_org`.
- Per-org dedupe: `test_observable_same_value_different_orgs_are_distinct` (:471) and `test_phase3_schema.py::test_observable_uniqueness_is_bounded` assert the `(owner_org, data_type, data_hash)` key (:210).

**Gap:** no test asserts `escalated_case.owner_org_id == alert.owner_org_id`, nor `extracted_observable.owner_org_id == case.owner_org_id`. Both are enforced by `NOT NULL` + working flow, but the equality is not pinned. Add assertions in `test_mvp_loop.py` (escalate) and `test_ui_loop.py` around the `add_observable` uses (:534-540).

### T2-c: webhook requires a valid per-org `IngestionToken`; tokenless/invalid → 401; one source routes to many tenants
**Status: Met (partial — the "same source → different tenants" sub-case is untested)**

Implementation: `ingest/views.py::_authenticate_token` (:114-152) loads non-revoked tokens for the source, denies `401 unauthorized` when none (:126), when no token is presented (:137), and when no prefix matches / hash fails (:152); `verify()` rejects revoked tokens (`identity/models.py:182-186`). Auth runs **before** throttling (`ingest/views.py:243-246`).

- tokenless → 401: `test_tenant_scoping.py::test_webhook_without_token_is_401` (:525); `test_webhook_hardening.py::test_a_missing_token_is_401` (:161); `test_authz.py::test_webhook_and_session_endpoints_are_reachable_anonymously` (no-token branch, :269).
- invalid → 401: `test_webhook_with_invalid_token_is_401` (:538); `test_an_invalid_token_is_401` (:166); `test_the_secret_is_checked_before_the_rate_budget_is_spent` (10×401 before the real one is accepted, :500).
- **same `IngestionSource` routing to different tenants by token: not tested.** No test creates two tokens (different orgs) on one source and asserts the two alerts carry the two orgs. The capability exists (`IngestionToken.organisation` + prefix match), so this is a coverage gap, not a defect. A test creating `org1`/`org2` tokens on one source and posting with each, asserting `owner_org_id`, would close it.

Also note: no test covers a **revoked** token → 401, though the code path exists (`revoked_at__isnull=True` filter + `verify`).

### T2-d: legacy `IngestionSource.webhook_secret_hash` adopted as an `IngestionToken`
**Status: Met (partial — the migration itself is untested)**

Implementation: `identity/migrations/0006_create_default_org` (:18-30) adopts every source with a non-empty `webhook_secret_hash` as a default-org `IngestionToken`, preserving the raw value in `secret_hash`; `_verify_secret` (`ingest/views.py:99-112`, `identity/models.py:108-117`) falls back to a constant-time compare for non-Argon2/plaintext values.

**Gaps:** the two tests that claim this AC (`test_tenant_scoping.py::test_legacy_webhook_secret_becomes_token` :553 and `test_webhook_hardening.py::test_a_legacy_plaintext_secret_still_verifies` :180) **manually create** the `IngestionToken` themselves and only prove the runtime plaintext-verification path — neither seeds a legacy `webhook_secret_hash` *before* migrating and asserts the token exists afterward. A migration test that creates a legacy source, runs `identity/0006` forward, and asserts one `IngestionToken(organisation=default_org)` is produced would close it. The `test_tenant_scoping` docstring also cites the wrong migration ("identity.0005", should be `0006`).

## Structural-risk areas touched by the change

- **H3-2 seed rollback graph — intact.** New migrations add no dependency on another app's *seed*: `cases/0015`→`identity/0006`, `alerts/0013`→`identity/0006`, `observables/0007`→`identity/0006`; `identity/0006` depends only on `identity/0005`. All `test_seed_migrations.py` H3-2 plan tests pass (`test_h3_2_rolling_back_a_seed_unapplies_nothing_in_another_app`, `test_h3_2_no_migration_depends_on_another_apps_seed`). The §15 deviation from the plan's single `identity` backfill to per-app migrations is the correct fix for exactly this hazard and is **accepted**.
- **FK audit (`tests/conformance/test_fk_audit.py`) — matches reality.** `EXPECTED_ON_DELETE` pins `Case/Alert/Observable.owner_org → PROTECT` (:106, :157, :169), `IngestionToken.organisation → PROTECT`, `.source → CASCADE` (:183-184), `AuditLog.actor/actor_org/target_org → SET_NULL` (:186-188), `User.org → SET_NULL` (:177). Inventory tripwire bumped `51 → 57` = +6 (IngestionToken×2, AuditLog×3, Observable.owner_org×1) (:241-245). CASCADE surface re-reviewed and closed (:293-331). Behavioural PROTECT tests delete against a live org (:567-600). All pass.
- **Index audit (`tests/conformance/test_indexes.py`) — matches reality.** New `obs_org_created_idx (owner_org, -created_at)` is verified live by `test_every_declared_index_exists_in_the_database`; `Observable.data_type` regains `db_index=True` and is verified live; the three dropped implicit FK indexes in `identity/0005` are the ones `test_no_index_is_left_prefix_covered_by_a_wider_one` and `test_every_unindexed_fk_is_a_left_prefix_of_a_composite_index` would flag. All pass. `observables/0007` swaps `uniq_obs_dtype_hash → uniq_obs_org_dtype_hash` (:30-33, :70-75), confirmed by `test_phase3_schema.py::test_observable_uniqueness_is_bounded` (:210) and `test_schema_mutation_guards.py` MUTATION-5 (:180-196).
- **Secret hygiene:** the eight stray `print("DEBUG: …")` lines are gone (`rg` finds no `print(`/`DEBUG` in `ingest/views.py`); tokens are stored Argon2-hashed (`IngestionToken.secret_hash`); plaintext is returned once by `create_token` only. Note the legacy adoption copies the pre-existing raw column value into `secret_hash` (plaintext for pre-hash rows) — necessary for the fallback, but not a *hash*, so §10's "token hashes only" is satisfied in spirit, not literally, for migrated rows.

## Deviations

Recorded in §15 (judged **acceptable** against the ACs):
1. **Per-app migrations instead of one `identity` backfill.** Required to keep seed rollbacks app-local (H3-2). Acceptable; no AC depends on a single migration.
2. **`Observable.data_type` regains its own index.** Correct consequence of the constraint swap. Acceptable.
3. **Removed stray DEBUG prints.** Secret-hygiene fix. Acceptable.
4. **Explicit `User.Meta.verbose_name`.** Prevents a cosmetic `AlterModelOptions`. Acceptable.

**Not recorded in §15 (flagged):**
- **`cases/views.py::_case_org_scope` returns `.query.where`** (a `WhereNode`, not a `Q`, despite the `-> Q` annotation and `# type: ignore[return-value]`, `cases/views.py:150-158`). It is injected via `.filter(...)` at :499 and :820 and works **only because both call sites carry `select_related("case")`**, which happens to alias the case table so the embedded `case_record.owner_org_id` column resolves. Org-less callers fail closed (`.none()` → `NothingNode` → `0 = 1`) and cross-org/owner-org are correctly proven by `test_authz.py::test_a_foreign_org_gets_a_404_on_the_org_scoped_direct_lookups` (:284-337). **Risk: low but latent** — dropping `select_related` would make the filter raise (loud) or, worse in a refactor, silently stop constraining. Recommend re-implementing as `identity.scoping.org_q("case__owner_org", request.user)` (a real `Q`) and adding a "no `select_related`" regression test.
- **`IngestionToken`/`AuditLog` are not registered in Django admin.** `identity/admin.py` registers only `User`. ADR-004 §Consequences and plan §8 say both "must appear in Django admin" (plan §9 assigns admin hosting to P7, so this is arguably deferred rather than missed). Flag for P7.
- **`identity/permissions.py` is unused/unwired** (see T1-d).

## Blockers

None. P1/P2 meet every AC to at least a partial level; nothing fails.

## Recommendations (ordered by severity)

1. **T2-c multi-tenant routing test (highest).** Add a test with two `IngestionToken`s on one `IngestionSource` bound to two orgs; assert each token stamps its org. This is the headline Q6 capability and currently has no direct test.
2. **T2-d migration test.** Prove `identity/0006` actually adopts a pre-existing `webhook_secret_hash` (seed-before-migrate), rather than re-implementing the adoption inside the test. Fix the `0005`→`0006` docstring drift.
3. **T1-c reversibility/idempotency.** Add reverse-plan (or executed, SQLite-only) tests for the four tenant migrations and a "run the default-org function twice → 1 org" test.
4. **T2-a/T2-b direct assertions.** Pin `alert.owner_org_id` on API alert-create and `case/observable.owner_org_id` on escalate/extract (one line each).
5. **T1-d permission-class unit tests** for `IsSuperAdmin` / `IsOrgAdminOrAbove` / `IsAuthenticatedAndOrgMember`, even before they are wired in.
6. **Refactor `_case_org_scope`** off `.query.where` onto `org_q("case__owner_org", user)`; add a regression test without `select_related`.
7. **Uniform-404 equivalence test** asserting cross-tenant and non-existent HTTP bodies are identical.
8. **Register `IngestionToken`/`AuditLog` in admin** (P7) and add a revoked-token → 401 test.

## Is P1/P2 safe to build P3 on?

**Yes.** The foundations are solid and greppable: one scoping module, uniform 404, fail-closed org-less handling, mandatory `NOT NULL`+`PROTECT` `owner_org`, per-org observable uniqueness, and a single token-routed ingest path with auth-before-throttle. The two structural guardrails that could have silently rotted (H3-2 seed-rollback graph; FK/index audits) were updated to match the new reality and pass. The gaps above are all *test-coverage* gaps, not behavioural defects, so P3 (read enforcement) can proceed in parallel with closing them. The one caution for P3: P3's route-enumeration conformance test (`T3-c`) should also assert the cross-tenant ≡ non-existent equivalence mechanically, and the `_case_org_scope` quirk should be refactored before more call sites adopt it.
