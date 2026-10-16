# ADR-008: Webhook Routing is by Credential, Not by Path

**Status:** Accepted (2026-10-15) — implemented
**Date:** 2026-10-15
**Owner:** planner
**Extends:** ADR-004 decision 6 (ingest is token-routed) and ADR-004 Q6 (`IngestionToken`)
**Supersedes:** the `{source_id}` path segment in AGENTS.md §2 Module A and ADR-002 §D9 (as amended
2026-10-15)
**Related:** `docs/planning/PLAN-2026-10-15-token-only-webhook-routing.md`,
`docs/planning/PLAN-2026-10-15-settings-sources-ingestion-tokens.md` (whose non-goal N1 this reverses)

> **Numbering note:** 007 is already claimed in code and tests by the *user.org required* ADR planned in
> `docs/planning/PLAN-2026-10-13-non-null-user-org.md` (`identity/models.py`, `identity/migrations/0009_*`,
> `amalthea/settings/base.py`, ~15 tests all cite "ADR-007"). That document has not been written yet, so
> this ADR takes the next free number rather than colliding with it.

## Context

The spec was self-contradictory about the webhook URL. AGENTS.md §2 (Module A) wrote it as
`POST /api/v1/alerts/webhook/{source_id}`, while AGENTS.md §4 wrote the bare
`POST /api/v1/alerts/webhook/`. The implementation followed §2, so the `IngestionSource.slug` sat in the
path. That had three consequences:

1. **An unauthenticated existence oracle.** `{source_id}` was resolved *before* the credential, so an
   unknown slug returned `404` and a known slug with a bad secret returned `401`. Anyone could enumerate
   a tenant's configured feeds without a credential — the exact class of leak ADR-004's "org is never
   taken from the body" rule was written to prevent, reached through a different door.
2. **A URL that leaks tenant structure.** Ingest URLs get pasted into vendor consoles, runbooks and
   SIEM export configs. Embedding `o365` / `crowdstrike` in them advertises the stack and couples the
   integration to a name that is really an internal mapping-profile key.
3. **A two-key client contract for no benefit.** The operator had to manage *both* a slug and a secret
   when the secret already resolves to exactly one `(IngestionSource, Organisation)` pair — the token
   was sufficient.

The `IngestionToken` model already carries everything routing needs: `prefix` (unique, indexed) →
`secret_hash` (Argon2) → `source` FK + `organisation` FK. A single indexed lookup on `prefix` is enough
to build all three-throttle-bucket keying (`wh:src:`, `wh:ip:`, `wh:token:`), the mapping config and the
org stamp.

## Decision

1. **The webhook path is source-less: `POST /api/v1/alerts/webhook/`** (plus a deliberately registered
   no-slash twin, since `APPEND_SLASH` would 301-redirect and drop the body). This is a **hard cut** —
   `POST /api/v1/alerts/webhook/<slug>/` is no longer routed. Ingest is one URL per deployment.
2. **The credential is the only routing fact.** `_authenticate_token(request)` resolves the presented
   secret (via `X-Webhook-Secret`, `X-Api-Key` or `Authorization: Bearer`) by `prefix` with no prior
   context, then verifies with Argon2, and returns the token with its `source`/`organisation` preloaded.
3. **Match on the URL is deleted.** There is no "unknown source" branch. Tokenless, unknown-prefix,
   wrong-secret and revoked-token all return the same **401** `unauthorized` — no existence oracle.
4. **`IngestionSource` survives as a reusable mapping profile**, not as a route segment: `mapping_config`,
   `default_severity`, `correlation_enabled` stay keyed by a unique, global `slug`, and many tokens (one
   per org) may reference one profile. Its read-only `/sources` view does not grant a new project-wide
   invariant — sources are **not** org-owned and are still not CRUD-managed in the UI.
5. **A source name provisions a profile on demand.** The Settings → Sources create form takes a free-text
   *source name* instead of a picker; it is `slugify`-ed and `get_or_create`-d (`IntegrityError` → refetch),
   which is the first UI writer for the previously write-less table. Provisioning a new profile writes
   `AuditLog` action `ingestion_source.created`. `admin` may mint a token for their own org only;
   `superAdmin` selects the org.

## Consequences

- **Positive:** closes the slug-enumeration oracle; one stable URL per deployment; ingest clients manage a
  single secret; the org-stamp rule (`owner_org` from the token, never the body) is now the *only* trust
  input on ingest; the mapping profile is decoupled from the URL, so renaming a feed no longer breaks
  integrations.
- **Negative / accepted:** an unauthenticated request costs one indexed lookup + one Argon2 verify before
  it is rejected, so a pre-auth per-IP throttle is still owed (tracked in `TODO.md` §10.10). Operators with
  existing `/webhook/<slug>/` clients must repoint them at the bare URL — a breaking API change, taken
  deliberately while the surface has no external consumers.
- **Documentation:** `AGENTS.md` §2 (Module A) was corrected to the bare path — `AGENTS.md` §4 already
  specified it, so no spec change was needed beyond removing the contradiction. `docs/spec/api.md`,
  `docs/spec/identity-auth.md`, `docs/spec/data-model.md`, `README.md` and ADR-002's endpoint table were
  updated.
- **Not a stability promise:** the bare path is the contract; the source profile's `slug` is an internal
  resolution key and may be changed without notice.

## Alternatives considered

- **Keep `{source_id}` and reject only unknown slugs with a uniform 401** — preserves the URL shape but
  keeps a client-visible two-key contract and still lets an attacker confirm which slugs exist (the
  resolution happens before auth, so the timing/behaviour differs). Rejected.
- **Make `IngestionToken` self-contained (fold `IngestionSource` into it)** — would remove the
  name-resolution step entirely, but loses the shared-mapping-profile property that ADR-004's
  one-token-per-tenant fan-out depends on and orphans `Alert.ingestion_source`. Rejected (Q1).
- **Accept the slug *and* the token (optional path segment)** — two code paths, two sets of tests, and the
  deprecated one is the vulnerable one. Rejected.
