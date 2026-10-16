# PLAN 2026-10-15 — Token-Only Webhook Routing (`/api/v1/alerts/webhook/`)

**Status:** Implemented (uncommitted at time of writing)
**Owner:** planner
**Related:** [ADR-008](../decisions/ADR-008-webhook-routing.md),
[ADR-004](../decisions/ADR-004-tenant-isolation.md) (Q6),
[PLAN-2026-10-15-settings-sources-ingestion-tokens](./PLAN-2026-10-15-settings-sources-ingestion-tokens.md) (superseded in part — see §6 Deviations)

## 1. Goal

Make webhook ingestion a single, source-less endpoint whose credential alone decides the feed profile
**and** the tenant, and give the Settings → Sources form a way to create the feed it previously only
let you select.

Success = an operator can mint a token by typing a feed name, POST to
`/api/v1/alerts/webhook/` with that secret, and get an alert stamped with the right org — with no
`<slug>` anywhere in the client contract.

## 2. Non-goals

- No pre-auth per-IP throttle on the webhook (deferred; `TODO.md` §10.10).
- No source CRUD UI: sources are still provisioned only by name-on-token-create (or in the shell);
  `mapping_config` still has no editor.
- No migration or alias for the old `/webhook/<slug>/` clients — this is a hard cut.
- No change to the org stamping rule, guard order, response shapes or throttle bucket keys.

## 3. Architecture

```
POST /api/v1/alerts/webhook/            (no <slug>)
  Content-Length cap .............. 413 entityTooLarge
  Content-Type check ............. 415 unsupportedMediaType
  _authenticate_token(request) ... 401 unauthorized   ← the only routing step
        prefix lookup (unique, indexed)
        Argon2 verify  →  token.source, token.organisation
  throttle (src/ip/token buckets) . 429 rateLimitExceeded
  parse + map + persist .......... 400 / 201
```

The token is resolved with **no path context**, so `_authenticate_token` reads only the request. The
`source` argument was dropped from both it and `_coerce_severity`.

## 4. Data model

No schema change. `IngestionSource.slug` stays unique and global (it is now the name-resolution key).
The one new write path is `IngestionSource.objects.get_or_create(slug=slugify(name), defaults=...)` from
`ui/views.py`, and the one new audit action is `ingestion_source.created`
(`identity/audit.py::record_ingestion_source_created`, `target_org=None`).

## 5. Tasks & Acceptance Criteria

| # | Task | AC |
|---|------|----|
| T1 | `ingest/urls.py`: bare `alerts/webhook/` + no-slash twin; drop the slug route | `reverse("alerts-webhook")` resolves; `/webhook/o365/` 404s |
| T2 | `ingest/views.py`: `_authenticate_token(request)` token-first; `webhook_alerts(request)`; no 404 branch | tokenless / unknown-prefix / wrong-secret / revoked → identical 401 body |
| T3 | UI create form takes a **source name** | POST with a new name → source provisioned (slugified) + `ingestion_source.created` audit row; POST with an existing name → profile reused, no duplicate |
| T4 | Name that slugifies to empty is rejected | `"!!!"` → 302 with error, no `IngestionSource`/`IngestionToken` rows |
| T5 | Copy + docs updated | `AGENTS.md` §2, `docs/spec/{api,identity-auth,data-model}.md`, `ADR-002` table, `README.md`, `.opencode/agents/*`, `dashboard.html`, `sources_list.html` show the bare path |

## 6. Deviations (recorded, not hidden)

- **Reverses non-goal N1** of `PLAN-2026-10-15-settings-sources-ingestion-tokens.md:42` ("No source CRUD.
  Sources stay global…"). The reversal is narrow: sources are still not org-owned and gain no editor —
  they are now *provisionable by name* from the token form, because that plan shipped a create form whose
  source list had no UI writer at all (`sources = IngestionSource.objects.order_by("name")` had no write
  path in the product).
- **Supersedes the URL** stated at `PLAN-2026-10-15-settings-sources-ingestion-tokens.md:192,282,452` and
  at ADR-004 Q6 ("the URL carries the source"), per ADR-008.
- **`sources_list.html` is only half-fixed.** The stale URL sentence is corrected, but the `open` /
  `required` pills and the claim that a source "marked open accepts unauthenticated posts" are left as-is:
  they have been wrong since strict token auth (2026-10-10) and are pinned by
  `test_ui_loop.py:520-537`. Splitting copy-and-pin into its own change keeps this one small; tracked in
  `TODO.md` §10.8.

## 7. Testing

`tests/conformance/`: `test_webhook_hardening.py` (helper de-slugified; the old unknown-source-404 test
replaced by `test_an_unknown_token_does_not_leak_whether_a_feed_exists`), `test_settings_ingestion_tokens.py`
(+`TestSourceProvisioning`: provisioning, cross-org reuse, empty-slug rejection), `test_tenant_scoping.py`,
`test_authz.py`, `test_mvp_loop{,_automation}.py`, `test_ui_loop.py`. Full suite: **1918 passed, 3 skipped**.

## 8. Risks

- **Silent 301s dropping bodies** if the no-slash twin is ever removed — the twin is load-bearing for
  `APPEND_SLASH`; keep both names in the URLconf.
- **Name collisions are silent by design**: two operators typing "CrowdStrike" share one profile and its
  `mapping_config`. That is the intended fan-out behaviour (ADR-004 Q6), but it means a typo'd name
  creates a new, unmapped profile rather than failing. Accepted for now; a merge/rename story is future
  work.
- **Hard cut** breaks any external `/webhook/<slug>/` client. Accepted while the surface has no external
  consumers.
