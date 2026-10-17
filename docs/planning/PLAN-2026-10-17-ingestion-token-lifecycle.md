# PLAN-2026-10-17 — Ingestion-token lifecycle actions (pause / resume / delete)

**Status:** IMPLEMENTED 2026-10-17 — awaiting verification and commit approval.
**Source:** `TODO.md` priority rows **10a** / **10b** (user request 2026-10-17).
**Related:** `PLAN-2026-10-15-settings-sources-ingestion-tokens.md` (the surface this extends),
`ADR-004` (per-org ingestion tokens), `ADR-009` (org-owned sources / org scoping).

---

## 1. Summary

Settings → Sources could mint a token and revoke it. Revocation is **terminal**, so "I want to
stop this feed for an hour" and "I want to delete a credential nobody uses any more" both had the
same, destructive answer: revoke, and then look at a list of dead rows forever.

This increment adds the missing middle: **deactivate** (reversible pause), **activate** (resume),
and **delete** (for inactive tokens only), each audited, org-scoped and keyboard-reachable.

## 2. Goals

- G1 — An `admin` (own org) or `superAdmin` (any org) can pause an active token; the webhook stops
  accepting its secret immediately.
- G2 — The same actor can resume a paused token; the *same* secret works again (no re-mint, no
  redistribution to the source owner).
- G3 — The same actor can permanently delete a token that is **inactive** (revoked or paused).
- G4 — Every state change lands in `AuditLog` with no secret material; a deleted token keeps an
  audit trail that still identifies it.
- G5 — The three controls are ordinary `<form>` POSTs (work without JS), destructive ones behind a
  themed modal (§6 "modals over native dialogs").

## 3. Non-goals

- No API surface: these are `ui/*` routes, not `/api/v1/*`. Token lifecycle over the API is a
  separate decision (the API has no token CRUD at all today).
- No rotation / grace period ("mint new, keep old valid for N minutes"). Deactivate + mint is the
  manual workaround; rotation is its own item.
- No bulk actions, no expiry/`expires_at` field, no scheduled re-activation.
- No change to the webhook contract: a paused token produces the *same* uniform 401 as an unknown
  or revoked one.

## 4. Design decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D1 | Add a **reversible** `IngestionToken.deactivated_at`, distinct from the **terminal** `revoked_at`. | Overloading `revoked_at` would mean either "pause" can be undone (breaking the guarantee that a compromised secret is dead forever) or a pause cannot be undone (defeating the point). Two columns, two meanings. |
| D2 | **Inactive = deactivated OR revoked.** Only inactive tokens are deletable; only active tokens are deactivatable; only paused tokens are activatable. | One shared definition (`IngestionToken.is_active`) keeps the UI, the views and the tests from drifting apart. An active credential a live feed still presents is never destroyed from under it. |
| D3 | **Precedence: revoked supersedes paused.** A token that was paused and then revoked reads "Revoked" and offers only Delete. A revoked token can never be activated. | Revocation is the stronger, irreversible statement. Showing "Deactivated" with an Activate button for a dead credential would be a lie. |
| D4 | **Activate = direct inline POST**; **Deactivate** and **Delete** = confirm modals. | Activation is non-destructive and instantly reversible; pausing interrupts a live feed; deleting is permanent. Modal friction should be proportional to the consequence. |
| D5 | Delete writes the audit row **before** `delete()`, inside `transaction.atomic()`. | Django clears the pk after deletion, so `token.id` / `token.organisation` must be read while the row still exists. The trail must survive the credential (`docs/spec/deviations.md` has no exemption for "the row is gone"). |
| D6 | Org scoping mirrors revoke exactly: non-`superAdmin` filters on `organisation_id`, and a miss is the **uniform 404**, not a 403. | `ADR-009` parity — a token outside your scope must be indistinguishable from one that does not exist. |
| D7 | Two independent gates on the webhook: the lookup excludes `deactivated_at__isnull=False`, **and** `verify()` returns `False` for a paused token. | The queryset is the fast path; the model is the last line of defence if a future caller forgets the filter. |
| D8 | No new API, no new index, no new CSS. | The lookup hits the unique `prefix` index and then filters two nullable columns; the action row reuses the existing `.case-actions` alignment utility. |

The user approved D1 + D2 ("A" + "2a") before implementation.

## 5. Data model & migration

```python
deactivated_at = models.DateTimeField(null=True, blank=True)  # reversible pause (§10a)
```

- Migration: `identity/migrations/0012_ingestiontoken_deactivated_at.py` — `AddField`, reversible,
  depends on `0011_auditlog_details`.
- New properties on `IngestionToken`: `is_revoked`, `is_deactivated`, `is_active`.
- `IngestionToken.verify()` now refuses both revoked and paused tokens.
- **Merge note:** the parallel §12.8 verdict work on the other host adds its own migration; expect a
  linearisation conflict on this number and resolve by hand (no dependency between the two).

## 6. Routes (all `@not_viewer @login_required @require_POST`, admin-gated)

| name | path | action | audit action |
|------|------|--------|--------------|
| `ui-settings-token-revoke` *(existing)* | `/settings/sources/<uuid:token_id>/revoke` | terminal | `ingestion_token.revoked` |
| `ui-settings-token-deactivate` | `/settings/sources/<uuid:token_id>/deactivate` | pause | `ingestion_token.deactivated` |
| `ui-settings-token-activate` | `/settings/sources/<uuid:token_id>/activate` | resume | `ingestion_token.activated` |
| `ui-settings-token-delete` | `/settings/sources/<uuid:token_id>/delete` | destroy (inactive only) | `ingestion_token.deleted` |

Shared resolver: `_org_scoped_token(request, token_id)` → `(token, None)` or `(None, 404)`;
`settings_token_revoke` was refactored onto it so all four routes share one scoping rule.

## 7. UI

Settings → Sources token table, "Status / Action" cell — one `<td>` per row:

| State | Pill | Controls |
|-------|------|----------|
| Active | `pill-on` "Active" | **Deactivate** (modal), **Revoke** (modal) |
| Deactivated | `pill-pending` "Deactivated" + timestamp | **Activate** (direct POST), **Delete** (modal) |
| Revoked | `pill-off` "Revoked" + timestamp | **Delete** (modal) |

Controls sit in a `.case-actions` wrapper (existing `margin-top:0`, centred, equal-height utility —
reused here, see §10). New modal bodies: `_settings_token_deactivate_form.html`,
`_settings_token_delete_form.html` (pattern-matched to the existing revoke body).

## 8. Security

- Org scope + role gate on every route (D6); uniform 404, no existence oracle.
- No secret material in any response or audit row: deactivate/activate/delete only ever read
  `name`, `prefix`, `id`, `organisation`; `secret_hash` is never rendered.
- Paused token ⇒ 401 with `type: "unauthorized"` — indistinguishable from unknown/revoked (D7).
- Delete is refused for an active token (server-side, not just hidden in the UI).
- CSRF: every mutation is a real form POST with `{% csrf_token %}`.

## 9. Acceptance criteria → tests

`tests/conformance/test_settings_ingestion_tokens.py`

| AC | Statement | Test |
|----|-----------|------|
| AC-1 | Deactivate sets `deactivated_at`, leaves `revoked_at` null, audits `…deactivated`, and the same secret then gets 401/`unauthorized`. | `TestTokenActivationLifecycle::test_deactivate_pauses_the_token_and_the_webhook_turns_401` |
| AC-2 | A paused token fails at the model layer too (`verify() is False`). | `…::test_the_token_is_rejected_at_the_model_layer_too` |
| AC-3 | Activate clears `deactivated_at`, audits `…activated`, and the same secret gets 201 again. | `…::test_activate_resumes_the_token_and_the_webhook_accepts_again` |
| AC-4 | A revoked token cannot be activated: `revoked_at` stays set, no `…activated` audit, webhook still 401. | `…::test_activate_refuses_a_revoked_token` |
| AC-5 | A revoked token cannot be deactivated. | `…::test_deactivate_refuses_a_revoked_token` |
| AC-6 | Deleting an **active** token is refused and unaudited. | `…::test_delete_refuses_an_active_token` |
| AC-7 | Deleting a **deactivated** token removes the row and writes `…deleted` with the surviving id + org. | `…::test_delete_removes_a_deactivated_token_and_audits_before_the_row_goes` |
| AC-8 | Deleting a **revoked** token removes the row. | `…::test_delete_removes_a_revoked_token` |
| AC-9 | The row offers only the permitted controls in each state (and none for revoked beyond Delete). | `…::test_the_status_cell_offers_the_right_control_for_each_state` |
| AC-10 | Cross-org `deactivate`/`activate`/`delete` are 404 and mutate nothing. | `TestTokenLifecycleIsolation::test_cross_org_actions_are_404_and_inert` |
| AC-11 | `analyst`/`viewer` are locked out of all three routes (404, no mutation). | `…::test_non_admin_roles_are_locked_out` |
| AC-12 | A `superAdmin` may pause and delete another org's token. | `…::test_superadmin_can_pause_and_delete_another_orgs_token` |

## 10. Notes, deviations & follow-ups

- **`.case-actions` reuse.** The aligned-action-row utility lives under a case-flavoured name but is
  already used by `source_detail.html`; reused here for equal-height, `margin-top:0` controls.
  Recorded in `TODO.md` §12.9 as something to generalise/rename there.
- **`has_token` on the sources list is deliberately unchanged** (`ui/views.py`, filters
  `revoked_at__isnull=True`): a paused token can be resumed, so its source is still "protected". A
  revoked-and-then-deleted token's source becomes unprotected again — acceptable, the source is
  re-tokenable.
  **Superseded 2026-10-17 (§10c)** — see `PLAN-2026-10-17-feed-reap-on-last-token.md`. The boolean
  `has_token` was replaced by `has_active_token` / `has_paused_token` (three states, because a token is
  usable only while neither revoked nor deactivated), and a source with **no** token is not "unprotected"
  but *accepts nothing* — the "open" framing it sat next to was false from strict token auth onward.
  The re-tokening fallback also changed: deleting a feed's **last** token now deletes the feed, so the
  "source is re-tokenable after a revoke+delete" case only survives while a sibling token exists.
- **Not done, called out:** `IngestionTokenAdmin.list_display` still omits `deactivated_at` (Django
  admin only, low value). `TODO.md` §12.6/§12.7 file-path drift (`ui/cases.html` vs the real
  `ui/templates/ui/case_list.html`) was corrected for §12.7 while editing that file's row.
- **Separate, follow-up commit:** `ingest/views.py` carried the dead `_verify_secret` + argon2 imports
  that commit `0d1d541`'s message claimed to remove (its diff for that file is empty), and
  `docs/spec/deviations.md` / `TODO.md` §6.18 asserted the removal. Removed in the follow-up commit
  that carried this note.

## 11. Files touched

```
identity/models.py                                        deactivated_at + is_* properties + verify()
identity/migrations/0012_ingestiontoken_deactivated_at.py  (new)
identity/audit.py                                          record_ingestion_token_change docstring
ingest/views.py                                            _authenticate_token lookup + comment
ui/views.py                                               _org_scoped_token + 3 views + revoke refactor
ui/urls.py                                                3 routes
ui/templates/ui/settings_sources.html                     status/action cell
ui/templates/ui/_settings_token_deactivate_form.html      (new)
ui/templates/ui/_settings_token_delete_form.html          (new)
tests/conformance/test_settings_ingestion_tokens.py        +19 tests (25 → 44)
TODO.md                                                   rows 10a/10b done; §12.9 note
```
