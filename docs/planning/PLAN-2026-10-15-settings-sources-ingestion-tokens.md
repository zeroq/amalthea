# PLAN — Settings → Sources: per-organisation ingestion-token management

- **Date:** 2026-10-15
- **Status:** APPROVED (2026-10-15) — decisions locked in §12; ready for implementation
- **Author:** planner
- **Related:** `docs/decisions/ADR-004-tenant-isolation.md` (Q6 token routing), `docs/spec/identity-auth.md`, `docs/spec/api.md` §Ingest, `docs/spec/data-model.md` (IngestionToken/IngestionSource), `TODO.md` §6.13 / §6.16 P4, `TODO.md` §10.8

---

## 1. Summary

Amalthea can authenticate an inbound webhook and route it to the right tenant today, but
**there is no way to mint the credential that does the routing.** `IngestionToken.create_token()`
exists (`identity/models.py:202`) and is called only by tests and a data migration; the Django admin
is read-mostly with add disabled (`identity/admin.py:71`). The README tells operators to use
`manage.py shell` (`README.md:58-62`).

This plan adds the missing **operator surface**, under **Settings → Sources**, where:

- an **`admin`** manages tokens **only for their own organisation**;
- a **`superAdmin`** manages tokens for **any** organisation;
- the plaintext secret is **shown once** at creation and is never recoverable, logged, or rendered again.

It also introduces the **Settings shell** (a landing page + sub-navigation) that the current Settings
area lacks, because the rail links only to Organisations and the Users page is not reachable from any
link at all (`ui/templates/ui/base.html:97-104`).

## 2. Goals

- G1 — Mint an `IngestionToken` from the UI, scoped to a source **and** an organisation, with the
  plaintext displayed **exactly once**.
- G2 — Revoke an `IngestionToken` from the UI (sets `revoked_at`), with a confirm modal.
- G3 — **Cross-org invisibility:** a user must never see, create, or revoke a token belonging to
  another organisation (unless they are `superAdmin`).
- G4 — Audit every token create/revoke (`AuditLog`), without ever recording the secret.
- G5 — Fail-closed authorisation: `admin`/`superAdmin` only; `analyst`/`viewer` get a uniform 404.
- G6 — Give Settings a real shell (landing page + sub-nav) so Sources has a discoverable home next to
  the existing Organisations page.

## 3. Non-goals

- **N1 — No source CRUD.** Sources stay global and are not created/edited here (user decision 3).
  `IngestionSource` is unchanged: no `Organisation` FK, no per-org uniqueness.
- **N2 — No HMAC / request signing.** The webhook remains a bearer shared secret in a header
  (user decision 4). No signature verification, no nonce/timestamp, no replay protection.
- **N3 — No token expiry.** `expires_at` is not added (see Open Question Q4).
- **N4 — No DRF endpoints.** This is UI-only; `test_authz.py`'s API matrix is untouched.
- **N5 — No change to the webhook contract** (`docs/spec/api.md` §Ingest) or to `IngestionSource`.
- **N6 — Not a "sources" inventory rework.** The existing analyst-facing `/sources` page
  (`ui-sources-list`) stays as-is except for the optional stale-column fix (Open Question Q5).

## 4. Architecture

### 4a. Stack / placement (no new technology)

Server-rendered Django templates + the existing `<dialog>` modal component + plain
POST/redirect-after-POST + Django messages. No HTMX is required (the Settings area uses plain POSTs);
no new JS framework. Dark theme and the `?` cheat-sheet stay authoritative.

Rationale: this is an administrative form surface with one irreversible output (the secret). The
existing `settings_organisations` / `settings_users` pattern already solves the same problem class
(superAdmin CRUD with modals, CSRF, audit) and is pinned by `test_settings_ui.py`. Reusing it keeps
the diff small and the tests honest.

**ADR reference:** `ADR-004-tenant-isolation.md` Q6 ("Ingest is token-routed … a token binds a source
to exactly one org; organisation is *never* taken from the request body"). This plan implements the
*issuance* half of that decision, which was specified but never built. **No ADR amendment is needed**:
the design adds a UI over an existing, ratified model.

### 4b. Authorisation model (the core of the design)

`User.role` is one of `superAdmin | admin | analyst | viewer` (`core/enums.py:117-122`). Two existing
helpers do exactly what we need and must be reused rather than re-implemented:

- `identity.scoping.is_admin(user)` — `role in ("admin", "superAdmin")` (deny-only; `scoping.py:66`).
- `identity.scoping._is_superadmin(user)` — `role == "superAdmin"` (`scoping.py:39`).

| Caller | Can view | Can create for | Can revoke |
|---|---|---|---|
| `superAdmin` | every org's tokens | any org (must pick one) | any token |
| `admin` | **own org only** | **own org only** | **own org only** |
| `analyst` | ✗ 404 | ✗ 404 | ✗ 404 |
| `viewer` | ✗ 404 (`not_viewer`) | ✗ 404 | ✗ 404 |

Two rules make this safe:

1. **Authorisation is role-based; visibility is org-based.** `is_admin` answers "may this caller enter
   the view?"; `user.org_id` answers "which rows may they see?". A non-superAdmin `admin` never passes
   an org parameter — their org is taken from `request.user.org_id`, never from the request. This is
   the ADR-004 rule ("organisation is never taken from the request") applied to the admin surface.
2. **Cross-org access is a uniform 404**, identical in shape to the case-scoping and playbook gates
   (see `not_viewer`'s docstring, `ui/views.py:160-172`): an `admin` targeting another org's token must
   not be able to tell "does not exist" from "not yours".

For the `superAdmin`, the org to write into is an **explicit, required** selection (`<select>`), which
is the one place a request parameter may name an organisation — and it is authorised by role.

### 4c. Abandoned alternative

**Role-dependent rail href** (Settings link → `/settings/organisations` for superAdmin, `/settings/sources`
for admin) was rejected: `test_ui_nav.py` extracts one `url_name` per rail `<li>` and asserts
rail↔`ROUTES` parity (`test_ui_nav.py:92-103`), so a two-URL nav item breaks the parser and the
invariant. A single landing route is also the honest reading of "Settings menu with submenu items".

## 5. Data model

**No schema change is required.** `IngestionToken` already carries everything the page needs
(`identity/models.py:165-232`): `source` (FK, CASCADE), `organisation` (FK, PROTECT), `name`, `prefix`
(unique, 12 chars), `secret_hash` (Argon2), `revoked_at`, `last_used_at`.

**Schema change (1 field, approved Q1):** add
`created_by = FK(User, on_delete=SET_NULL, null=True, blank=True, related_name="+")` so the list and the
audit trail can answer *who* issued a token. Additive and nullable — no backfill, no data migration.
Migration in `identity/migrations/`.

**Out of scope (Q4):** `expires_at` is *not* added. Noted only because "credentials that outlive their
owner's tenure" is a real operational gap.

No changes to `IngestionSource`, `Alert`, or the webhook path.

## 6. API / interface contracts

**UI routes only** (no DRF). Added to the `# Settings` block in `ui/urls.py` (after line 208):

| Route name | Path | Method | Gate | Purpose |
|---|---|---|---|---|
| `ui-settings` | `/settings` | GET | `is_admin` | Settings landing (card grid) |
| `ui-settings-sources` | `/settings/sources` | GET | `is_admin` | Token list |
| `ui-settings-token-create` | `/settings/sources/token` | POST | `is_admin` | Mint a token |
| `ui-settings-token-revoke` | `/settings/sources/<uuid:token_id>/revoke` | POST | `is_admin` | Revoke a token |

**Naming collision avoided:** the existing analyst page keeps `ui-sources-list` / `/sources`
(`ui/urls.py:174`); the new admin routes are namespaced under `ui-settings-*` / `/settings/sources`.

**POST contracts** (both `application/x-www-form-urlencoded`, `{% csrf_token %}`, redirect-after-POST
except create):

- Create — `source` (slug or id), `organisation` (UUID; **required for superAdmin, ignored/injected for
  admin**), `name` (free text, ≤200). On success: **render** (200) the show-once modal with the
  plaintext; on failure: `messages.error` + redirect back.
- Revoke — no body fields beyond CSRF; `token_id` from the path. On success: `messages.success` +
  redirect to `/settings/sources`.

**Webhook contract: unchanged** (bearer in `X-Webhook-Secret` / `X-Api-Key` / `Authorization: Bearer`,
`ingest/views.py:114-152`). A token minted here works immediately; a token revoked here stops working.
This is the end-to-end acceptance criterion (AC-9).

## 7. UI / UX

### 7a. Settings shell (new)

- `/settings` — the Settings home, listing the sub-pages, **filtered by role**:
  - **Organisations** (superAdmin) — the existing `ui-settings-organisations` page. This is **not** a
    new page: Organisations management already exists and is only being linked from the new shell.
  - **Sources** (`admin` + `superAdmin`) — new.
- A **sub-nav tab strip** rendered on every Settings page (shared partial `ui/_settings_nav.html`) with
  `aria-current="page"` on the active tab. Tabs are role-filtered by the same predicate as the cards,
  and the tab list is built from **one iterable**, so adding a further tab later is a one-line change.

**Note — the orphaned Users page (flagged, not silently included).** `ui-settings-users`
(`ui/urls.py:198`, template `ui/settings_users.html`) is fully built, but **no template links to it**:
the only references in the codebase are its own templates' Back/Cancel/Edit links and the
`aria-current` condition in `base.html:99`. It is reachable only by typing the URL. The approved scope
is **two** sub-nav items (Organisations, Sources), so the Users page stays orphaned. See Q8 — adding it
as a third tab is a one-line change if wanted.
- Rail: the Settings `<li>` (`base.html:97-104`) changes its gate from `role == "superAdmin"` to
  `is_admin`, and its `href` from `ui-settings-organisations` to the new `ui-settings`.
  The `,` shortcut is retargeted to `/settings` in `keys.js` (`ROUTES`, line 50) and the cheat-sheet.
- Keyboard: the `?` cheat-sheet row label updates to the new target; the modals open on Enter and close
  on Esc (existing `modal.js` behaviour).

### 7b. Token list — `/settings/sources`

A table grouped by organisation, modelled on `settings_organisations.html`:

| Column | Source | Notes |
|---|---|---|
| Source | `token.source.name` + `slug` | link is out of scope (N1) |
| Organisation | `token.organisation.name` | **superAdmin only**; admins see one org |
| Name | `token.name` | |
| Prefix | `token.prefix` | safe: 12-char non-secret discriminator (already read-only in admin) |
| Created | `token.created_at` | |
| Last used | `token.last_used_at` or "never" | |
| Issued by | `token.created_by` or "—" | only if Q1 is approved |
| Status / action | Active **or** Revoked (with `revoked_at`) + "Revoke" button | |

- Empty state: "No ingestion tokens yet — create one to let a source send alerts."
- `secret_hash` is **never** selected into the template context (enforce by explicit `.values(...)` or
  `defer()`, not by template discipline alone).
- Above the table: an explanation block naming the exact endpoint and header, so the page answers
  "where do I point the tool?":
  `POST /api/v1/alerts/webhook/<slug>/` with `X-Webhook-Secret: <token>`.

### 7c. Create modal (show-once)

Trigger: a primary "New token" button. The `admin` form has **no org field**; the `superAdmin` form has
a **required** organisation `<select>`.

Two-step flow, because the secret must be revealed after a POST without being re-renderable:

1. `POST /settings/sources/token` → on success the view **renders** a show-once modal (HTTP 200) —
   deliberately *not* a redirect, since a redirect would either lose the secret or put it in a URL.
2. The modal shows the plaintext in a `<code>` block with a **Copy** button, a prominent
   "you will not see this again" warning, and a Close action.

Implementation constraints:

- Plaintext is passed **only** in the render context of that one response. It must **not** be written
  to the session, a message, a log, the audit row, or any cache.
- Reloading `/settings/sources` must never re-show it (it is not persisted) — asserted by AC-6.
- Copy uses `navigator.clipboard.writeText` from a **static file** (no inline `<script>` / `onclick`:
  `test_ui_inline_assets.py` fails on either), with a graceful fallback that selects the text and tells
  the user to copy manually (the Clipboard API needs a secure context; plain-HTTP deployments will hit
  the fallback).
- Modal colours/labels follow the dark theme; the warning pairs colour with text (never colour alone).

### 7d. Revoke flow

Reuse `ui/_modal.html` with a body partial (`_settings_token_revoke_form.html`) and
`confirm_class="btn-err"`, exactly like the existing org/user delete modals
(`settings_organisations.html:37-43`). A revoked token cannot be un-revoked from the UI (no
"reactivate"); mint a new one instead — stated in the modal copy.

## 8. Phases, tasks and acceptance criteria

Each phase is independently shippable and green. ACs are per task.

### Phase 1 — Settings shell

- **T1.1** Add `ui-settings` `/settings` landing view + template (card grid, role-filtered).
- **T1.2** Add `ui/_settings_nav.html` tab strip partial; include it on all five Settings pages.
- **T1.3** Add a UI helper `_require_admin_ui(request)` beside `_require_superadmin_ui`
  (`ui/views.py:3410`), returning the uniform 404 for non-admins. Existing pages keep
  `_require_superadmin_ui`.
- **T1.4** Retarget rail Settings `<li>` (`base.html:97-104`) to `ui-settings` and gate on `is_admin`.
- **T1.5** Retarget `,` in `keys.js` `ROUTES` (line 50) and the `CHEATSHEET` label; update the
  cheat-sheet table row in `base.html:169-171`.
- **T1.6** Update the frozen test literals that pin the old target (see §9).

**ACs**
- AC-1 `GET /settings` → 200 for `admin` and `superAdmin`; 404 for `analyst` and `viewer`.
- AC-2 The navigate-to-Settings rail item and the `,` shortcut both reach `/settings`; the cheat-sheet
  row, `CHEATSHEET` array and `ROUTES` map agree (the existing parity test passes unchanged in intent).
- AC-3 An `admin` sees Sources in the sub-nav but **not** Organisations; a `superAdmin` sees both.
- AC-4 No regression for the existing Settings pages: `/settings/organisations` and `/settings/users`
  still return 200 for a `superAdmin`, and the sub-nav marks the active tab with `aria-current`.

### Phase 2 — Token list (read surface, cross-org invisibility)

- **T2.0** Add `IngestionToken.created_by` (Q1) + migration; set it in the create path (T3.1).
- **T2.1** Add `ui-settings-sources` view: `@not_viewer @login_required`, `_require_admin_ui`;
  queryset = `IngestionToken.objects.select_related("source", "organisation")`, then
  `if not superadmin: qs = qs.filter(organisation_id=request.user.org_id)`.
- **T2.2** Add the template `settings_sources.html` (table per §7b, empty state, endpoint/header hint).
- **T2.3** Ensure `secret_hash` is never placed in the context.

**ACs**
- AC-5 A `superAdmin` sees tokens for all orgs, each labelled with its organisation; an `admin` sees only
  their own org's tokens, with no organisation column rendered (or a single fixed value).
- AC-6 **The response body never contains `secret_hash` nor any substring of a stored hash** — asserted
  by asserting the raw hash value is absent from the rendered HTML for a seeded token.
- AC-7 `analyst` and `viewer` → 404 on `/settings/sources`.

### Phase 3 — Create token (show-once)

- **T3.1** Add `ui-settings-token-create` POST view: `@not_viewer @login_required @require_POST`,
  `_require_admin_ui`; resolve `source` by slug; resolve org (admin → `request.user.org_id`;
  superAdmin → required `organisation` param, validated against `Organisation`); validate `name`;
  call `IngestionToken.create_token(...)`.
- **T3.2** Write the audit row (§Security) **before** rendering, sharing the transaction.
- **T3.3** Render the show-once modal (200) with the plaintext; add
  `ui/_settings_token_created.html` + the create form partial.
- **T3.4** Add `ui/static/ui/clipboard.js` (static, delegated listener) for the Copy button.
- **T3.5** Handle the `prefix` uniqueness collision with a bounded retry (the column is `unique=True`;
  do not surface an `IntegrityError` as a 500).

**ACs**
- AC-8 An `admin` creating a token gets exactly one 200 response containing a plaintext secret; the
  `IngestionToken` row stores only the hash and a matching 12-char `prefix`, and `created_by` is the
  acting user.
- AC-9 **End-to-end:** the plaintext minted in AC-8, sent as `X-Webhook-Secret` to
  `POST /api/v1/alerts/webhook/<slug>/`, returns **201** and stamps the alert with the token's org.
- AC-10 Reloading `/settings/sources` (and any GET) never contains the plaintext — the secret is
  unrecoverable after the single response.
- AC-11 An `admin` cannot create a token for another organisation: passing another org's UUID is
  ignored (the token lands in the admin's own org) or rejected — **never** created in the other org.
- AC-12 A `superAdmin` must name an organisation; omitting it is a validation error, not an org-less token.
- AC-13 An audit row exists with `action="ingestion_token.created"`, `target_type="IngestionToken"`,
  `target_id=<token uuid>`, `target_org=<token org>`, and **no** secret material.

### Phase 4 — Revoke token

- **T4.1** Add `ui-settings-token-revoke` POST view: `@not_viewer @login_required @require_POST`,
  `_require_admin_ui`; resolve the token scoped to the caller (superAdmin → any; admin →
  `organisation_id=request.user.org_id`), else uniform 404; set `revoked_at=timezone.now()`; audit.
- **T4.2** Add the confirm modal + body partial; wire a "Revoke" button per active row.

**ACs**
- AC-14 Revoking sets `revoked_at`, the row shows Revoked, and the action is idempotent-safe (a second
  revoke is a no-op success or a 404, never a 500).
- AC-15 **End-to-end:** after revoking, the same `X-Webhook-Secret` returns **401**
  (`ingest/views.py:114-152` path).
- AC-16 An `admin` revoking another org's token → **404**, and that token remains active.
- AC-17 An audit row `action="ingestion_token.revoked"` is written with the same shape as AC-13.

### Phase 5 — Gates and documentation

- **T5.1** Update the enumerated/enumerated-by-test lists in §9.
- **T5.2** Update `docs/spec/identity-auth.md` (add the "Ingestion tokens" section that `TODO.md` §10.8
  asks for) and `README.md:58-62` (the "no Settings UI" sentence is now false).
- **T5.3** Fix the stale `Secret: required/open` column on the analyst `/sources` page (approved Q5):
  `sources_list.html:28` reads the legacy `IngestionSource.webhook_secret_hash`, which the receiver no
  longer consults, so a token-protected source currently renders "open". Drive it from the presence of
  a non-revoked `IngestionToken` instead, and add a test that bites (a source with only a token must
  not render "open").

**ACs**
- AC-18 `ruff check`, `ruff format --check`, `makemigrations --check` clean; full suite green in the
  pre-commit hook.
- AC-19 No UI route is reachable by a `viewer` that mutates state (`test_viewer_readonly_ui.py` passes
  with the new routes auto-discovered).
- AC-20 Docs no longer claim there is no token UI.

## 9. Testing plan (and the frozen lists that must change)

**Existing tests that will need edits — call these out in the brief, they are the risk surface:**

| File | Why | Detail |
|---|---|---|
| `tests/conformance/test_settings_ui.py` | `TestSettingsNav` (82-140) pins the old nav target; `TestKeyboardShortcuts` (662-678) pins the **literal** `'",": "/settings/organisations"'` (670) and `'label: "Settings (Organisations)"'` (677) | update literals; add an `admin`-sees-Sources test |
| `tests/conformance/test_ui_nav.py` | rail↔`ROUTES`↔cheat-sheet parity (92-122) | keep consistent; the `,` row stays the sole superAdmin-only row **only if** admin also gets `,` — it does, so the `{% if superAdmin %}` wrapper on that cheat-sheet row must be revisited |
| `tests/conformance/test_ui_urls_smoke.py` | hard-coded `superadmin_only` set (126-131) | decide whether the new routes belong (they 404 for *analyst*, not for *admin*) |
| `tests/conformance/test_tenant_scoping.py` | `_UI_GLOBAL_READ_ROUTES` (755-782), `_UI_WRITE_ONLY_ROUTES` (829-863) | the new POST route must be added to write-only; the **read** route is genuinely org-scoped for `admin` yet global for `superAdmin` — reconcile with the suite's model (see Risk R4) |
| `tests/conformance/test_ui_loop.py` | `test_the_rail_links_every_page` (209-218) hard-codes rail targets | add the Settings/Sources entry if it enumerates it |
| `tests/conformance/test_ui_icons.py` | (175-184) | only if a new `fa-*` icon is used in `base.html` |

**New tests (biting, per the house standard in `TODO.md` §7.5):**

1. Show-once: POST create returns the plaintext; a subsequent GET of the list does **not** (AC-8/AC-10).
2. Hash never leaks: seed a token, assert `secret_hash` and the plaintext are absent from the list HTML
   (AC-6).
3. Cross-org invisibility (the user's #1 requirement): org A admin's list contains no org B token; a
   direct revoke of an org B token id → 404 and the token is still active (AC-5/AC-16).
4. Role gate: `analyst` and `viewer` → 404 on every new route (AC-7, AC-19).
5. Audit shape for create and revoke, asserting **no secret material** in the row (AC-13, AC-17).
6. End-to-end: create via UI → webhook 201 → revoke via UI → webhook 401 (AC-9, AC-15). This is the
   criterion that proves the whole feature is real rather than a pretty table.
7. CSV/error paths: duplicate/blank name, unknown source slug, superAdmin missing org (AC-12).

Assertions must be **positive and non-vacuous** (assert the actual rendered row / the actual 401),
because a "page renders" check would pass on an empty table.

## 10. Security

- **Secret handling.** Only `IngestionToken.create_token()` mints secrets. The plaintext is returned to
  exactly one response and never stored, logged, messaged, cached, or audited. `secret_hash` is excluded
  from every context. `prefix` (12 chars, non-secret) is the only token identifier shown.
- **Authorisation is fail-closed.** `@login_required` + `@not_viewer` + `_require_admin_ui` on every
  route; `analyst`/`viewer` get the uniform 404. Mutating views are POST-only.
- **Cross-org writes are impossible by construction:** the admin path never reads an org from the
  request; the superAdmin path requires an explicit, role-authorised selection.
- **CSRF** is enforced (session-authenticated forms; `{% csrf_token %}` in every form). Unlike the
  webhook, these are **not** `@csrf_exempt`.
- **Audit:** `record_ingestion_token_change()` to be added to `identity/audit.py`, modelled on
  `record_organisation_change` (`identity/audit.py:136-166`) — `action` ∈
  `{"ingestion_token.created", "ingestion_token.revoked"}`, `target_type="IngestionToken"`,
  `target_id=<uuid>`, `target_org=<token.organisation>`, `actor`, `actor_org`, `ip`. The audit row
  carries **no** secret. The write shares the mutation's transaction.
- **AGENTS.md §5 compliance:** no secret in code, tests, fixtures, docs, commit messages, or the
  cheat-sheet modal; `scripts/secret-scan.sh --staged/--tracked/--history` must stay clean; the test
  fixtures must use obviously-fake values, never realistic tokens.
- **No inline JS/CSS/`on*=`** (CSP rule, `test_ui_inline_assets.py`); clipboard logic lives in a static file.
- **Rate limiting:** not applied — these are session-authenticated admin actions, not an anonymous
  surface. Explicitly out of scope.

## 11. Risks

- **R1 — Frozen-test churn.** Retargeting `,` and the Settings nav breaks at least two pinned string
  literals. Mitigation: do the retarget and the literal updates in the same commit (Phase 1) and run
  the full suite; do not let the hook go red.
- **R2 — Settings-shell scope creep.** A landing page + sub-nav is more than "one page". Mitigation:
  Phase 1 is deliberately minimal (cards + tab strip, no new JS); Phase 2+ does not depend on it.
- **R3 — Show-once UX.** If the operator closes the modal or navigates away, the secret is gone.
  Accepted and documented; the UI must say so plainly, and revoke+recreate is the recovery path.
- **R4 — Tenant-suite model mismatch.** The read page is org-scoped for `admin` but global for
  `superAdmin`, which may not fit `test_tenant_scoping.py`'s single-behaviour-per-route assumption.
  Mitigation: resolve during T5.1; if the suite cannot express it, record the deviation explicitly
  rather than weakening the test.
- **R5 — `prefix` collision.** `prefix` is `unique=True`; a collision would raise `IntegrityError`.
  Mitigation: bounded retry in the create path (T3.5).
- **R6 — Clipboard API availability.** Requires a secure context; plain-HTTP deployments fall back to
  manual copy. Mitigation: the fallback is part of T3.4, not an afterthought.
- **R7 — Confusion with the existing `/sources` page.** Two "Sources" surfaces (analyst inventory vs
  admin token management) can confuse. Mitigation: distinct routes/names, and sub-nav context makes the
  admin page's scope obvious.

## 12. Decisions (resolved 2026-10-15)

All questions below were decided by the product owner on 2026-10-15; the plan body above reflects them.

| # | Question | Decision |
|---|---|---|
| Q1 | `created_by` FK on `IngestionToken`? | **Yes** — add it (nullable `SET_NULL`, no backfill). |
| Q2 | Settings shell shape? | **`/settings` home + role-filtered sub-nav tab strip.** |
| Q3 | Retarget the `,` shortcut to `/settings`? | **Yes** — including the frozen-literal edits in `test_settings_ui.py`. |
| Q4 | Token expiry (`expires_at`)? | **Out of scope.** |
| Q5 | Fold in the stale `Secret: required/open` fix on the analyst `/sources` page? | **Yes** — fix it in this plan (T5.3). |
| Q6 | Token `name` required? | **Yes** — required free text, ≤200 chars. |
| Q7 | Update `README.md` + add the `ingestion-auth` spec section? | **Yes** — T5.2. |
| Q8 | Sub-nav item count — Organisations + Sources (2), or add the orphaned Users page as a third tab? | **2 items** (Organisations, Sources). Users stays orphaned; adding it later is one line. |

### Q8 in detail (the only item left open)

The approved sub-nav is **Organisations + Sources**. Separately, `ui-settings-users` is fully built but
unreachable from any link (evidence in §7a). Options:

- **(a) Leave as-is** (current decision): the Users page stays reachable only by URL. The tab list is
  built from one iterable, so adding it later is a one-line change plus the smoke-test/role updates.
- **(b) Add Users as a third tab** now: fixes the orphan, costs one iterable entry plus the matching
  role-visibility and conformance-list updates.

Recommendation: **(b)**, but it is deliberately not assumed — the plan implements (a).

### Open on implementation (minor, no design impact)

- The Settings home is a card grid (§7a); if the owner prefers the rail item to link straight to
  `/settings/sources` for an `admin` and `/settings/organisations` for a `superAdmin`, that is a
  Phase 1 variant but it breaks `test_ui_nav.py`'s one-`url_name`-per-rail-item parity assertion (§4c).


## 13. Implementation discovery (2026-10-15) — what the timed-out subagent left and what was fixed

The uncommitted tree at the time of verification already contained substantial parts of the feature (from a `django-backend` subagent that timed out with a 504 idle). The remaining gaps were (a) the new biting tests (B10), and (b) a critical context-name mismatch in the show-once render.

### 13.1 The show-once context-name mismatch (real bug, now fixed)

`ui/templates/ui/_settings_token_created.html` renders:
- `<code id="token-plaintext">{{ token_plaintext }}</code>` (three times in the template)
- `<code>{{ webhook_path }}</code>` (twice)

The `settings_token_create` view (BRIEF §2 B7) was passing:
```python
_base_context(
    request,
    plaintext=plaintext,
    token_name=token.name,
    token_prefix=token.prefix,
)
```
with **no** `webhook_path`. As a result, the modal rendered **an empty secret and an empty endpoint** — the entire point of "show once" was broken.

**Fix** (`ui/views.py:4015-4030`): pass `token_plaintext=plaintext` and `webhook_path=f"/api/v1/alerts/webhook/{source.slug}/"`. The unused `token_name`/`token_prefix` were dropped (kept minimal).

The fix was caught and proven by the new tests (re-introducing the bug made `TestShowOnce::test_show_once_displays_the_plaintext_then_persists_only_a_hash`, `TestShowOnce::test_show_once_modal_names_the_concrete_webhook_endpoint`, `TestAuditTrail::test_create_and_revoke_are_audited_without_secrets`, and `TestEndToEnd::test_mint_then_webhook_then_revoke_then_rejected` all fail).

### 13.2 New conformance tests

`tests/conformance/test_settings_ingestion_tokens.py` (13 tests, all passing) now back AC-5–AC-17:
- Show-once: plaintext shown, persisted only as a hash, list page never re-renders it; modal shows the concrete webhook endpoint (regression guard).
- Hash secrecy: `secret_hash` never appears in the list HTML.
- Cross-org isolation: admin list excludes other org's tokens; an admin POST to revoke another org's token returns 404 and leaves it unrevoked.
- Role gates: `analyst` and `viewer` receive 404 on `/settings`, `/settings/sources`, token-create POST, and token-revoke POST; no side effects.
- Audit trail: `ingestion_token.created` and `ingestion_token.revoked` written with correct target/actor/org; secret material absent from serialized audit values.
- SuperAdmin: POST without `organisation` redirects back (no token created); superAdmin can mint for any chosen org; superAdmin's list includes both orgs' tokens.
- Admin org binding: an admin cannot mint for another org — even if `organisation=<other>` is POSTed, the token's org is forced to the admin's own org.
- End-to-end: mint in UI → plaintext read → webhook POST with `X-Webhook-Secret` returns 201 and stamps the alert with the admin's org → revoke in UI → same secret rejected with 401 (type `unauthorized`).

### 13.3 T5.3 (Q5) — already satisfied

- `ui/views.py:sources_list` annotates `has_token = Exists(IngestionToken.objects.filter(source=OuterRef("pk"), revoked_at__isnull=True))` (lines ~1492–1508 in the current tree).
- `ui/templates/ui/sources_list.html:28` reads `{% if source.has_token %}`.
- `tests/conformance/test_ui_loop.py::test_the_sources_page_shows_whether_a_secret_is_required` was updated to the token-driven behaviour (no legacy `webhook_secret_hash` assumptions) with biting assertions.

### 13.4 Retry robustness (GAP 3)

`ui/views.py:3983-4014` now imports `IntegrityError` from `django.db` (vendor-neutral) and retries on prefix collisions (3 attempts). The comment clarifies the only unique constraint on `IngestionToken` is `prefix`. This is a robustness improvement over string-matching Postgres error messages.

### 13.5 Test tenancy allowance (deviation note remains)

`tests/conformance/test_tenant_scoping.py` retains its explanatory deviation comment placing `ui-settings` and `ui-settings-sources` into `_UI_GLOBAL_READ_ROUTES` (hybrid scoping: org-scoped for admin, global-read surface for superAdmin). The dedicated coverage above makes this intentional and test-backed.

### 13.6 Gate

Full suite: **1904 passed / 3 skipped** (≈108s). `ruff check .` and `ruff format --check .` clean. `makemigrations --check --dry-run` reports "No changes detected".
