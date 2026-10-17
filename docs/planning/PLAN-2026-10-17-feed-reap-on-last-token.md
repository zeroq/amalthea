# PLAN-2026-10-17 — Reap a feed when its last token is deleted

**Status:** IMPLEMENTED 2026-10-17 — awaiting verification and commit approval.
**Source:** `TODO.md` priority row **10c** (user request 2026-10-17).
**Related:** `PLAN-2026-10-17-ingestion-token-lifecycle.md` (§10a/§10b, which this extends),
`PLAN-2026-10-15-token-only-webhook-routing.md` (ADR-008), `ADR-009` (org-owned sources),
`docs/spec/deviations.md` **WTR-2**.

---

## 1. Summary

Settings → Sources could delete a token, but the **feed** it belonged to stayed behind: an
`IngestionSource` row that could never receive anything, still advertised on the Sources page with a
pill that (falsely) implied it was open to the world.

Deleting a token that is its feed's **last** credential now deletes the feed with it. The feed's
alerts are **kept** — `Alert.ingestion_source` is `SET_NULL`, so evidence outlives the mapping profile.

The same pass fixed two false UI claims found while grounding this: the source detail page rendered a
*deactivated* token as **Active** (a regression from §10a), and `sources_list.html` told operators an
open feed "accepts unauthenticated posts", which has been false since strict token auth (WTR-2,
`TODO.md` §10.8(a)).

## 2. Goals

- G1 — Deleting the last token of a feed removes that feed from the database, so it disappears from
  the **Sources** list.
- G2 — A feed with any remaining token (including a *revoked* one) is never reaped by a sibling's
  deletion.
- G3 — Existing alerts survive the feed with their `raw_payload` and verbatim wire `source`; they only
  lose the link to which profile accepted them.
- G4 — The consequence is stated in the confirmation modal **before** the operator confirms, and only
  when it is real (i.e. only for the last token).
- G5 — The reaping is audited as its own row, written before the delete, attributed to the feed's
  tenant.
- G6 — Sources list shows three credential states (`required` / `paused` / `none`) and makes no false
  security claim.

## 3. Non-goals

- **No explicit "Delete feed" control** — rejected by the user: a feed with a live token is still
  reachable through deactivate → delete (the deliberate path), so a second destructive affordance was
  judged unnecessary surface.
- No archive/soft-delete flag for feeds; no `deleted_at`.
- No new API route, no schema change, no new index, no new CSS class.
- No change to the webhook contract or to `IngestionToken.verify()` — the receiver behaviour is
  unchanged; this is purely lifecycle bookkeeping in the management UI.
- No change to alert history: no backfill, no soft-deletion of alerts.

## 4. Design decisions (user-confirmed defaults)

| ID | Decision | Rationale |
|----|----------|-----------|
| D1 | **Cascade only when the deleted token is the feed's last one.** | A feed exists to carry credentials. A feed with another token still has a job; reaping it on an unrelated delete would take a working credential's target out from under it. |
| D2 | **The feed's `mapping_config` goes with the feed**, and the modal says so. | The profile has no meaning without a credential to hand it; keeping it would leave an orphan the Sources page cannot show (no token → no row under D1's rule). The operator is told before confirming, so config loss is never a surprise. |
| D3 | **Alerts are kept** (`SET_NULL`), never cascade-deleted. | The schema already says this: `Alert.ingestion_source` is `SET_NULL`, `null=True`. `raw_payload` + wire `source` are the forensic record; a deleted mapping profile is not a reason to lose them. |
| D4 | **Emptiness is evaluated *after* the token delete, inside the same `transaction.atomic()`.** | If evaluated before, a token created concurrently could be silently swept up by the cascade. Checking last makes the decision local to the state being written. |
| D5 | **The rule is "last row", not "last usable token".** A revoked sibling keeps the feed. | Predictable: the feed goes when its credential *rows* go, not when a particular flag is set. The alternative ("revoked rows don't count") makes a feed disappear while a row still references it. |
| D6 | **New audit action `ingestion_source.deleted`**, row written before `source.delete()`, no `details` payload. | Mirrors `ingestion_source.created`; Django clears the pk after deletion. No config in the payload because the config no longer exists — the row answers "which feed, which tenant, by whom, when". |
| D7 | **Modal warning is rendered conditionally** on an annotated `source_token_count == 1`. | The blast radius belongs to *this* delete; showing it for every delete trains operators to ignore it. |
| D8 | **Three states on the Sources list** — `required` / `paused` / `none` — replacing the boolean `has_token`. | A token is usable only while neither revoked nor deactivated (§10a), so a two-state pill already lied about a paused feed. Fixes `TODO.md` §10.8(a) / `WTR-2` in the same pass. |

## 5. Data model

**No schema change.** Everything rides on columns that exist:
`IngestionToken.source` (already `CASCADE`), `Alert.ingestion_source` (already `SET_NULL`).

New audit action: `ingestion_source.deleted` via `record_ingestion_source_deleted`.

New annotated context on the Settings → Sources token list:
`source_token_count` (`Count("source__tokens", distinct=True)`) and
`source_alert_count` (`Count("source__alerts", distinct=True)`), both `distinct=True` because the two
aggregate joins would otherwise cross-multiply.

## 6. Routes

No new routes. `ui-settings-token-delete` (`/settings/sources/<uuid:token_id>/delete`) gained the
cascade; it keeps the same gates (`@not_viewer @login_required @require_POST`, `_require_admin_ui`,
`_org_scoped_token` → uniform 404).

## 7. UI

- **`_settings_token_delete_form.html`** — conditional paragraph naming the feed, stating that its
  mapping rules go, and naming the alert count if non-zero. Rendered only for the last token.
- **`settings_sources.html`** — unchanged this increment (the §10a status/action cell already has the
  right controls); the flash message after a reaping names the feed that went with it.
- **`sources_list.html`** — three-state pill; hint rewritten to say a feed with no usable token
  accepts *nothing* (not "is open"), and to point at Settings → Sources.
- **`source_detail.html`** — token table now shows all three states; `revoked_at` supersedes
  `deactivated_at`, matching the Settings page.

## 8. Acceptance criteria → tests

| AC | Criterion | Test |
|----|-----------|------|
| AC-1 | Deleting a feed's last token deletes the feed, and it vanishes from the Sources list. | `test_settings_ingestion_tokens.py::TestFeedReapedWithLastToken::test_deleting_the_last_token_deletes_the_feed` |
| AC-2 | The reaping is audited with the feed's id, type and tenant, by the acting admin. | *(same test — asserts `ingestion_source.deleted` `target_id` / `target_org` / `actor`)* |
| AC-3 | Deleting a token when a sibling token remains keeps the feed and writes no feed audit row. | `…::test_deleting_a_non_last_token_keeps_the_feed` |
| AC-4 | A revoked sibling still keeps the feed ("last row", not "last usable token"). | `…::test_a_revoked_sibling_still_keeps_the_feed` |
| AC-5 | The feed's alerts survive with `ingestion_source IS NULL` and their `raw_payload`. | `…::test_the_feeds_alerts_survive_without_a_feed_link` |
| AC-6 | The delete modal warns only when the token is the feed's last. | `…::test_the_delete_modal_warns_only_when_it_is_the_last_token` |
| AC-7 | A superAdmin reaping another org's token also reaps that org's feed, audited to the feed's tenant. | `…::TestTokenLifecycleIsolation::test_superadmin_can_pause_and_delete_another_orgs_token` *(extended)* |
| AC-8 | The source detail page shows a deactivated token as **Deactivated**, never **Active**, and revoked supersedes it. | `…::TestSourceDetailTokenStatus::test_a_deactivated_token_is_not_shown_as_active` |
| AC-9 | Sources list shows `none` / `required` / `paused` for the three credential states, and no longer contains the "unauthenticated posts" claim. | `test_ui_loop.py::test_the_sources_page_shows_whether_a_secret_is_required` *(rewritten)* |
| AC-10 | Deleting a cross-org token remains a uniform 404 and mutates nothing (unchanged). | `…::TestTokenLifecycleIsolation::test_cross_org_actions_are_404_and_inert` |

## 9. Notes, deviations & follow-ups

- **Rejected option:** an explicit "Delete feed" button (user: "4 no"). Recorded here so it is not
  re-proposed as missing coverage; the deactivate → delete path is the supported route.
- **Stale sibling note superseded:** `PLAN-2026-10-17-ingestion-token-lifecycle.md` §10 recorded
  `has_token` as deliberately unchanged and described a source as "re-tokenable" after a revoke+delete.
  Both are now false; corrected there with a dated note rather than edited away.
- **`WTR-2` and `TODO.md` §10.8(a) are closed** by AC-9; `PLAN-2026-10-15-token-only-webhook-routing.md`
  §6 carries a dated correction too.
- **Not done:** `IngestionTokenAdmin.list_display` still omits `deactivated_at`; `ui/urls.py` has no
  feed-level route (deliberate, see Non-goals).
- **Fixture gotcha (worth knowing):** `_create_token_via_ui` reuses an existing profile by
  `slugify(source_name)`. A test helper that creates a feed with `slug=X` but the default `name="Source"`
  makes the form provision a *second* feed — and a reaping delete would then reap the wrong one. The
  helper in this file names the feed after its slug, with a docstring saying why.

## 10. Files touched

```
identity/audit.py                                            record_ingestion_source_deleted (new)
ui/views.py                                                  _org_scoped_token select_related; _settings_sources_context annotations; sources_list 3 states; settings_token_delete cascade
ui/templates/ui/_settings_token_delete_form.html             last-token warning paragraph
ui/templates/ui/sources_list.html                            3-state pill + truthful hint
ui/templates/ui/source_detail.html                           token status pill (deactivated fix)
tests/conformance/test_settings_ingestion_tokens.py          +6 tests (44 → 50)
tests/conformance/test_ui_loop.py                            rewritten stale-copy pin (3 states)
docs/spec/identity-auth.md                                   §10a/§10b lifecycle + audit set + reap rule
docs/spec/data-model.md                                      deactivated_at
docs/spec/deviations.md                                      WTR-2 marked fixed
docs/planning/PLAN-2026-10-15-token-only-webhook-routing.md  dated correction to §6
docs/planning/PLAN-2026-10-17-ingestion-token-lifecycle.md   §10 note superseded (dated)
TODO.md                                                      row 10c; §10.8(a) closed; header
```
