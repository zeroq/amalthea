# ADR-009: `IngestionSource` is Organisation-Owned

**Status:** Accepted (2026-10-16) — implemented
**Date:** 2026-10-16
**Owner:** planner
**Supersedes:** ADR-008 decision 4 (the source as a *fleet-global* reusable mapping profile). ADR-008
decisions 1–3 and 5 stand unchanged.
**Extends:** ADR-004 (tenant isolation — the `owner_org`/`PROTECT` + blocker pattern, and Q5 break-glass
reads) and ADR-006 (role semantics — the role gate answers before the org gate, both as 404)
**Related:** `docs/planning/PLAN-2026-10-16-source-field-mapping.md` §6.1 (the Q5 decision and its
consequences), `docs/planning/PLAN-2026-10-15-token-only-webhook-routing.md`

> **Numbering note:** see ADR-008 — 007 is claimed in code by the *user.org required* ADR, so the next
> free number is used.

## Context

ADR-008 §4 kept `IngestionSource` out of tenancy deliberately: it was a **read-only, write-less**
mapping profile that several tokens (one per organisation) could point at, with a globally unique `slug`
and no UI writer. Nothing about the object was tenant-specific, so nothing about it was tenant-scoped.

PLAN-2026-10-16 changes what the object *is*. `mapping_config` gains a real language
(`ingest/mapping.py`, v2: targets, ordered JSONPath candidates, transforms, per-rule defaults), it is
edited per source in Settings, and it becomes the thing that decides what an alert from a feed *means*.
Three consequences follow, and each of them is a tenancy claim:

1. **Rules are a tenant's opinion.** Which vendor field holds the severity, whether a numeric `severity`
   of 90 is "Critical", which path is the title — that is a mapping the operator of *that* organisation
   maintains. A fleet-global profile means the first tenant to configure `crowdstrike-edr` silently
   determines another tenant's alert titles and severities.
2. **A write surface needs an owner.** ADR-008 §4 was safe partly because nothing wrote the row from the
   UI. Once an editor exists, the permission question is "who may edit *this* source", and a global row
   has only one available answer: everybody, or superAdmin-only (the option PLAN Q5 explicitly rejected).
3. **`slug` uniqueness has to move.** With an org-owned editor, two tenants must each be able to run a
   feed called `crowdstrike-edr` with their own rules. A global `unique=True` on `slug` forbids exactly
   the case org ownership exists to allow.

The same Q5 review also surfaced a latent hole: `IngestionToken` carries its own `organisation`, and
since ADR-008 nothing tied it to the `organisation` of the source it points at. A token naming tenant B
while pointing at tenant A's profile is a **cross-tenant ingest** — B's alerts, written with A's mapping,
stamped with B as the owner. There is no CHECK constraint that can express it (it spans two tables), so it
sat unenforced.

## Decision

1. **`IngestionSource.organisation`** — a non-null FK to `identity.Organisation`, `on_delete=PROTECT`,
   `related_name="ingestion_sources"`. There is **no model-level default**: every creation site names the
   owner, because a convenience default on a `PROTECT`ed tenancy FK is the fail-open shortcut
   `identity/scoping.py` exists to prevent.
2. **`slug` is unique *per organisation*.** The global `unique=True` is replaced by
   `UniqueConstraint(fields=("organisation", "slug"), name="ingestion_source_org_slug_uniq")`. Same slug
   in two orgs: two feeds. Twice in one org: refused by the database.
3. **The token invariant is enforced in Python**, on every path: a token whose `organisation` is not its
   source's is refused by `IngestionToken.save()` (raw writes, fixtures, shell, future migrations) and by
   `IngestionToken.create_token()` (refused *before* hashing, so no prefix is burned and no row is
   written). The Settings form resolves the source *within the actor's org* and therefore cannot express
   the bad pair at all. One conformance test per path —
   `tests/conformance/test_settings_source_mapping.py` — because a cross-table check cannot.
4. **The org is derived, once, from the tokens.** `ingest/migrations/0003_ingestion_source_organisation`
   adds the column, backfills per source from the distinct organisations of its tokens, then makes it
   NOT NULL and swaps the unique constraint. **A source whose tokens span two organisations raises**
   instead of picking one: the right failure mode for a tenancy question is a human splitting the feed,
   not a coin flip. A source with no tokens falls back to `settings.AMALTHEA_DEFAULT_ORG_NAME` (the
   organisation `identity.0006` guarantees exists — which is why this migration depends on that one and
   not on identity's head).
5. **`PROTECT` is reported, not just enforced.** An organisation that owns a feed cannot be deleted, and
   `identity.services.organisation_blockers` gains `ingestionSources` so the 409 body and the UI's delete
   modal (`_org_blockers_text`) name "1 ingestion source" instead of leaving a bare `ProtectedError`.
6. **The source page is gated by role and org.** `GET /settings/sources/<uuid>` (`ui-settings-source-detail`)
   resolves through `identity.scoping.resolve_ingestion_source`: `admin` for their own organisation's
   feeds, `superAdmin` for any, everyone else never. Both refusals are the same 404 as a nonexistent id
   (no existence oracle), and a superAdmin's foreign read is recorded (`cross_org_read` /
   `IngestionSource`) per ADR-004 Q5.
7. **`Alert.ingestion_source` stays `SET_NULL`.** Retiring a feed still never deletes what it received
   (ADR-002 "unlink on delete"); org ownership does not change the alert's lifetime.

## Consequences

- **Positive:** the mapping gains an owner, so "who may edit this tenant's rules" has a scoped answer
  (AC8 is a conformance test, not code review); two tenants can run the same vendor feed with different
  rules; the token/source organisation mismatch is refused on every write path; org deletion stays
  explicable.
- **Negative / accepted:** two organisations can no longer share one profile, and the same vendor mapping
  duplicated per org is **duplication by design** — that is the price of a per-tenant editor. A source
  whose tokens straddle two orgs now needs a manual split before the migration will run (it raises with
  the org names and the remedy). ADR-008 §5's `get_or_create`-by-name provisioning is now scoped to the
  actor's organisation, so the same name in two orgs provisions two rows rather than reusing one.
- **Unchanged by this ADR:** routing is still by credential, not by path (ADR-008 1–3); the endpoint stays
  `POST /api/v1/alerts/webhook/`; `mapping_config` stays an unvalidated-at-the-DB `JSONField` (`CheckConstraint`
  cannot validate JSONPath); no fleet-wide "shared" scope above the organisation is introduced.
- **Not chosen (recorded):** a per-organisation `SourceMappingOverride` table over a shared default. It
  preserves reuse, but every ingest would have to answer "which two configs apply, in what order" for a
  product with no second tenant using a shared profile today (PLAN §6.1).

## Alternatives

- **Keep the profile global and let only `superAdmin` edit it** (PLAN Q5's rejected option). Cheapest by
  far — no migration, no invariant — but it makes every tenant's alert semantics depend on one operator,
  and it leaves the mapping unowned by the people who must maintain it.
- **Enforce the token/source pair in a database trigger or a composite FK.** Postgres can do it with a
  composite foreign key *if* the source exposes `(id, organisation)` as a unique key; that is a real
  option, but it buys a constraint Django's migration state cannot express or test, for a rule that has
  exactly three writers. Kept as a future hardening note, not taken here.
- **Backfill by picking the first token's organisation for a split source.** Silently moves one tenant's
  ingest into another's tenant. Rejected; the migration raises instead.
