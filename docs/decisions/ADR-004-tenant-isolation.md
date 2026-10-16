# ADR-004: Tenant Isolation

**Status:** Accepted
**Date:** 2026-10-10
**Owner:** planner
**Supersedes:** decision 3.2 (2026-10-06, single-tenant + nullable `Organisation` FK) and the tenant deferral (§6.4 / F2) of 2026-10-09.

## Context

Amalthea models tenants (`Organisation`, `User.org`, nullable `Case.owner_org` / `Alert.owner_org`, case-only `Share`) but never enforced them: `owner_org` was never populated, so every scoping filter was a no-op, and the WebSocket consumer, observable mutation, `run_now`, the UI, and the ingest webhook never consulted organisation at all. `Observable` was globally deduplicated on `(data_type, data_hash)`, so one forensic artifact could span every tenant. There was no audit entity. Decision 3.2 deliberately deferred enforcement; §6.4 recorded the options (middleware org-scope vs per-view filters; roles) without choosing.

The project is **greenfield** — no production data to preserve — so strict schema changes are affordable now and expensive later.

## Decision

**Strict, mandatory, app-layer multi-tenancy.**

1. **Mechanism — app-layer scoping helpers + uniform 404.** A single module `identity/scoping.py` provides `case_visible_q`, `resolve_case`, `org_filtered`, shared by HTTP and WebSocket. Cross-tenant and non-existent resolve identically to a 404 / `4404` — never a 403 (no existence oracle). **No middleware org-scope, no Postgres RLS.**
2. **Strict default.** Every tenant-owned row carries an org; `Case.owner_org`, `Alert.owner_org`, `Observable.owner_org` become `NOT NULL`. There is no "org-NULL = open" mode. Org-less callers see nothing.
3. **Observables are org-scoped.** Unique on `(owner_org, data_type, data_hash)` (was global); the same value in two orgs is two rows, and case auto-linking is confined to one org. **This narrows the "same artifact across cases over months" feature from global to per-tenant** — an accepted trade for isolation.
4. **Roles.** `User.role ∈ {superAdmin, admin, analyst, read-only}` (default `analyst`), enforced by DRF permission classes. One org per user (`User.org`, DB-nullable but required for access; `NOT NULL` is avoided because it breaks `createsuperuser`).
5. **superAdmin is cross-tenant and audited.** A new `AuditLog` records break-glass reads and role/org/share/token changes.
6. **Ingest is token-routed.** A new `IngestionToken(source, organisation, secret_hash, …)` binds
   a token to exactly one org; the same `IngestionSource` routes to different tenants by token. A
   tokenless/invalid request is `401` (closes today's open-source hole). Organisation is **never**
   taken from the request body.
    *Implemented 2026-10-15 (ADR-008): the webhook URL carries no source slug — the credential alone
    identifies the feed, so the token is looked up by `prefix` with no path context.*

7. **Sharing stays.** `Share` remains the only sanctioned cross-tenant read, additive and default-deny, and is itself authorized (owner org only).

## Rationale

- **Explicitness over magic.** App-layer helpers are greppable and unit-testable; middleware query interception is invisible and hard to reason about; RLS adds migrations, session-var plumbing, and PG-only test surface without a decision urgency.
- **Fail-closed on greenfield.** Mandatory org (no nullable escape hatch) is only cheap now; the single-tenant decision 3.2 is retired.
- **Isolation beats global dedup.** Sharing enrichment data and a link graph across tenants is a leak; per-org observables are the honest model.
- **Uniform 404.** Distinguishing "not yours" from "does not exist" would leak tenant existence. `identity/views.py:_own_org` already establishes this.
- **Ingest must not be the hole.** Token-routing gives an unauthenticated write path a deterministic, spoof-resistant tenant, and closes the unsecreted-source open door.

## Consequences

- Migration is schema+data in one release: create default org (`AMALTHEA_DEFAULT_ORG_NAME`, default `"Default"`), backfill NULL `owner_org`/`User.org`, then `ALTER … NOT NULL` and swap the observable unique constraint.
- `Observable` cross-org linking is gone by construction; existing dev data is greenfield so no compatibility shim is owed.
- Every tenant-touching route must use the helpers, guarded by a conformance route-enumeration test.
- `IngestionToken` and `AuditLog` must appear in Django admin; secret hygiene (AGENTS.md §5) applies (hashes only).
- Retires deviations **F2** and plan §6.4 / §12-Q4; subsumes Wave B **S1/S2/S4/F2**.
