# Security Audit — Ingestion-Token Lifecycle (§10a pause/resume, §10b delete inactive, §10c last-token feed reaping)

**Date:** 2026-10-19  
**Auditor:** security-auditor (read-only; no code/tests/docs modified — this report is the only artefact written)  
**Scope:** The ingestion-token lifecycle wave shipped in commits leading to HEAD `44282f84d94a1f08251f18d9fe5382b9b8ef6424`, comprising three planning documents:
- `PLAN-2026-10-17-ingestion-token-lifecycle.md` (§10a/§10b, AC-1…AC-12)
- `PLAN-2026-10-17-feed-reap-on-last-token.md` (§10c, AC-1…AC-10)
- Provisional self-verification at `VERIFY-2026-10-17-ingestion-token-lifecycle.md` (22/22 ACs Met, **not independent**)

**Files read (primary):**
- `identity/models.py` (`IngestionToken`, `verify()`, properties)
- `ingest/views.py` (`_authenticate_token`, webhook receiver)
- `ui/views.py` (`_org_scoped_token`, `settings_token_revoke`, `settings_token_deactivate`, `settings_token_activate`, `settings_token_delete`, `_settings_sources_context`)
- `identity/audit.py` (`record_ingestion_token_change`, `record_ingestion_source_deleted`)
- `alerts/models.py` (`Alert.ingestion_source` FK)
- `ingest/models.py` (`IngestionSource`)
- Templates: `settings_sources.html`, `_settings_token_delete_form.html`, `_settings_token_deactivate_form.html`, `_settings_token_revoke_form.html`, `_settings_token_created.html`, `source_detail.html`, `sources_list.html`
- Conformance tests: `tests/conformance/test_settings_ingestion_tokens.py` (50 passed)

**Method:** Source review against the five required scope areas; targeted test run (`pytest tests/conformance/test_settings_ingestion_tokens.py -q` → **50 passed**); static analysis of all token-resolution and token-mutation paths.

---

## Verdict

**PASS** — No Critical or High findings. The implementation correctly enforces the two independent refusal gates for paused tokens, org-scopes all mutation routes uniformly, orders audit writes before deletes inside a single transaction, and keeps secret material out of logs, templates (beyond the one-time modal), and audit rows. Two Medium findings and three Low/Info observations are documented below for hardening.

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| **Medium** | **2** |
| **Low** | **2** |
| **Info** | **2** |

---

## Findings

| # | Severity | Area | Summary |
|---|----------|------|---------|
| **M1** | **Medium** | Concurrency / feed reaping | `settings_token_delete` evaluates `source.tokens.exists()` after `token.delete()` inside `transaction.atomic()` but **without `select_for_update`**. On Postgres, a concurrent token create on the same feed can race and be silently reaped. |
| **M2** | **Medium** | Prefix collision / enumeration | The 12-character `prefix` is unique but only 72 bits of entropy; a determined attacker with write access to the token table (e.g., compromised admin session) could force a collision via the 3-retry loop, though practical risk is low. |
| **L1** | **Low** | Admin UI `list_display` | `IngestionTokenAdmin.list_display` omits `deactivated_at`; paused tokens appear as "Active" in Django admin (read-only surface, but misleading). |
| **L2** | **Low** | Webhook throttle key includes token prefix | `_throttled` builds a per-token rate-limit key `wh:token:{prefix}:{window}`. The prefix is not secret, but including it in a cache key leaks token existence to a cache operator. |
| **I1** | **Info** | `verify()` constant-time property | `IngestionToken.verify()` returns `False` early for revoked/deactivated tokens *before* the Argon2 verify. This is intentional (uniform 401) and timing-safe because the early exit is on public state, not secret comparison. |
| **I2** | **Info** | Legacy plaintext fallback in `_verify_secret` | The fallback `constant_time_compare(plaintext, hash_)` handles pre-Argon2 rows (migration 0006). No live rows use it; kept for migration safety. |

---

### M1 — Medium — Feed reaping race on concurrent token creation (Postgres only)

**Where**  
`ui/views.py:5199-5212` — `settings_token_delete`:

```python
with transaction.atomic():
    record_ingestion_token_change(...)
    token.delete()
    # After the token is gone: a feed with no tokens left has nothing to do and goes with it.
    source_reaped = not source.tokens.exists()
    if source_reaped:
        record_ingestion_source_deleted(...)
        source.delete()
```

**Impact**  
On Postgres (production), two concurrent requests deleting the last two tokens of the same feed can both observe `source.tokens.exists() == False` after their respective `token.delete()`, causing **both** to reap the feed. The second `source.delete()` is a no-op (row already gone), but the **audit row for the second reap is still written** with a stale `target_id`, and the second token's deletion silently reaps a feed that *already had a new token created by a third concurrent request*.

Sequence that loses a token:
1. Feed `F` has tokens `T1`, `T2` (both inactive).
2. Request A deletes `T1` → `token.delete()` → `source.tokens.exists()` sees `T2` → no reap.
3. Request B deletes `T2` → `token.delete()` → `source.tokens.exists()` sees **none** → reaps `F`.
4. **Concurrently**, Request C creates `T3` on `F` (via `settings_token_create` which does `get_or_create` on the source).
5. Request B commits: `F` deleted, `T3` cascade-deleted (FK `IngestionToken.source` is `CASCADE`), **`T3` disappears without audit**.

The plan D4 explicitly chose "emptiness evaluated *after* the token delete, inside the same transaction" to avoid a pre-check race, but **did not address the post-delete race** where a new token is created *during* the transaction.

**Verifiable only on Postgres** — SQLite's single-writer mode cannot reproduce this; the current test suite (SQLite) passes.

**Evidence**  
- `identity/models.py:193-199`: `source = ForeignKey(..., on_delete=models.CASCADE)`
- `ui/views.py:4954-4965`: `settings_token_create` uses `get_or_create` on `IngestionSource` (org-scoped) — a concurrent create on the same slug reuses the source row.
- `ui/views.py:5208`: `source.tokens.exists()` is a fresh SELECT inside the transaction; no row lock on the source.

**Remediation**  
Add `select_for_update` on the source row before the emptiness check, so concurrent token creates block until the reap decision is committed:

```python
with transaction.atomic():
    record_ingestion_token_change(...)
    token.delete()
    # Lock the source row to serialise against concurrent token creates on the same feed
    source = IngestionSource.objects.select_for_update().get(pk=source.pk)
    source_reaped = not source.tokens.exists()
    if source_reaped:
        record_ingestion_source_deleted(...)
        source.delete()
```

Add a conformance test that simulates the race (two threads: one deleting last token, one creating a token on the same feed) and asserts the new token survives. This test requires Postgres; mark it `@pytest.mark.postgres`.

---

### M2 — Medium — 12-character prefix collision window (theoretical)

**Where**  
`identity/models.py:314-324` — `IngestionToken.create_token`:

```python
plaintext = secrets.token_urlsafe(32)  # ~43 chars, ~256 bits
prefix = plaintext[:12]  # 12 chars, ~72 bits (URL-safe base64 alphabet)
```

**Impact**  
The `prefix` column has a unique index. A collision on the first 12 characters of two independently generated `token_urlsafe(32)` strings has probability ≈ `2^-72` per pair — negligible for accidental collision. However, the **retry loop caps at 3 attempts** (`ui/views.py:4987-4991`):

```python
for attempt in range(max_retries):  # max_retries = 3
    try:
        with transaction.atomic():
            token, plaintext = IngestionToken.create_token(...)
    except IntegrityError:
        if attempt < max_retries - 1:
            continue
        messages.error(request, "Could not generate a unique token. Please try again.")
```

An attacker who can **write to the `identity_ingestion_token` table** (e.g., via a compromised admin session, SQL injection elsewhere, or direct DB access) could pre-populate prefixes to force the retry loop to exhaust and surface an error to the operator. This is a **denial-of-service on token creation**, not a secret leak.

The prefix is also used in the webhook throttle key (`wh:token:{prefix}:{window}` in `ingest/views.py:148`), so a collision would merge two tokens' rate-limit buckets — a minor availability impact.

**Evidence**  
- `identity/models.py:208`: `prefix = models.CharField(max_length=12, unique=True, db_index=True)`
- `ui/views.py:4987-4991`: 3-retry cap on `IntegrityError` (prefix collision only).

**Remediation**  
1. Increase `prefix` length to 16 characters (96 bits) — still fits in `CharField(12)`? No, would need migration. Acceptable as-is given the threat model (requires DB write access).
2. **Alternatively**, document the 3-retry cap as a DoS surface in `docs/spec/identity-auth.md` and monitor for `IntegrityError` spikes in production logs.
3. The throttle key leak (L2) is a stronger reason to avoid putting `prefix` in cache keys; see L2.

---

### L1 — Low — Django admin shows paused tokens as "Active"

**Where**  
`identity/admin.py:61`: `list_display = ("name", "organisation", "source", "prefix", "revoked_at", "last_used_at")`

**Impact**  
The admin list omits `deactivated_at`. A paused token (only `deactivated_at` set) appears with empty `revoked_at` and no visual indication of its paused state. Since the admin is read-only for tokens (`has_add_permission = False`), this is a **display bug only** — no security bypass. However, an operator relying on the admin for a quick audit could miss a paused credential.

**Remediation**  
Add `deactivated_at` to `list_display` and a boolean `is_active` property or method for a clear pill. Low priority; admin is not the primary management surface.

---

### L2 — Low — Token prefix in webhook throttle cache key

**Where**  
`ingest/views.py:147-148`:

```python
if token:
    keys.append((f"wh:token:{token.prefix}:{window}", settings.WEBHOOK_RATE_SOURCE_PER_MIN))
```

**Impact**  
The cache key includes the token's 12-char `prefix`. While the prefix is not secret (it is returned in the webhook response JSON at `ingest/views.py:264` and shown in the Settings UI), **cache introspection** (e.g., a compromised Redis instance, or a shared cache operator) reveals which token prefixes are actively receiving traffic. This is a **minor existence oracle** — it confirms a token is live and its approximate request rate.

The per-source and per-IP keys (`wh:src:{slug}`, `wh:ip:{ip}`) are already identifiable; the per-token key adds granularity but also identifiability.

**Remediation**  
Replace the prefix with a hashed variant in the cache key: `hashlib.sha256(token.prefix.encode()).hexdigest()[:16]`. The prefix itself is low-sensitivity, but defence-in-depth favors not writing it to shared cache. Alternatively, drop the per-token bucket — the per-source and per-IP limits already provide adequate DoS protection.

---

### I1 — Info — `verify()` early exit on public state is timing-safe

**Where**  
`identity/models.py:333-335`:

```python
if self.revoked_at is not None or self.deactivated_at is not None:
    return False
return _verify_secret(self.secret_hash, presented)
```

**Analysis**  
The early return checks **public column values** (`revoked_at`, `deactivated_at`), not the secret. The Argon2 `verify()` (in `_verify_secret`) is only reached for active tokens and is constant-time. The early exit does **not** create a timing side-channel on the secret — it creates a timing difference between "token exists but paused" vs "token exists and active", which is acceptable because the caller already knows the token prefix (public) and the webhook returns a uniform 401 either way.

**Verdict:** Correct by design.

---

### I2 — Info — Legacy plaintext fallback in `_verify_secret`

**Where**  
`identity/models.py:156-164`:

```python
def _verify_secret(hash_: str, plaintext: str) -> bool:
    try:
        _ph.verify(hash_, plaintext)
        return True
    except Exception:
        from django.utils.crypto import constant_time_compare

        return constant_time_compare(plaintext.encode("utf-8"), hash_.encode("utf-8"))
```

**Analysis**  
The `except Exception` catches Argon2's `VerifyMismatchError` *and* any other exception (e.g., malformed hash). The fallback does a constant-time compare against the stored `hash_` column — which for legacy rows **is the plaintext secret**. Migration `0006_create_default_org` adopted legacy `webhook_secret_hash` as `IngestionToken` rows with Argon2 hashes, so **no live row should have a plaintext hash**. The fallback is dead code for production but harmless (constant-time compare).

**Verdict:** Acceptable migration compat; no action needed.

---

## Explicit Checks Performed

| Check | Result | Evidence |
|-------|--------|----------|
| Two independent paused-token refusals (webhook queryset + `verify()`) | **PASS** | `ingest/views.py:121` filters `revoked_at__isnull=True, deactivated_at__isnull=True`; `identity/models.py:333` returns `False` if either is set. Both active, consistent. |
| No other token-resolution path exists | **PASS** | Grep shows only `_authenticate_token` (webhook) and `verify()` (model). DRF `ApiKeyAuthentication` uses `ApiKey` model, not `IngestionToken`. No middleware, management commands, or custom auth classes touch `IngestionToken`. |
| Org scope on all 4 mutation routes (`_org_scoped_token`) | **PASS** | `ui/views.py:5028-5044` shared helper; all 4 routes call it first. Non-superAdmin filters `organisation_id`; miss → uniform 404. |
| No route skips the helper / no GET mutates / CSRF on all | **PASS** | All 4 routes: `@not_viewer @login_required @require_POST` + `_require_admin_ui` + `_org_scoped_token`. Forms/modals include `{% csrf_token %}`. |
| SuperAdmin scoping deliberate, failed scope check inert | **PASS** | SuperAdmin bypasses org filter in helper; test `test_superadmin_can_pause_and_delete_another_orgs_token` confirms. Cross-org 404 returns before any write. |
| Last-token reap order: token delete → emptiness check → feed delete, single `atomic()` | **PASS** | `ui/views.py:5199-5212` order confirmed. `source.tokens.exists()` after `token.delete()`. |
| **Race on concurrent token create (M1)** | **FINDING M1** | No `select_for_update` on source; concurrent create can be cascade-deleted. Verifiable only on Postgres. |
| `Alert.ingestion_source` is `SET_NULL` (evidence survives) | **PASS** | `alerts/models.py:105-111`: `on_delete=models.SET_NULL, null=True`. |
| Other FKs to `IngestionSource`: only `IngestionToken.source` (CASCADE) | **PASS** | `identity/models.py:193-199`: CASCADE. No PROTECT that would 500. |
| `ingestion_token.deleted` audit written before `delete()`, same transaction | **PASS** | `ui/views.py:5200-5206`: `record_ingestion_token_change` then `token.delete()`. `target_id` survives (test `test_delete_removes_a_deactivated_token_and_audits_before_the_row_goes`). |
| `ingestion_source.deleted` audit written before `source.delete()`, same transaction | **PASS** | `ui/views.py:5210-5212`: `record_ingestion_source_deleted` then `source.delete()`. |
| No secret material in audit rows (`secret_hash`, `prefix`, plaintext) | **PASS** | `identity/audit.py:227-235` and `:282-290`: only `actor`, `actor_org`, `target_org`, `action`, `target_type`, `target_id`, `ip`. Test `test_delete_removes_a_deactivated_token_and_audits_before_the_row_goes` asserts `plaintext not in str(deleted.__dict__)`. |
| Plaintext shown once (create modal), never re-rendered, never in logs | **PASS** | `_settings_token_created.html:24` renders `{{ token_plaintext }}` only in show-once modal. `settings_token_create` returns 200 render, not redirect. `_settings_sources_context` defers `secret_hash`. Logs in `ingest/views.py:260-267` log `token.prefix` only. |
| Argon2 hash, constant-time verify, prefix lookup not enumerable | **PASS** | `identity/models.py:152-164`, `326-335`. Prefix unique index prevents enumeration via collision; verify only runs on prefix hit. |
| `deactivated_at` + `revoked_at` both set → `is_active=False`, `verify()=False`, display shows Revoked | **PASS** | `is_active` property (241-248), `verify()` (333), templates (55-66, 209-217) all give revoked precedence. |
| Cross-org 404 uniform (no existence oracle) | **PASS** | `_org_scoped_token` returns 404 for both missing and cross-org. Tests `test_cross_org_actions_are_404_and_inert` (3 actions × 2 plans). |
| Analyst/viewer locked out (404, no mutation) | **PASS** | `@not_viewer` (outermost) + `_require_admin_ui` (checks `is_admin`). Tests `test_non_admin_roles_are_locked_out` (6 cases). |

---

## What I Did Not Review

| Item | Reason |
|------|--------|
| Pre-auth webhook throttle (§10.10 TODO) | Explicitly out of scope for this wave; tracked separately. |
| Token rotation / grace period / expiry | Non-goal per Plan §3; separate decision. |
| API surface for token lifecycle (`/api/v1/*`) | Non-goal per Plan §3; UI-only routes. |
| `IngestionTokenAdmin` change/add permissions | Admin is read-only (`has_add_permission=False`); not a mutation surface. |
| WebSocket / realtime propagation of token state changes | Not implemented; UI relies on full page reload after POST. |
| Migration `0012` merge conflict with parallel §12.8 work | Documented in Plan A §5; not a security issue. |
| `IngestionSource.webhook_secret_hash` legacy column | Retired by migration 0006; no live code reads it. |

---

## Commands Run for Verification

```bash
# Targeted conformance tests for the ingestion-token surface
cd /Users/goebej/proggen/amalthea
.venv/bin/python -m pytest tests/conformance/test_settings_ingestion_tokens.py -q
# → 50 passed in 3.67s

# Secret scan (tracked files)
scripts/secret-scan.sh --tracked
# → clean (317 tracked files)

# Lint / type gates
ruff check .
ruff format --check .
.venv/bin/python -m mypy --strict .
# → all clean
```

---

## Final Integrity

```text
HEAD: 44282f84d94a1f08251f18d9fe5382b9b8ef6424
git status --porcelain: (only this review file)
```

```bash
$ git rev-parse HEAD
44282f84d94a1f08251f18d9fe5382b9b8ef6424
$ git status --porcelain
 docs/reviews/SECURITY-2026-10-19-ingestion-token-lifecycle.md
```

HEAD remains `44282f8`; the only working-tree change is this review file.

---

## Summary for the Team

The ingestion-token lifecycle wave is **secure as shipped**. The two-gate refusal for paused tokens (queryset + model) is correctly implemented and consistent. Org scoping is uniform and fails closed with 404. Audit trails survive deletion. Secrets are hashed with Argon2, shown once, and never leak into logs or audit rows.

**Two hardening items before next release:**
1. **M1** — Add `select_for_update` on the source row in `settings_token_delete` to serialise the reap decision against concurrent token creates (Postgres-only race).
2. **M2** — Consider increasing prefix length or documenting the 3-retry DoS window; also address L2 (prefix in cache key).

The Low/Info items are cosmetic or defence-in-depth and do not block release.
