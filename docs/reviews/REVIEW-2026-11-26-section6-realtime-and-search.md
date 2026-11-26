# Final Security Review — Realtime and Search (§6.16-fu1 + §6.16 P7)

**Date:** 2026-11-26
**Auditor:** security-auditor (read-only; no code modified)
**Scope:** `realtime/consumers.py` (OrgConsumer, CaseConsumer), `realtime/presence.py` (HMAC members, counts-only frames), `realtime/publisher.py` (org event payloads), `cases/ledger.py` + `alerts/views.py` (org refresh emissions after commit, no payload bodies), `realtime/routing.py` / `realtime/views.py` (HTTP fallback), `query/search.py` (tenant scoping, deferred fields), new trigram/btree indexes, HTMX partial views and templates for CSRF/tenant scoping/no inline assets

---

## Verdict

**No new High/Medium findings.** The realtime and search implementations are secure per the approved design. All accepted residuals are documented; no authz bypasses, existence oracles, or identity leakage detected. The sole low-severity residual is a development-secret fallback in the presence store.

---

## 1. Methodology

- Read-only review: inspected source code, tests, and configuration (no writes except creating this report).
- Cross-referenced against `TODO.md` §6.16, `PLAN-2026-11-02-search-page-rail-entry.md`, and the prior tenant-isolation security reviews.
- Verified the §6.16 P7 realtime changes and §6.16-fu1 search follow-ups against the live codebase.
- No tests were modified; conformance gates were not re-run (full pytest not executed per task instruction).
- A later user-run full suite (`/tmp/amalthea-full-pytest.log`) exposed non-security regressions in
  SQLite-safe index DDL, migration dependency order, delete modals, and malformed-filter echo.
  Remediation and targeted regression evidence are recorded in
  `docs/verification/VERIFY-2026-11-26-section6-realtime-and-search.md` §1.1; the security verdict
  is unchanged. The user's final full pytest rerun is green (`4390 passed, 3 skipped`).

---

## 2. Scope Checklist

### 2.1 WebSocket auth-before-accept (realtime/consumers.py)

- **CaseConsumer** (`realtime/consumers.py:75-167`): Authentication occurs before `accept()`. Unauthenticated sockets get 4401; forbidden targets get 4404; protocol errors get 4000. The `_is_uuid` check on `case_id` prevents malformed path injection. The `identity.scoping.resolve_case` gate enforces org-boundary access before the socket joins any group. *No existence oracle*: refused sockets never add to a group, and non-existent/forbidden targets close with 4404.
- **OrgConsumer** (`realtime/consumers.py:169-211`): `_org_access` returns `(allowed, is_cross_org_superadmin)`. Cross-org superAdmins are permitted but audited via `_audit_cross_org_org_join` — an `AuditLog` entry recording the cross-org realtime subscription. Same org analysts are allowed without audit. Non-existent orgs close with 4404.
- **Both consumers**: `disconnect` only broadcasts presence leave if `joined` was True (set after `accept`), preventing leave-orchestration for rejected connections.

### 2.2 Presence store — HMAC opacity, counts-only (realtime/presence.py)

- **`_presence_member`** (`realtime/presence.py:19-23`): Computes a server-side only HMAC-SHA256 member identifier: `hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:16]`. The identifier is 16 opaque hex chars — no user-identifying information is ever broadcast. The `secret` falls back to `"amalthea-dev-only"` when `SECRET_KEY` is absent (development guard; production uses the real `SECRET_KEY`).
- **Counts-only frames** (`realtime/consumers.py:156-160`): The `presence` relay method sends `{"type": "presence", "count": int(event["count"])}` — no member identities, no user-list leakage. The `exclude_channel` check in the consumer (`realtime/consumers.py:158`) ensures a joiner does not receive its own join broadcast.
- **Redis sorted-set with TTL** (`realtime/presence.py:35-81`): Members expire after 60 seconds (`TTL_SECONDS = 60`). `zremrangebyscore` prunes stale entries. In-memory fallback for dev/test uses the same HMAC contract.
- **Verdict**: No identity leakage. Presence is strictly numerical and opaque.

### 2.3 Org event publisher — server-derived room names (realtime/publisher.py)

- **`publish_org_event`** (`realtime/publisher.py:24-42`): The room name is `f"org_{org_id}"` derived from the *object's* `owner_org_id`, never from a caller-supplied room name. This prevents cross-org fan-out: only analysts belonging to the owner organisation (or superAdmin) receive the refresh. The docstring explicitly states: "The room name is derived server-side from the object's owner_org_id, never from a caller-supplied room name. This prevents cross-org fan-out and ensures that only analysts belonging to the owner organisation (or superAdmin) receive the refresh."
- **Verdict**: Correct by-design cross-org prevention.

### 2.4 Ledger refresh after commit, no payload bodies (cases/ledger.py + alerts/views.py)

- **`append_timeline_event`** (`cases/ledger.py:39-113`): Uses `transaction.on_commit` for both the case-room event publish and the org refresh broadcast. A rolled-back case edit emits nothing — no client can render an event the database does not have. The org refresh payload is minimal: `{"surface": "cases", "reason": reason, "object_id": str(case.id), "occurred_at": timezone.now().isoformat()}` — no raw payload bodies, no PII-in-wire.
- **`alert_creation`** (`alerts/views.py:290-386`): Also uses `transaction.on_commit` for the org refresh (`publish_org_event` with `surface: "alerts"`, `reason: "alert-created"`, `object_id`, `occurred_at`).
- **`alert_comment_list`** (`alerts/views.py:822-878`): Timeline event and org refresh both go through `transaction.on_commit`; payloads are structured, no raw telemetry in the wire.
- **Verdict**: Rolled-back changes leave no live signal; org refresh emissions have minimal, safe payloads.

### 2.5 HTTP fallback for WebSocket endpoints (realtime/routing.py / realtime/views.py)

- **`websocket_fallback`** (`realtime/views.py:12-25`): Returns HTTP 400 with a helpful plain-text message: "This endpoint requires a WebSocket connection. Please connect via WebSocket protocol (ws:// or wss://) instead of HTTP." Never a 404 that could mask the endpoint's existence.
- **Verdict**: Correct fallback; no information leakage beyond the intentional message.

### 2.6 Tenant scoping in search (query/search.py)

- **`org_q("owner_org", user)`** (`query/search.py:107-119`): Filters alerts/observables to the caller's `owner_org`. SuperAdmin gets `Q()` (all); org-less users get `Q(pk__in=[])` (deny).
- **`case_visible_q(user)`** (`query/search.py:88-104`): Cases visible via own org or shares; superAdmin sees all.
- **Deferred fields** (`query/search.py:186-188`, `212-218`, `235-236`): `description`, `summary`, `raw_payload`, `ingestion_warnings` etc. are deferred to avoid PII or large payloads in default selects.
- **Term semantics**: AND across terms, OR within fields per term; digit-only terms (with optional `#`) match `Case.number` exactly; max 200 chars / 8 terms clamped.
- **Verdict**: Proper org scoping, no cross-org leakage, input caps prevent unbounded scans.

### 2.7 New trigram/btree indexes (alerts/migrations/0020_alert_upper_trgm.py, 0021_alert_search_order_idx.py)

- **GIN trigram indexes** (`0020_alert_upper_trgm.py`): `alert_title_upper_trgm_idx`, `alert_desc_upper_trgm_idx`, `alert_summary_upper_trgm_idx` on `UPPER("title"::text)` etc. with `gin_trgm_ops`. Postgres-only; SQLite falls back gracefully.
- **Btree search-order index** (`0021_alert_search_order_idx.py`): `alert_search_order_idx` on `(owner_org, -severity, -date, -id)` — supports ordered search results with tenant scoping.
- **Existing unique constraint**: `(owner_org, source, type, source_ref)` in `alerts/models.py:163-166` and `alerts/migrations/0014_tenant_dedupe_key.py` — narrowed from the original global constraint to per-org, preventing duplicate artifacts across tenants.
- **Verdict**: Indexes are correctly scoped; unique constraint properly per-org.

### 2.8 HTMX partial views and templates (UI layer)

- **No inline assets in HTMX responses**: HTMX partials return bare HTML fragments (e.g., `<tr>` elements, card partials) — no `<style>`, `<script>`, or inline asset payloads. The base template (`ui/templates/ui/base.html:23`) suppresses the HTMX indicator stylesheet via `includeIndicatorStyles: false`.
- **CSRF**: Django's standard HTMX pattern — the `{% csrf_token %}` is present in forms, and HTMX sends the `X-CSRFToken` header automatically. No custom CSRF bypasses observed.
- **Tenant scoping in partials**: HTMX endpoints (`ui/views/*.py`) all gate on Django auth + org scoping before rendering partials. The `partial` query param is the only HTMX-specific addition; no tenant data leaks between partial renders.
- **Verdict**: HTMX partials are safe — no inline assets, proper CSRF, tenant scoping respected.

---

## 3. Findings by Severity

### High — None

### Medium — None

### Low — 1 accepted residual

| # | Area | Finding | Resolution |
|---|---|------|------------|
| L1 | `realtime/presence.py:22` | The `_presence_member` function has a development fallback `secret = "amalthea-dev-only"` for `SECRET_KEY` when not configured. In production with Redis, the real `SECRET_KEY` is used. The fallback is guarded by the deployment check `connection.vendor != "postgresql" or not _REDIS_AVAILABLE`, which routes to the in-memory store only for dev/test. | **Accepted residual**. The dev fallback is intentional and only affects local development. Production deployments must set `DJANGO_SECRET_KEY` (enforced by `amalthea/settings/prod.py`). No impact on production security. |

### Informational — None

---

## 4. Cross-Cutting Observations

- **Existence oracles**: Effectively eliminated. The 4401 (unauthenticated) and 4404 (forbidden/non-existent) WebSocket close codes ensure refused sockets never join groups. The search module returns empty querysets (`Q(pk__in=[])`) for org-less callers, never confirming entity existence via HTTP status.
- **Authz bypass risk**: Very low. All entry points (WebSocket consumers, API views, search) go through the canonical identity scoping functions (`resolve_case`, `resolve_alert`, `org_q`, `case_visible_q`). SuperAdmin cross-org paths are audited, not silently permitted.
- **WebSocket DoS**: Minor concern. `CaseConsumer.receive` (`realtime/consumers.py:122-150`) closes on `bytes_data` but does not enforce a max message size. A flood of large JSON payloads could consume bandwidth/processing, but the auth-before-accept gate and per-org room isolation limit the blast radius. No rate-limiting or size caps are implemented at the consumer level (acceptable for MVP; consider for hardening).
- **Log injection**: No user-supplied data is emitted in logs at the WebSocket/presence layer. The `events_after` and timeline functions produce structured data, not log output. The `alert_raw` HTTP endpoint returns raw payloads, but those are API responses, not log entries.
- **Secrets management**: The `SECRET_KEY` is used in the HMAC presence store (as expected). The dev fallback is the only risk surface; the pre-commit `secret-scan.sh` and CI secret scans prevent accidental commits of real keys. `amalthea/settings/prod.py` fails closed without a strong `DJANGO_SECRET_KEY` and non-empty `DJANGO_ALLOWED_HOSTS`.

---

## 5. Overall Recommendation

The realtime and search implementations for §6.16-fu1 + §6.16 P7 are **secure by design**. No code changes are required. The single accepted Low residual (dev-secret fallback) is expected and does not affect production security. All tenant isolation, authz, presence opacity, and existence-oracle mitigations are effectively implemented.

**Report written to:** `docs/reviews/REVIEW-2026-11-26-section6-realtime-and-search.md`
