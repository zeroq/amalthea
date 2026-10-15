# BRIEF — Settings → Sources (ingestion-token management)

- **Date:** 2026-10-15
- **Plan:** `docs/planning/PLAN-2026-10-15-settings-sources-ingestion-tokens.md` (APPROVED)
- **Agents:** `django-backend` (backend contract), `django-frontend` (templates/static)
- **Do NOT commit.** Leave the work uncommitted for review. No `--no-verify`.

---

## 0. Non-negotiables

1. **Strict file ownership** — do not edit files owned by the other agent:
   - backend owns: `identity/models.py`, `identity/migrations/`, `identity/audit.py`, `ui/views.py`,
     `ui/urls.py`, all `tests/`, `README.md`, `docs/spec/identity-auth.md`
   - frontend owns: `ui/templates/ui/**`, `ui/static/ui/**`
   - If you need a change on the other side, **report it** — do not make it.
2. **No inline `<script>` / `<style>` / `on*=`** — `tests/conformance/test_ui_inline_assets.py` fails on these.
3. **Never render `secret_hash`.** The plaintext is used in exactly one response and is never stored,
   logged, messaged, cached, or audited.
4. Full gate before reporting: `.venv/bin/python -m pytest -q` (~108s), `ruff check .`,
   `ruff format --check .`, `manage.py makemigrations --check --dry-run`.
5. Reuse `identity.scoping.is_admin` / `_is_superadmin`; never re-implement role logic.

---

## 1. Frozen contract (both sides code to this)

### Routes — `ui/urls.py` (add to the `# Settings (superAdmin only)` block, ~line 208)

| name | path | method |
|---|---|---|
| `ui-settings` | `settings` | GET |
| `ui-settings-sources` | `settings/sources` | GET |
| `ui-settings-token-create` | `settings/sources/token` | POST |
| `ui-settings-token-revoke` | `settings/sources/<uuid:token_id>/revoke` | POST |

Keep the existing `ui-sources-list` (`/sources`) and `ui-settings-organisations` untouched.

### Templates (frontend)

| File | Role |
|---|---|
| `ui/settings_home.html` | Settings home (role-filtered sub-page cards) |
| `ui/_settings_nav.html` | shared tab strip, included by every Settings page |
| `ui/settings_sources.html` | token list |
| `ui/_settings_token_create_form.html` | create modal **body** (uses `form="{{ form_id }}"`) |
| `ui/_settings_token_created.html` | **show-once** modal, rendered by the POST response |
| `ui/_settings_token_revoke_form.html` | revoke confirm modal body |

Modal shell: reuse `ui/_modal.html` exactly as `settings_organisations.html:37-43` does.

### Context contract for `settings_sources.html` (backend supplies, frontend consumes)

```
tokens            list of IngestionToken, each with: name, prefix, created_at,
                  last_used_at, revoked_at, created_by (User|None),
                  .source (has .name, .slug), .organisation (has .name)
show_org_column   bool   # True only for superAdmin
sources           queryset of IngestionSource (slug, name) — for the create form
organisations     queryset of Organisation (id, name) — for the superAdmin org select
is_superadmin     bool
webhook_path      str    # e.g. "/api/v1/alerts/webhook/<slug>/" for the hint block
```

### Create-request contract (frontend posts, backend reads)

- `source` — IngestionSource **slug** (required)
- `name` — token label (required, ≤200; Q6)
- `organisation` — UUID (required **only** for superAdmin; ignored for admin, whose org comes from
  `request.user.org_id`)

---

## 2. Backend tasks (`django-backend`)

- **B1** `identity/models.py`: add
  `created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")`
  to `IngestionToken`; generate the migration (`identity/migrations/`). Nullable, no backfill.
- **B2** `ui/views.py`: add `_require_admin_ui(request)` beside `_require_superadmin_ui` (line ~3410) —
  uniform 404 when `not is_admin(request.user)`.
- **B3** `ui/views.py`: `settings_home` (`@not_viewer @login_required`, `_require_admin_ui`).
- **B4** `ui/views.py`: `settings_sources` — `@not_viewer @login_required`, `_require_admin_ui`;
  `IngestionToken.objects.select_related("source", "organisation", "created_by")`; **exclude
  `secret_hash` from the context** (`defer("secret_hash")` or `.values(...)`); non-superAdmin →
  `filter(organisation_id=request.user.org_id)`.
- **B5** `ui/views.py`: `settings_token_create` — `@not_viewer @login_required @require_POST`,
  `_require_admin_ui`; resolve source by slug; resolve org (admin → own; superAdmin → required param);
  validate name; call `IngestionToken.create_token(...)` **with `created_by`**; write the audit row in
  the same transaction; **render** `_settings_token_created.html` (200) with the plaintext. Bounded
  retry on `IntegrityError` (the `prefix` column is `unique=True`).
- **B6** `ui/views.py`: `settings_token_revoke` — `@not_viewer @login_required @require_POST`,
  `_require_admin_ui`; resolve the token scoped to the caller (superAdmin → any; admin →
  `organisation_id=request.user.org_id`), else **uniform 404**; set `revoked_at=timezone.now()`; audit;
  `messages.success` + redirect to `ui-settings-sources`.
- **B7** `identity/audit.py`: add `record_ingestion_token_change(request, *, actor, action, token)`
  modelled on `record_organisation_change` (lines 136-166). `action` ∈
  `{"ingestion_token.created", "ingestion_token.revoked"}`, `target_type="IngestionToken"`,
  `target_id=str(token.id)`, `target_org=token.organisation`, plus `actor`, `actor_org`, `ip`.
  **No secret material.**
- **B8** Routes per §1.
- **B9** **Frozen/enumerated test updates** (the risk surface — see plan §9):
  - `tests/conformance/test_settings_ui.py`: update the two literals at ~670 (`'",": "/settings"'`) and
    ~677 (cheat-sheet label) to match `keys.js` exactly; add "an `admin` sees Settings + Sources".
  - `tests/conformance/test_ui_urls_smoke.py`: add the new GET routes to the `superadmin_only` set
    (~126-131) if the set's user is an analyst (verify first).
  - `tests/conformance/test_tenant_scoping.py`: add the two POST routes to `_UI_WRITE_ONLY_ROUTES`
    (~829-863); reconcile the read route against `_UI_GLOBAL_READ_ROUTES` (~755-782) — the read page is
    org-scoped for `admin` but global for `superAdmin`; if the suite cannot express that, record the
    deviation rather than weakening the test.
  - `test_ui_nav.py` should pass unchanged (rail ↔ `ROUTES` ↔ cheat-sheet stay consistent); verify.
- **B10** New tests (biting, positive assertions):
  1. show-once: POST returns the plaintext; a later GET does not (AC-8/AC-10)
  2. `secret_hash` never in the list HTML (AC-6)
  3. cross-org: an org-A admin's list has no org-B token; revoking an org-B token id → 404 and the
     token is still active (AC-5/AC-16)
  4. roles: `analyst`/`viewer` → 404 on all four routes (AC-7)
  5. audit rows for create + revoke, asserting no secret material (AC-13/AC-17)
  6. **end-to-end: create in UI → webhook `201` → revoke in UI → webhook `401`** (AC-9/AC-15)
  7. superAdmin create without `organisation` → validation error (AC-12)
  8. `/sources` stale column (T5.3): a source with only a non-revoked token must not render "open"
- **B11** Docs: `README.md:58-62` (the "no Settings UI for tokens yet" sentence is now false);
  add the "Ingestion tokens" section to `docs/spec/identity-auth.md` (TODO §10.8).

## 3. Frontend tasks (`django-frontend`)

- **F1** `ui/_settings_nav.html`: role-filtered tab strip built from **one iterable**; tabs =
  Organisations (superAdmin), Sources (admin + superAdmin); `aria-current="page"` on the active tab.
  Include it on `settings_home.html`, `settings_sources.html`, `settings_organisations.html`,
  `settings_users.html` (do not otherwise change those two pages).
- **F2** `ui/settings_home.html`: role-filtered cards linking Organisations + Sources.
- **F3** `ui/settings_sources.html`: table per plan §7b (Source, Organisation **only when
  `show_org_column`**, Name, Prefix, Created, Last used, Issued by, Status + Revoke), empty state, and
  the endpoint/header hint block using `webhook_path`; a "New token" trigger button.
- **F4** Create modal: `_settings_token_create_form.html` (no org field for admin; **required** org
  `<select>` for superAdmin) + `ui/_modal.html` wiring; POST to `ui-settings-token-create`.
- **F5** `_settings_token_created.html`: show-once modal — plaintext in a `<code>` block, "Copy"
  button, prominent "you will not see this again" warning. Colour paired with text.
- **F6** `_settings_token_revoke_form.html`: confirm modal, `confirm_class="btn-err"`, POST to
  `ui-settings-token-revoke`.
- **F7** `ui/static/ui/clipboard.js`: delegated click listener for the Copy button; `navigator.clipboard.writeText`
  with a select-the-text fallback; no inline handler; loaded from `base.html`.
- **F8** `ui/templates/ui/base.html`: rail Settings item (`:97-104`) → `{% url 'ui-settings' %}`, gated
  `{% if request.user.role == "admin" or request.user.role == "superAdmin" %}`; cheat-sheet row for `,`
  (`:169-171`) — same gate, label must match `keys.js`.
- **F9** `ui/static/ui/keys.js`: `ROUTES[","] = "/settings"` (line ~50) and the matching `CHEATSHEET`
  label (line ~34). **Exact strings must equal what backend asserts in `test_settings_ui.py`.**
- **F10** Any new `fa-*` icon used in `base.html` must exist in the fontawesome subset
  (`test_ui_icons.py`).

## 4. Acceptance criteria

All AC-1 … AC-20 from plan §8, plus the end-to-end AC-9/AC-15 pair. Report the exact command output for
the full suite and the ruff/migrations gates.

## 5. Deviations

Any deviation from this brief must be recorded in the plan doc (§12 or a new §13) — not just in your
report.
