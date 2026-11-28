# PLAN-2026-11-27 — §10 open follow-ups

**Status:** Implemented and verified 2026-11-27; uncommitted on top of the §15, §11.4, and §12.12 work. Verification: `docs/verification/VERIFY-2026-11-27-section-10-followups.md`.

## Summary
Close the five remaining `TODO.md` §10 items: custom-field API write parity with independent verification (10.7-fu1), a pre-auth per-IP webhook limiter (10.10), token reap/create serialization (10.12-fu1), token throttle key hashing (10.12-fu2), and the three token test-quality gaps (10.12-fu4).

## Goals
- **10.7-fu1:** route UI and API custom-field writes through one shared service; PATCH/PUT accepts `customFields`.
- **10.10:** bound unauthenticated webhook floods before the token lookup/Argon2 step without masking the post-auth 429.
- **10.12-fu1:** make last-token reaping and token creation mutually exclusive via `IngestionSource` row locks on both sides.
- **10.12-fu2:** stop using the plaintext prefix directly as a cache-key component.
- **10.12-fu4:** fix the misleading `_mint` feed name, pin the Revoke affordance, and test delete rollback.

## Non-goals
- No schema migration. Existing `CaseCustomFieldValue`, `IngestionToken`, and `IngestionSource` rows stay as-is.
- No custom-field create/delete API: only updates to existing case-scoped value rows, matching the UI.
- No new webhook endpoint or routing fact; ADR-008/ADR-009 token routing is unchanged.
- No widen-prefix migration for tokens: 72-bit prefix collision with bounded retries remains accepted and documented once the bucket key is hashed.
- No live Postgres concurrency harness in this worktree; bounds are pinned by deterministic guards and documented for a later Postgres run.

## Architecture
- **Stack rationale:** keep request-free logic in service modules (`cases.custom_fields`, `cases.update`) so DRF and Django-template views cannot drift.
- **API/UI choke point:** `apply_case_update` owns the case write surface; it now delegates `customFields` to `cases.custom_fields.apply_custom_field_updates`.
- **Scalability/security:** cache-only limiter runs before any token DB lookup; no new datastore dependency.
- **ADR reference:** ADR-008 (source-less webhook path), ADR-009 (org-owned feeds), ADR-002 (case enums).

## Data model
- No migration.
- `CaseCustomFieldValue.value` remains JSON; coercion rules move, not change.
- `IngestionToken.prefix` remains 12 chars; only the cache key derivation changes.

## API contracts
- `PATCH|PUT /api/v1/case/{idOrNumber}` now accepts an optional `customFields` list:
  - each item requires `_id` (existing `CaseCustomFieldValue` UUID on this case) and `value`;
  - `name`/`type`/`order` from GET are accepted but ignored on write;
  - invalid type, oversized value, unknown/foreign `_id`, or malformed item shape → `400` with `fields.customFields`;
  - no-op writes emit no timeline event but still count as a supplied field.
- Webhook 401/429 ordering: pre-auth IP 429 may fire before token verification; post-auth source/IP/token buckets remain unchanged. Pre-auth bucket is separate so it cannot mask the post-auth budget.
- Production refuses to boot when `WEBHOOK_PREAUTH_RATE_IP_PER_MIN <= 0`.

## UI/UX
- No layout changes.
- UI custom-field edit continues to call the same service and emits the same `custom-field-updated` timeline event.
- Settings → Sources Revoke affordance remains visible only for active tokens and is pinned by markup tests.
- Dark theme, keyboard-driven behavior, and modal conventions are unchanged.

## Phases and Acceptance Criteria
### Phase 1 — Custom-field API parity (10.7-fu1)
- Move coercion/row/update helpers to `cases/custom_fields.py`; keep `ui/case_custom_fields.py` as a compatibility re-export.
- Call `apply_custom_field_updates` from `apply_case_update`.
- Add API conformance tests and a verification record under `docs/verification/`.
- **AC1:** API PATCH updates an existing value and emits one `custom-field-updated` event.
- **AC2:** Invalid type, oversized value, foreign `_id`, and malformed item each return 400 without partial writes.
- **AC3:** Re-submitting the same value emits no new event.
- **AC4:** UI write and API write produce the same stored value and event kind.
- **AC5:** Independent verification record lists AC1–AC4 as PASS with targeted test evidence.

### Phase 2 — Pre-auth webhook limiter (10.10)
- Add `WEBHOOK_PREAUTH_RATE_IP_PER_MIN` (`AMALTHEA_WEBHOOK_PREAUTH_IP`, default 600).
- Check `wh:preauth:ip:{ip}:{window}` before `_authenticate_token`.
- **AC6:** Unauthenticated requests above the limit get 429 without reaching token lookup.
- **AC7:** A legitimate sender from a different IP is unaffected by an attacker's budget.
- **AC8:** Pre-auth 0 disables the limiter in dev/test; prod fails closed.
- **AC9:** `docs/spec/api.md` documents the budget and the separate bucket.

### Phase 3 — Token concurrency and bucket key (10.12-fu1/fu2)
- Lock the `IngestionSource` row inside both the token-create and token-delete transactions before token mutation/reap checks.
- Hash the throttle bucket component with SHA-256 of the token prefix.
- **AC10:** Both create and delete paths acquire `IngestionSource.objects.select_for_update()` in the same transaction that writes/deletes the token.
- **AC11:** ASQLite-safe guard tests pin both lock call sites; a Postgres concurrency case is documented as deferred.
- **AC12:** The webhook cache key never contains the raw token prefix.
- **AC13:** Existing webhook throttle tests still pass with equivalent budgets.

### Phase 4 — Test-quality gaps (10.12-fu4)
- Fix `TestTokenActivationLifecycle._mint` to name its source after the slug so the posted `source_name` binds the intended row.
- Pin Revoke in the active state and its absence in deactivated/revoked states.
- Add a rollback test that raises after `token.delete()` and asserts the token and source survive.
- **AC14:** No `_mk_source` row is orphaned by the lifecycle helper.
- **AC15:** Active markup includes Revoke; paused/revoked markup does not.
- **AC16:** A mid-transaction raise restores the token row.

## Security
- Custom-field writes remain case-scoped; foreign IDs are rejected as 400 to avoid an existence oracle.
- Pre-auth limiting reduces Argon2/DB work from unauthenticated floods; no secret is logged or cached.
- Row locks on `IngestionSource` are the parent-side serialization point for both token writers.
- Hashing the throttle key prevents prefix reuse as both lookup identity and cache namespace.
- Secret scan remains mandatory; no secret values in fixtures or docs.

## Testing
- Updated: `tests/conformance/test_case_custom_fields_api.py`, `tests/conformance/test_webhook_hardening.py`, `tests/conformance/test_settings_ingestion_tokens.py`.
- New: focused §10 follow-up conformance module as needed.
- Gates: targeted pytest, `make check`, `git diff --check`, `scripts/secret-scan.sh --files ...`.
- User-owned final gate: full `pytest -q`.

## Risks
- Pre-auth limiter can block legitimate bulk senders behind a shared NAT; default 600/min is intentionally above the post-auth 300/min budget and documented.
- Custom-field API writes change hashes/ledger volume for callers that previously saw `customFields` ignored; intentional parity fix.
- SQLite cannot prove the Postgres row-lock race; deterministic call-site guards + deferred harness are the interim mitigation.
- Prefix collision remains a bounded retry path; documented as accepted after cache-key hashing.

## Open Questions
- Whether to widen `IngestionToken.prefix` beyond 12 chars remains open; no migration is included here.
- A real Postgres concurrency harness for AC11 remains open.
