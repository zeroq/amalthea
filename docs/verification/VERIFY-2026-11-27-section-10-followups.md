# Verification Report: §10 open follow-ups

Plan: `docs/planning/PLAN-2026-11-27-section-10-open-followups.md`
Verified: 2026-11-27 (analysis of the uncommitted working tree)
Status: Pass

## Summary
- Total ACs: 16
- Met: 16
- Not Met: 0
- Deviated: 0 (one explicitly deferred Postgres harness noted below)

## Criterion-by-Criterion

### Phase 1 — custom-field API parity
#### AC1: API PATCH updates an existing value and emits one `custom-field-updated` event
- **Status:** Met
- **Evidence:** `test_patch_updates_an_existing_value_and_emits_one_event`; shared service in `cases/custom_fields.py`.

#### AC2: Invalid type, oversized value, foreign `_id`, and malformed item return 400 without partial writes
- **Status:** Met
- **Evidence:** `test_patch_rejects_invalid_types_without_partial_writes`, `test_patch_rejects_a_foreign_value_id`, `test_patch_rejects_malformed_custom_field_items`, `test_patch_rejects_a_non_list_custom_fields_value`; coercion guard retained from the UI implementation.

#### AC3: Re-submitting the same value emits no new event
- **Status:** Met
- **Evidence:** `test_patch_repeated_value_is_a_no_op`.

#### AC4: UI write and API write produce the same stored value and event kind
- **Status:** Met
- **Evidence:** `test_api_and_ui_writes_share_value_and_event_contract`; `ui/case_custom_fields.py` re-exports the shared service.

#### AC5: Independent verification record lists AC1–AC4 as PASS
- **Status:** Met
- **Evidence:** `docs/verification/VERIFY-2026-11-27-case-custom-field-api-parity.md`.

### Phase 2 — pre-auth webhook limiter
#### AC6: Unauthenticated requests above the limit get 429 without reaching token lookup
- **Status:** Met
- **Evidence:** `ingest/views.py::_preauth_throttled` runs before `_authenticate_token`; `test_preauth_budget_bounds_unauthenticated_floods`.

#### AC7: A legitimate sender from a different IP is unaffected by an attacker's budget
- **Status:** Met
- **Evidence:** The same test posts a valid token from a second `REMOTE_ADDR` and gets 201.

#### AC8: Pre-auth 0 disables the limiter in dev/test; prod fails closed
- **Status:** Met
- **Evidence:** `_consume_budget` skips `limit <= 0`; `amalthea/settings/prod.py` refuses non-positive `WEBHOOK_PREAUTH_RATE_IP_PER_MIN`; `test_the_pre_auth_webhook_budget_cannot_be_disabled`.

#### AC9: `docs/spec/api.md` documents the budget and separate bucket
- **Status:** Met
- **Evidence:** Ingest check-order bullet now includes `wh:preauth:ip:{ip}:{window}`, the 600/min default, and the distinct-budget rule; ADR-008 was updated from “still owed”.

### Phase 3 — token concurrency and bucket key
#### AC10: Both create and delete paths acquire `IngestionSource.objects.select_for_update()` in the same transaction that writes/deletes the token
- **Status:** Met
- **Evidence:** `ui/views/ingestion_tokens.py::_lock_source` is called inside the token-create retry transaction and inside the delete/reap transaction; `TestTokenSourceLocking` asserts one call from each path and `connection.in_atomic_block is True`.

#### AC11: SQLite-safe guard tests pin both lock call sites; a Postgres concurrency case is documented as deferred
- **Status:** Met
- **Evidence:** `TestTokenSourceLocking`; the security-review addendum and TODO closeout record SQLite’s no-op and the deferred real harness.

#### AC12: The webhook cache key never contains the raw token prefix
- **Status:** Met
- **Evidence:** `_throttled` uses `sha256(token.prefix)[:16]`; `test_the_token_throttle_key_does_not_contain_the_prefix` spies on `cache.add` keys.

#### AC13: Existing webhook throttle tests still pass with equivalent budgets
- **Status:** Met
- **Evidence:** `tests/conformance/test_webhook_hardening.py` passes with the new pre-auth bucket and hashed token key.

### Phase 4 — test-quality gaps
#### AC14: No `_mk_source` row is orphaned by the lifecycle helper
- **Status:** Met
- **Evidence:** `TestTokenActivationLifecycle._mint` now names the feed after the slug; token lifecycle suite passes.

#### AC15: Active markup includes Revoke; paused/revoked markup does not
- **Status:** Met
- **Evidence:** `test_the_status_cell_offers_the_right_control_for_each_state` now pins all three states.

#### AC16: A mid-transaction raise restores the token row
- **Status:** Met
- **Evidence:** `test_a_raise_before_source_deletion_rolls_back_the_token` patches the source-delete audit to raise and asserts the token and feed survive.

## Gates
- Focused combined suites: **156 passed in 84.69s** (`test_case_custom_fields_api`, `test_ui_custom_fields_tab`, `test_webhook_hardening`, `test_prod_settings`, `test_settings_ingestion_tokens`, `test_wire_boundary`).
- Final user-run full suite: **4511 passed, 3 skipped, 15 warnings, 2 subtests passed in 718.65s**.
  Skips are the known Postgres-only planner/case-numbering checks; warnings are the pre-existing
  naive-datetime fixtures and tracked CSS-token follow-up.
- `make check`: passed (ruff, format, mypy, Django check, migrations, CSS artifact, playbook scripts).
- `git diff --check`: clean.
- Secret scan over all changed and untracked files: clean, 59/59.

## Post-run correction
The first user-run full suite (`1 failed, 4510 passed, 3 skipped, 15 warnings, 2 subtests passed
in 698.42s`) failed only `test_no_wire_literals_outside_the_wire_boundary`: the new shared custom-field
service and case updater held exact `customFields`/`_id` literals. Decoding now lives in
`core/serializers/cases.py`; services use internal keys and an internal error-field constant that the
view remaps to the wire name. The wire-boundary test and focused §10 suites pass after correction.

## Deferred work
- A real Postgres concurrency harness for the M1 race remains in `TODO.md` §3.1. The code serialization and deterministic call-site guards are in place; SQLite cannot exercise the lock itself.

## Recommendations
- The user should run the full `.venv/bin/pytest -q` gate before the work is considered final.
- No commits were created in this workstream.
