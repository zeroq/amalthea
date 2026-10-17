# Verification Report: Ingestion-token lifecycle — pause / resume / delete + feed reaping (§10a, §10b, §10c)

Plans:
- `docs/planning/PLAN-2026-10-17-ingestion-token-lifecycle.md` (§10a/§10b, AC-1…AC-12)
- `docs/planning/PLAN-2026-10-17-feed-reap-on-last-token.md` (§10c, AC-1…AC-10)

Verified: 2026-10-17, at commit `37e5279` (on top of `87ad10f`, `372a862`)
Status: **Pass** — 22/22 ACs Met

> **Provenance / independence caveat (read first).** This verification was performed **inline by the
> planning agent**, not by the `verifier` subagent, because the subagent service was unavailable
> during the session (4 × HTTP 503 `Upstream error from Nvidia: Service temporarily overloaded`,
> both on first call and on resume). The method below is the verifier's method (read each AC, read
> the cited test *assertions*, read the implementation, mutate the behaviour to prove the tests
> discriminate), but **it is not independent** — the same session that wrote the code graded it.
> **Action:** re-run `verifier` against both plans when the service recovers and re-issue this file;
> until then treat "Pass" as provisional. No `security-auditor` pass has been run on §10a–§10c either
> (see Follow-ups).

---

## Summary

| Category | §10a/§10b | §10c | Total |
|----------|-----------|------|-------|
| Total ACs | 12 | 10 | **22** |
| Met | 12 | 10 | **22** |
| Not Met | 0 | 0 | 0 |
| Deviated | 0 | 0 | 0 |

Test evidence: `tests/conformance/test_settings_ingestion_tokens.py` **50 passed**, `tests/conformance/test_ui_loop.py` **28 passed** (78 passed in 8.39 s, re-run after the mutation check below); full suite `make check` **2386 passed / 3 skipped** — green at commit time, pre-commit (`check-fast`) and pre-push (ruff, format, mypy, `django check`, `makemigrations --check`, css-check, pytest, secret scan).

What shipped: a reversible `IngestionToken.deactivated_at` pause distinct from terminal `revoked_at`; deactivate/activate/delete routes with one shared org scope; two independent refusals of a paused token (webhook queryset **and** `verify()`); delete of an inactive token that reaps the feed **only** when it was the feed's last row; kept alerts via `SET_NULL`; and two false UI claims corrected (`source_detail` showed a paused token as Active; `sources_list` claimed open feeds accept unauthenticated posts).

---

## Criterion-by-Criterion Evaluation — Plan A: `PLAN-2026-10-17-ingestion-token-lifecycle.md`

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC-1** | Deactivate sets `deactivated_at`, leaves `revoked_at` null, audits `…deactivated`, webhook then 401/`unauthorized` | **Met** | `test_settings_ingestion_tokens.py:408-424` — 201 before, 302 from `ui-settings-token-deactivate`, `deactivated_at is not None`, `revoked_at is None`, `AuditLog(action="ingestion_token.deactivated")` exists, webhook `401` with `json()["type"] == "unauthorized"`. View `ui/views.py:4613-4649` (guard chain `:4628-4633`, write `:4637-4647`) |
| **AC-2** | Paused token fails at the model layer too (`verify() is False`) | **Met** | `:426-433` sets `deactivated_at` directly (bypassing the view) and asserts `token.verify(plaintext) is False`; `identity/models.py:333` refuses on `revoked_at is not None or deactivated_at is not None` |
| **AC-3** | Activate clears `deactivated_at`, audits `…activated`, same secret gets 201 again | **Met** | `:435-448` — deactivate → activate → `302`, `deactivated_at is None`, audit exists, webhook `201`. View `ui/views.py:4655-4687` |
| **AC-4** | Revoked token cannot be activated: `revoked_at` stays, no `…activated` audit, webhook still 401 | **Met** | `:450-463` — revoke → activate → `302`, `revoked_at is not None`, `deactivated_at is None`, `not AuditLog.filter(action="ingestion_token.activated").exists()`, webhook 401. Guard returns before any write: `ui/views.py:4665-4670` |
| **AC-5** | Revoked token cannot be deactivated | **Met** | `:465-474` — `deactivated_at is None, "a revoked token has nothing left to pause"`; guard `ui/views.py:4628-4630` |
| **AC-6** | Deleting an **active** token is refused and unaudited | **Met** | `:476-485` — `302`, `IngestionToken.objects.filter(pk=…).exists()`, `not AuditLog.filter(action="ingestion_token.deleted").exists()`. Refusal returns before the atomic block: `ui/views.py:4716-4718` (server-side, not just hidden UI) |
| **AC-7** | Deleting a **deactivated** token removes the row and audits `…deleted` with surviving id + org | **Met** | `:487-506` — row gone; `ingestion_token.deleted` with `actor_id`, `target_type=="IngestionToken"`, `target_id==str(token_id)` (survives the row), `target_org_id==org.id`, and `plaintext not in str(deleted.__dict__)` (no secret leakage). Audit written inside `transaction.atomic()` before `delete()`: `ui/views.py:4728-4735` |
| **AC-8** | Deleting a **revoked** token removes the row | **Met** | `:508-517` — `not IngestionToken.objects.filter(pk=…).exists()` |
| **AC-9** | Row offers only the permitted controls in each state (none for revoked beyond Delete) | **Met** | `:519-540` renders the real page in all three states: active → `data-modal-open="deactivate-token-"` present, `delete-token-` **absent**; paused → `"Deactivated"` + activate URL + `delete-token-` present + `deactivate-token-` **absent**; revoked → `"Revoked"` + `delete-token-` present + activate URL **absent**. Template chain `settings_sources.html:53/65/80` matches plan §7 exactly (Active → Deactivate+Revoke; Deactivated → Activate+Delete; Revoked → Delete). *See Observation O2 — the "Revoke" half of the Active row is not asserted.* |
| **AC-10** | Cross-org deactivate/activate/delete → 404, mutate nothing | **Met** | `TestTokenLifecycleIsolation:547-561`, parametrized over all three actions — `404`, `revoked_at is None and deactivated_at is None`, row still exists. Scoping `ui/views.py:4557-4570` (`_org_scoped_token`: non-superAdmin filters `organisation_id`, miss = uniform 404) |
| **AC-11** | `analyst`/`viewer` locked out of all three routes (404, no mutation) | **Met** | `:565-579`, parametrized role × action (6 cases) — `404`, flags unchanged. Decorators `@not_viewer @login_required @require_POST` on `ui/views.py:4610-4612, 4652-4654, 4690-4692` plus `_require_admin_ui` at `:4620, :4657, :4708` |
| **AC-12** | `superAdmin` may pause and delete another org's token | **Met** | `:581-609` — cross-org deactivate `302` + flag set, delete `302` + row gone (and, post-§10c, feed reaped — see AC-7 of Plan B) |

## Criterion-by-Criterion Evaluation — Plan B: `PLAN-2026-10-17-feed-reap-on-last-token.md`

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC-1** | Deleting a feed's last token deletes the feed and it vanishes from the Sources list | **Met** | `test_settings_ingestion_tokens.py:636-660` — `not IngestionSource.objects.filter(pk=source_id).exists()` **and** `f'href="{detail_url}"' not in list_body`. The href assertion is **not vacuous**: `sources_list.html:27` renders that link when `can_open_source_page`, which is `is_admin(request.user)` (`ui/views.py:1548`) — true for the admin in the test |
| **AC-2** | Reaping audited with feed id, type, tenant, by the acting admin | **Met** | same test `:656-660` — `action="ingestion_source.deleted"`, `actor_id==admin.id`, `target_type=="IngestionSource"`, `target_id==str(source_id)`, `target_org_id==org.id`. Row written before `source.delete()` inside the transaction: `ui/views.py:4738-4741`; `identity/audit.py:263-292` |
| **AC-3** | Sibling token remains → feed kept, no feed audit row | **Met** | `:662-678` — `IngestionSource…exists()` True, `not AuditLog.filter(action="ingestion_source.deleted").exists()`, second token intact |
| **AC-4** | A **revoked** sibling still keeps the feed ("last row", not "last usable token") | **Met** | `:680-693` — sibling revoked, live token deactivated+deleted, feed survives. Implements D5 (`source.tokens.exists()` counts rows regardless of flags) |
| **AC-5** | Feed's alerts survive with `ingestion_source IS NULL` and their `raw_payload` | **Met** | `:695-713` — end-to-end: real webhook ingest → `alert.ingestion_source_id == source_id`; after reaping → `ingestion_source_id is None` **and** `raw_payload` truthy. `alerts/models.py:97-103` `on_delete=SET_NULL, null=True`; **only** FK to `IngestionSource` besides the token rows, so `source.delete()` cannot hit `ProtectedError` |
| **AC-6** | Delete modal warns only when the token is the feed's last | **Met** | `:715-732` — with a sibling: `"This is the last token for feed" not in body`; after removing it: present + feed slug present. Conditional `ui/templates/ui/_settings_token_delete_form.html:17-25` on annotated `source_token_count == 1` (`ui/views.py:3972-3973`, both `Count(..., distinct=True)` so the two joins cannot cross-multiply) |
| **AC-7** | `superAdmin` reaping cross-org also reaps that org's feed, audited to the feed's tenant | **Met** | `:608-609` — `not IngestionSource.objects.filter(slug="src-b").exists()` and `AuditLog…ingestion_source.deleted).target_org_id == org_b.id` (the **feed's** org, not the actor's own org A) — requires `select_related("source__organisation")`, `ui/views.py:4567` |
| **AC-8** | Source detail shows a deactivated token as **Deactivated**, never **Active**, revoked supersedes | **Met** | `TestSourceDetailTokenStatus:745-764` — asserts the exact pill markup `pill pill-pending">Deactivated` and **`pill pill-on">Active` absent**, then `pill pill-off">Revoked` with **`Deactivated` absent**. Template `source_detail.html:206-213` (regression fixed in `37e5279`; previously `if token.revoked_at` → else Active) |
| **AC-9** | Sources list shows `none`/`required`/`paused`; "unauthenticated posts" claim gone | **Met** | `test_ui_loop.py:518-553` — initial `none` present **and** `required` absent **and** `"unauthenticated" not in body`; after token → `required` present / `none` absent; after pause → `paused` present / `required` absent. Template `sources_list.html:35-40`; `grep unauthenticated ui/templates/` returns nothing |
| **AC-10** | Cross-org delete remains a uniform 404, mutates nothing | **Met** | shared with Plan A AC-10 (`:547-561`, `delete` case) — no regression from the cascade |

---

## Design-decision compliance

### Plan A (D1…D8)

| Decision | Implemented | Evidence |
|----------|-------------|----------|
| **D1** Reversible `deactivated_at` separate from terminal `revoked_at` | ✅ | `identity/models.py:213` (+ comment), migration `identity/migrations/0012_ingestiontoken_deactivated_at.py` (`AddField`, reversible); `makemigrations --check` clean in `make check` |
| **D2** Inactive = deactivated **OR** revoked; one shared definition | ✅ | `is_revoked` / `is_deactivated` / `is_active` at `identity/models.py:233-241`, consumed by all three views and both templates |
| **D3** Revoked supersedes paused; revoked never activatable | ✅ | `settings_sources.html:53-79` single `if/elif/else` chain; `source_detail.html:206-213` (AC-8); `ui/views.py:4665` |
| **D4** Activate = inline POST; Deactivate/Delete = modals | ✅ | activate rendered as `<form … class="inline-form">` (`settings_sources.html:69`), deactivate/delete as `data-modal-open="…"` → `<dialog>` bodies (no native `confirm()` anywhere) |
| **D5** Audit written **before** `delete()` inside `transaction.atomic()` | ✅ | `ui/views.py:4728-4735`; proven by `target_id == str(token_id)` surviving (`:504`) |
| **D6** Scope mirrors revoke; uniform 404 | ✅ | `_org_scoped_token` `:4557-4570` shared by all four routes (revoke refactored onto it) |
| **D7** Two independent gates on the webhook | ✅ | `ingest/views.py:121` `.filter(prefix=…, revoked_at__isnull=True, deactivated_at__isnull=True)` **and** `identity/models.py:326-333` `verify()`; covered separately by AC-1 and AC-2 |
| **D8** No new API, no new index, no new CSS | ✅ | No route in `api/`, no migration index; pills reuse existing `pill-on`/`pill-pending`/`pill-off` and controls reuse `.case-actions` |

### Plan B (D1…D8)

| Decision | Implemented | Evidence |
|----------|-------------|----------|
| **D1** Cascade only on the **last** token | ✅ | `ui/views.py:4738-4741`; AC-3 |
| **D2** `mapping_config` goes with the feed, modal says so | ✅ | feed row (which owns `mapping_config`) deleted; `_settings_token_delete_form.html:19-21` states "its mapping rules go" |
| **D3** Alerts kept, never cascade-deleted | ✅ | `alerts/models.py:99` `SET_NULL`; AC-5 |
| **D4** Emptiness evaluated **after** the token delete, same transaction | ✅ | order at `ui/views.py:4735` (`token.delete()`) → `:4738` (`source.tokens.exists()`) → `:4741` (`source.delete()`), all inside `with transaction.atomic():` at `:4728`. An earlier check would race; this one cannot. **Not directly testable on SQLite** (see O5) |
| **D5** "Last row", not "last usable token" | ✅ | AC-4 (revoked sibling keeps the feed) |
| **D6** New `ingestion_source.deleted`, written pre-delete, no `details` payload | ✅ | `identity/audit.py:263-292` — no `details=` argument passed; docstring states why (config no longer exists) |
| **D7** Modal warning conditional on `source_token_count == 1` | ✅ | `_settings_token_delete_form.html:17` + annotations `ui/views.py:3972-3973`; AC-6 |
| **D8** Three states replace boolean `has_token` | ✅ | `ui/views.py:1513-1521` (`has_active_token`/`has_paused_token`) + `sources_list.html:35-40`; AC-9 |

---

## Mutation check (do the tests actually discriminate?)

To rule out false "Met" from vacuous assertions, the two crown-jewel behaviours were **temporarily mutated** and the suites re-run, then reverted (`git status --porcelain` empty after revert; source confirmed restored at `ui/views.py:4738` and `sources_list.html:38`).

| Mutation | Predicted | Observed |
|----------|-----------|----------|
| `ui/views.py:4738` → `source_reaped = False` | exactly the 3 reap-asserting tests fail; sibling tests stay green | ✅ **3 failed, 47 passed** — `test_deleting_the_last_token_deletes_the_feed`, `test_the_feeds_alerts_survive_without_a_feed_link`, `test_superadmin_can_pause_and_delete_another_orgs_token`; `test_deleting_a_non_last_token_keeps_the_feed`, `test_a_revoked_sibling_still_keeps_the_feed` and `test_the_delete_modal_warns_only_when_it_is_the_last_token` correctly **passed** |
| `ui/templates/ui/sources_list.html:37-38` → drop the `paused` branch | the three-state test fails on the paused pill | ✅ **1 failed** — `test_ui_loop.py:552 assert '<span class="pill pill-pending">paused</span>' in body` |

Both mutations reverted; post-revert run: **78 passed** (`test_settings_ingestion_tokens.py` 50 + `test_ui_loop.py` 28).

---

## Documentation / cross-artifact claims

| Claim site | Status | Evidence |
|------------|--------|----------|
| `docs/spec/identity-auth.md` documents `deactivated_at`, all five audit actions, delete/reap rule, `ingestion_source.deleted` | ✅ | lines 90, 96, 101, 110-111, 123-133, 136, 143-144 |
| `docs/spec/data-model.md` lists `deactivated_at` | ✅ | line 35 |
| `docs/spec/deviations.md` **WTR-2** marked *Fixed 2026-10-17* with the rewritten pinning test named | ✅ | line 393 |
| `TODO.md` §10.8(a) closed; rows 10a/10b/10c *SHIPPED*; header updated | ✅ | lines 4-7, 60-62, 380-382 |
| Stale sibling notes corrected with **dated** additions (not silently edited) | ✅ | `PLAN-2026-10-17-ingestion-token-lifecycle.md` §10 note superseded; `PLAN-2026-10-15-token-only-webhook-routing.md` §6 dated correction; Plan B §9 records the rejected "Delete feed" button |
| Migration merge expectation with the parallel §12.8 verdict work | ✅ documented | Plan A §5 "Merge note"; this increment added **no** migration, so the expected conflict remains the single `0012` number |

---

## Security review (Plan A §8)

| Item | Status | Evidence |
|------|--------|----------|
| Org scope + role gate on every route; uniform 404, no existence oracle | ✅ | AC-10 (404, inert), AC-11 (404 for analyst/viewer), `ui/views.py:4557-4570` |
| No secret material in any response or audit row | ✅ | AC-7 asserts `plaintext not in str(deleted.__dict__)`; only `name`/`prefix`/`id`/`organisation` read (`record_ingestion_token_change` `identity/audit.py:202`); `secret_hash` never rendered (pinned by `test_hash_secrecy`, `:175-189`) |
| Paused ⇒ 401 `type: "unauthorized"`, indistinguishable from unknown/revoked | ✅ | AC-1 |
| Delete refused for an active token server-side | ✅ | AC-6 (view guard, not merely hidden control) |
| CSRF: every mutation a real form POST with `{% csrf_token %}` | ✅ | activate inline form `settings_sources.html:70`; modal bodies carry the hidden `token_id` + form association; no `fetch`/`hx-post` mutation path added |

---

## Observations (not AC failures — no criterion deviated)

| # | Observation | Severity | Suggested action |
|---|-------------|----------|------------------|
| **O1** | `TestTokenActivationLifecycle._mint(slug=…)` passes a `slug` to `_mk_source(slug=slug)`, but `_create_token_via_ui` posts `source_name="Source"` (the default `name`), and the view reuses by `slugify(source_name)` → the token binds to a **separate** freshly-provisioned `slug="source"` feed, orphaning the `slug=<param>` row. The `slug` argument therefore does not do what it reads like it does. **No AC impact** — every assertion in that class is token/org/audit/pill-scoped. Already documented as a trap in Plan B §9, but only the Plan B `_mint` was fixed (it names the feed after its slug). | Low (test hygiene) | Align `TestTokenActivationLifecycle._mint` with the Plan B helper: `_mk_source(slug=slug, name=slug, …)` |
| **O2** | Plan §7's control matrix says an Active row offers **Deactivate + Revoke**; the template does (`settings_sources.html:90-96`), but AC-9's test only pins Deactivate/Activate/Delete. The Revoke affordance on an active row is unpinned. | Low (coverage) | Add one assertion to `test_the_status_cell_offers_the_right_control_for_each_state` |
| **O3** | The `source.delete()` failure path (rollback must restore the token row too) is correct **by construction** — same `transaction.atomic()` — but has no test. | Low | Optional: monkeypatch `IngestionSource.delete` to raise, assert both rows survive |
| **O4** | A token can hold **both** `deactivated_at` and `revoked_at` (pause → later revoke sets `revoked_at` only; `settings_token_revoke:4589` guards solely on `revoked_at`). Correct everywhere: `is_active` is False, `verify()` refuses, display precedence reads Revoked. Accepted state, but worth knowing before anyone adds a "clear the pause" feature. | Info | Documented here; no change |
| **O5** | D4's concurrency property (post-delete emptiness check) cannot be exercised under SQLite's single-writer test harness — it is verified by **code reading** (`ui/views.py:4735` → `:4738` order) only. | Info | Re-check if a Postgres concurrency test harness lands (TODO 3.1) |
| **O6** | `IngestionTokenAdmin.list_display` still omits `deactivated_at` — already declared *Not done* in Plan B §9 and agreed as out of scope. | Info | Optional Django-admin polish |

---

## Discrepancies

**None.** All 22 ACs met; all 16 design decisions implemented as written; every cited test exists at the named path and asserts the stated behaviour (mutation-checked where it mattered); documentation claims in four artifacts agree with shipped behaviour; no secret material introduced; full suite green.

---

## Verdict

**PROVISIONAL PASS** — 22/22 Acceptance Criteria Met (12/12 §10a+§10b, 10/10 §10c), 16/16 design decisions implemented, mutation check confirms the two headline tests discriminate, 2386 tests pass. **Provisional only because this pass was not independent** — see the caveat at the top. Re-run `verifier` and `security-auditor` on this scope when the subagent service recovers, then overwrite this file with their verdict.
