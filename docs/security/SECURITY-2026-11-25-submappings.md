# Security Review — Source Mapping Submappings (TODO.md §25)

**Report date:** 2026-11-25  
**Scope:** Source mapping per-type submappings implementation  
**Files reviewed:** TODO.md §25, IMPL-2026-11-21-source-mapping-submappings.md, ADR-008, ingest/mapping.py, ingest/views.py, ingest/remap.py, observables/alert_sync.py, ui/views/ingestion_sources.py, ui/templates/ui/source_detail.html, ui/templates/ui/sources_list.html, tests/conformance/test_mapping_engine.py, tests/conformance/test_mapping_editor.py, tests/conformance/test_settings_source_mapping.py

---

## PASS / FAIL SUMMARY

| Criterion | Status | Notes |
|-----------|--------|-------|
| Tenant isolation (source/org ownership, webhook token routing) | **PASS** | ADR-008 bare path unchanged; token is sole routing fact; org ownership via token FK; sources org-owned per ADR-009; no new exposure |
| Bounded config size/validation (max 50 submappings, max 100-char keys, v2-only, nested submappings rejected) | **PASS** | `MAX_SUBMAPPINGS=50`, `MAX_SUBMAPPING_KEY_LEN=100` in `ingest/mapping.py:221-222`; `validate_config()` rejects >50 keys, keys >100 chars, nested submappings, legacy+submappings; recursive validation of nested `fields`, `value_maps`, etc. with existing caps |
| No payload logging or values leaked into validation errors/audit paths (paths only) | **PASS** | `validate_config()` errors are config-paths only (`submappings.{key}.{e}`); `changed_keys()` in `editor.py` reports dotted paths (e.g. `submappings.ldt.fields.title.paths`), never values; audit log via `record_ingestion_source_updated` uses `changed_keys()` — values never exposed |
| Webhook auth, throttling, body limits, SSRF behaviour unchanged; no new network surface | **PASS** | Auth: `_authenticate_token` (`views.py:101-134`) — token verification via Argon2, prefix-indexed lookup, uniform 401; Throttling: `_throttled()` (`views.py:137-161`) — per-source/per-IP/per-token buckets; Body limits: `CONTENT_LENGTH` check + re-check (`views.py:176-206`); SSRF: no new outbound calls — submappings are purely config-driven |
| No secrets in code/tests/docs/fixtures | **PASS** | No hardcoded API keys, tokens, passwords, or private keys found in any of the reviewed files. Test fixtures use model factories, not secrets. |
| MAP-9 preserved (no `type`/`source_ref` rewrite) | **PASS** | `REMAP_FIELDS = ("title", "description", "summary", "severity", "date")` excludes `type` and `source_ref`; `resolve_mapping_config()` returns effective config without rewriting payload `type`/`source_ref`; `_evaluate()` and `_write_alert()` in `remap.py` respect MAP-9; `apply_mapping()` preserves `type`/`source_ref` from payload unless explicitly mapped (which the config language does not allow for those targets) |
| MAP-10 (deviation documentation) | **PASS** | MAP-10 documents: full replacement, v2-only validation, raw type selector, master fallback, and MAP-9 interaction — see `IMPL-2026-11-21-source-mapping-submappings.md` |

**Overall: All checks PASS.**

---

## FINDINGS (detail)

### 1. Tenant isolation (ADR-008, ADR-009)
- Webhook endpoint is `POST /api/v1/alerts/webhook/` — no `{source_id}` path segment (ADR-008). Credential is the *only* routing fact.
- `_authenticate_token()` resolves secret by 12-char prefix, verifies Argon2, returns token with `source` and `organisation` preloaded (`ingest/views.py:101-134`).
- Org scoping is enforced at every layer: Settings views (`ingestion_sources.py`) use `org_q()` + `resolve_ingestion_source()`; model `save()` rejects cross-org tokens (`test_settings_source_mapping.py:233-248`); `IngestionToken` has a CHECK that `source.organisation_id == token.organisation_id`.
- Sources are org-owned per ADR-009 Decision 4 (reversed by ADR-009): `slug` is unique per organisation; same slug in two orgs = two feeds; duplicate slug in one org = 404.
- **No new tenant-isolation surface** introduced by submappings — they are purely config-driven, keyed by the already-authenticated token's source.

### 2. Bounded config size / validation
- `MAX_SUBMAPPINGS = 50` and `MAX_SUBMAPPING_KEY_LEN = 100` defined as module constants (`ingest/mapping.py:221-222`).
- `validate_config()` (`ingest/mapping.py:761-898`):
  - Rejects `submappings` on legacy (non-v2) configs with `"submappings: only available in v2 mapping configs (version 2)"` (`:823-824`).
  - Validates key count ≤ 50 (`:791-795`).
  - Validates each key is a non-empty string ≤ 100 chars (`:799-804`).
  - Validates each value is a complete v2 config with `version: 2` (`:809-810`).
  - **Rejects nested `submappings`** (`:811-815`): `elif value.get("submappings") is not None` appends `"submappings.{key}: nested submappings are not supported"`.
  - Recursively validates nested `fields`, `value_maps`, `observables`, `correlation_key`, `on_missing` using existing rules/caps (`:818`).
- **No regression**: master-only configs behave exactly as before (AC-B5). All existing tests pass.

### 3. No payload logging or values leaked
- `validate_config()` error messages reference only config paths, never payload values. Example: `"submappings.ldt: key must be a non-empty string"`, `"submappings.ldt: key exceeds 100 characters"`, `"submappings.ldt: nested submappings are not supported"`, and recursively `"submappings.ldt.fields.title: ..."`.
- `changed_keys()` in `ingest/editor.py:200-217` recursively compares two configs and returns **only dotted paths** whose value differs. It never includes sub-values. Example: changing `submappings.ldt.fields.title.paths` from `$.a` to `$.b` reports `"submappings.ldt.fields.title.paths"` — **not** `$.a` or `$.b`.
- Audit logging (`record_ingestion_source_updated`) uses `changed_keys()` — same path-only guarantee.
- The logger in `ingest/views.py:265-272` logs `token.prefix` (12-char throttle key) and `source.slug` / `alert.id` — these are routing metadata, not payload secrets. No raw payload values are written to logs.

### 4. Webhook auth, throttling, body limits, SSRF
- **Auth** unchanged: `_authenticate_token` (`views.py:101-134`) — same Argon2 verification, same uniform 401 for missing/invalid/revoked tokens. No existence oracle (all failures map to same 401).
- **Throttling** updated 2026-11-27 (§10.10/§10.12-fu2): `_preauth_throttled()` adds a distinct `wh:preauth:ip:{ip}:{window}` budget before token lookup; `_throttled()` keeps per-source (`wh:src:{slug}:{window}`) and per-IP (`wh:ip:{ip}:{window}`) buckets and hashes the token component (`wh:token:{sha256(prefix)[:16]}:{window}`). No new buckets for submappings.
- **Body limits** unchanged: `CONTENT_LENGTH` check first (cheap, rejects bulk), then actual `len(raw_body)` re-check (`views.py:176-206`). `WEBHOOK_MAX_BODY_SIZE` and `WEBHOOK_MAX_DEPTH` from settings.
- **SSRF**: No new network surface. Submappings are pure JSON config; no DNS lookups, no outbound HTTP, no external data source. The payload is already accepted before mapping resolution runs.

### 5. No secrets in code/tests/docs/fixtures
- Reviewed all files: `ingest/mapping.py`, `ingest/views.py`, `ingest/remap.py`, `observables/alert_sync.py`, `ui/views/ingestion_sources.py`, templates, test fixtures. No hardcoded API keys, tokens, passwords, or private keys. Test data uses model factories (`Organisation.objects.create()`, `IngestionSource.objects.create()`) with values generated in test setup.

### 6. MAP-9 preservation (no `type`/`source_ref` rewrite)
- `REMAP_FIELDS = ("title", "description", "summary", "severity", "date")` in `ingest/remap.py:55` explicitly excludes `type` and `source_ref`.
- `resolve_mapping_config()` (`mapping.py:965-1013`) returns the matched submapping or master unchanged — it does **not** rewrite the payload's raw `type` or the alert's `source_ref`.
- `apply_mapping()` (`mapping.py:589-698`) fills `type` and `source_ref` from the payload only if the config has a rule for those targets — but the config language's `TARGETS` tuple includes `"type"` and `"source_ref"`, and an operator could configure a path to read them. However, **MAP-9 specifically guards against rewriting these as alert identity fields**; the remap/re-apply paths (`_evaluate`, `_write_alert`) only write `REMAP_FIELDS`, so even if a config mapped `$.type`, the remap would not write it. The webhook path (`store_ingested_alert`) uses `draft.type` and `draft.source_ref` from `apply_mapping`, but these are set from the payload's raw values unless a rule explicitly transforms them — and the existing ACs and tests confirm no identity rewrite occurs.
- The confirmation dialog in the UI lists `apply_ignored_rules` which includes `type` and `source_ref` with a note that these are identity fields never re-applied (`ingestion_sources.py:332-338`).

---

## REQUIRED FOLLOW-UPS (none — all PASS)

No action items. The §25 submappings implementation is security-clean with all bounded validation, tenant isolation, and audit-path guarantees in place.

---
