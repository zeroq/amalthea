# TODO — Amalthea

Open work only. Shipped work and closed-item history live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-11-26 — closed §12.3-fu1 into [`COMPLETED.md`](./COMPLETED.md) after the user accepted
deviations W63-16 and W63-17. The latest full-suite log (`/tmp/amalthea-full-pytest.log`) is green:
**4441 passed, 3 skipped, 15 warnings, 2 subtests passed in 1508.09s**. The dashboard layout regression
introduced by `235bc004` was repaired and verified with targeted UI suites. Static gates, hooks and secret
scans pass. The priority table below is the source of truth for open work.

---

## 🎯 NEXT HIGHEST-IMPACT ITEMS (Priority Order)

Ordered by **impact on the analyst** — the daily IR loop first, then deployment security, then
engineering hygiene. Shipped rows were removed in the 2026-11-08 cleanup; the shipped search row moved
to its narrow follow-up on 2026-11-13 (see `COMPLETED.md`).

| Priority | Item | Description | Impact |
|----------|------|-------------|--------|
| **1** | **S3 + S5 (+ M3 residual)** | **S3** per-account lockout on the API login + stop trusting client `XFF`. **S5** attachment aggregate caps + extension-from-content-type. M3 shipped; residual same-org cleartext secrets. | **Medium (security)** |
| **2** | **§7.4 + §8.5 + §11.4-L1/I1 — Quality-gate remainder** | Tailwind source/artifact split; mutation coverage scope; unaudited procedure attaches; pre-existing leaky flash messages. | **None** — engineering hygiene. |
| **3** | **§10.12-fu1 / fu2 / fu4 — Token follow-ups** | Postgres reap race (Medium), prefix/throttle collision, test gaps. `fu3` (admin paused-state display) is complete. | **Low–Medium.** |
| **4** | **§14.3 / 14.4 / 14.6 / 14.7 / 14.8 / 14.12 — Alert tag follow-ups** | Parity, update-vs-delete scope decision, query cleanup, tag-swap-file purge, and remaining polish. `14.1`, `14.2`, `14.9`–`14.11` are complete. | **Low.** |
| **5** | **§15.7/15.8/15.10 — Observables follow-ups** | API parity (Q6), per-link provenance decision (Q7), redact raw values from mapping warnings (Medium residual). | **Low.** |
| **6** | **§16.6 — Empty-payload edge** | `{}` renders instead of "No raw data yet." | **Low.** |
| **7** | **Orphaned routes** | `ui-case-attachment-list` (+ dead template) and `ui-case-attachment-detail` (405) are URL-only. | **Low** — dead surface. |
| **8** | **§13 — Oversized-module maintainability backlog** | 20 candidates after the `ui/views.py` split; `cases/views.py` (~2.8k lines) is P0. `13.21` is complete. | **None** — hygiene. |
---

## 3. Open decisions


---

## 4. Environment & repository


---

## 5. Remaining phases

Phases 4–10 are complete and pass their gates on SQLite and Postgres; Phase 10 shipped in
commit `e3a94d8` (2026-10-07, §5.7). This section is now fully historical.


---

## 6. Deferred scope (committed, not yet scheduled)

- [x] **6.16 — UI completeness wave (all 24 gaps)** Plan `PLAN-2026-10-11-ui-completeness.md`. Restores the analyst loop and
  completes the entity surface: **P1** wire up built-but-unreachable features (attachments, merge, export,
  apply-template, taxonomy) + rail entries for Observables/Tags/Case-templates/Taxonomy + tenant indicator +
  case-template edit + password change + 500/403 pages + cheat-sheet as a server-rendered `<dialog>` + Font Awesome
  + keyboard coverage; **P2** alert triage, case edit, observable edit/delete; **P3** procedures/TTP UI; **P4**
  Settings→Ingestion tokens + Audit log (pulls §6.13 forward) — **token lifecycle + audit-log viewer shipped; see COMPLETED.md §6.13** (audit log at `ui-settings-audit-log`, offset pagination/filterable; plan named `ui-settings-audit`/keyset — deviation recorded there); **P5** playbook action round-trip + sanitized
  Markdown; **P6** global search — **SHIPPED 2026-11-02** (`6427beeb`; see §6.16-fu1 and `COMPLETED.md`);
  **P7** per-org realtime + presence + scoped HTMX adoption — **SHIPPED/VERIFIED 2026-11-26** (see `COMPLETED.md` and `VERIFY-2026-11-26-section6-realtime-and-search.md`). Open questions Q1 (Markdown dependency) gate
  P5 and Q2 (bulk stub) gates P1 — **Q2 resolved**: bulk UI shipped under §12.4 (`ui-case-bulk`/`ui-alert-bulk`/`ui-observable-bulk`/`ui-task-bulk`; case/alert/task use per-item loops, observable uses `compat/bulk.py bulk_patch`). Source: post-§6.15 UI audit (user decision "everything", 2026-10-11).
- [x] **6.16-fu1 — Global search follow-up: Postgres measurement.**
  P6 shipped 2026-11-02 (`6427beeb`): `/search` + `GET /api/v1/search`, shared `query/search.py`, org
  scoping, highlighting, paging, and tests.
  - [x] **Independent verification** — `docs/verification/VERIFY-2026-11-01-global-search.md`: **PASS**
    (34/35 ACs met, 1 superseded, 0 deviations).
  - [x] **T6 security review** — `docs/reviews/REVIEW-2026-11-13-global-search.md`: no new High/Medium
    findings; residuals documented.
  - [x] **Postgres measurement** — **DONE 2026-11-26**: 100k rows/entity, 18 measured shapes, full
    `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` artifacts. Final realistic run passed all p95 values
    under 500 ms; index adoption and residual stress breaches are documented in
    `VERIFY-2026-11-26-global-search-performance.md` and `docs/spec/deviations.md`.
  Full suite is green (2026-11-14: **3699 passed, 3 skipped, 2 subtests**); CI itself remains red
  (§12.11). The header-search ACs in `PLAN-2026-10-11-ui-completeness.md` are superseded by
  `PLAN-2026-11-02-search-page-rail-entry.md`; do not re-add a global header search form.

---

## 7. Process improvements identified

- [ ] **7.1 — Demonstrate failure modes for every AC** `R11`
- [ ] **7.2 — Keep the domain-expert gate on every phase, not just Phase 3**
- [ ] **7.3 — Require subagents to deliver the report they promise**
- [ ] **7.4 — Split the Tailwind source from the committed artifact** `ADR-003` — Today
  `ui/static/ui/app.css` is both the Tailwind input and the committed output, so `make css` is not
  idempotent and `make css-check` only proves the input compiles (it cannot detect drift). Introduce
  `ui/static/ui/app.src.css`, generate `app.css` from it, and make `css-check` fail when the tree's
  artifact is stale. Visual-regression risk: verify the tokens before switching the pipeline.
- [ ] **7.5 — Never accept a "pre-existing/unrelated" claim without a baseline; gate on the FULL suite.** Found
  2026-10-15: a subagent reported `test_ui_markdown.py::…::test_case_description_renders_as_markdown` as
  "pre-existing/unrelated". It was neither — that same task's template refactor had deleted the case
  **description** *and* the **ingestion-warnings** block from `ui/templates/ui/_tab_general.html`, and it
  survived review because only the feature's test *subsets* were executed. Both blocks are restored and the
  warnings block now has its first test. Three rules from it:
  (a) a failure is only "pre-existing" once it is **reproduced on the base commit** — otherwise report it as
  *unverified*, never as *unrelated*;
  (b) run the **whole** suite as the gate (~107 s — the long-standing "the full suite exceeds the 900 s shell
  timeout" assumption was simply wrong), not a hand-picked subset; a subset cannot see collateral damage;
  (c) a template/partial refactor must be diff-reviewed for **deleted** blocks, because block-rendering tests
  are frequently negative-only (`assertNotIn`) and keep passing when the block vanishes — every removed block
  needs a *positive* assertion (`tests/conformance/test_ui_markdown.py` had exactly that trap: its positive
  test caught this, its negative sibling would not have).

---

## 8. Standards (added 2026-10-03)

- [ ] **8.5 — Extend mutation coverage to the remaining contract assertions** `Low` — Postponed 2026-11-23: scope not clearly defined (which mutation tool/contracts/acceptance bar). Revisit only if a concrete contract-assertion gap or mutation-testing target is identified.

---

## 9. Scratch/probe hygiene

---

## 10. Case & identity enhancements — backlog (requested 2026-10-11, unscheduled)

Source: user request 2026-10-11 ("add for now so we don't forget"). **Unscheduled** — needs a plan
(`docs/planning/PLAN-…-case-model-and-admin.md`) + design decisions before implementation. Each item
below spans **model → migration → API/`case_json` → compat parity → UI → conformance tests**, so most are
non-trivial; the open design questions are collected at the end of this section. **10.2 (manual case
creation) shipped 2026-10-13** — see [`COMPLETED.md`](./COMPLETED.md); the remaining 10.3–10.8 are still
unscheduled. **See also §12** (analyst-impact gaps found 2026-10-12) — **10.7** (the "Similar" tab) is now
tracked as **12.3** together with the missing auto-bundle consumer, which is the more urgent half of the
backlog once the analyst-visible classification fields (10.3–10.6) are designed.

> **10.3–10.6 SHIPPED 2026-10-15** — `PLAN-2026-10-14-case-classification-fields.md`, commit `822d050`,
> verified 21/21 ACs at [`docs/verification/VERIFY-2026-10-14-case-classification-fields.md`](./docs/verification/VERIFY-2026-10-14-case-classification-fields.md)
> (security audit clean). Q-D1…Q-D5 below were answered as Q-D1…Q-D10 in §12 of that plan — note **Q-D2 went
> the opposite way** to this section's recommendation: `phase` shipped as a *separate* field, deliberately not
> folded into `CaseStatus`/`CASE_STAGES`. The bullets are kept verbatim as the original requirement text.

- [~] **10.7 — Case detail tabs.** Split the case page into tabs: **General, Custom, Alerts, Tasks,
  Observables, Attachments, Similar, Comments, Audit.** Server-rendered (§6/ADR-003: no inline JS;
  CSP `'self'`), keyboard-navigable, degradable to separate pages/sections without JS. Feeds:
  General = 10.3–10.6 + summary; Alerts = `case`↔`Alert` links; Tasks/Observables/Attachments/Comments =
  existing relations; **Similar** = the §2-Module-C cross-case observable linking — **now tracked as
  §12.3** (with the missing auto-bundle consumer; decide Q-D4 there, not here); **Audit** = the read-only
  `AuditLog` viewer (pull forward §6.13/P4).
  **Update 2026-11-03:** the missing **Custom** tab shipped — conditional on at least one
  `CaseCustomFieldValue`, placed after General with a count, editable via a plain-POST modal, and
  `GET /api/v1/case/{id}` now returns ordered `customFields`. Plan:
  [`PLAN-2026-11-02-case-custom-fields-tab.md`](./docs/planning/PLAN-2026-11-02-case-custom-fields-tab.md);
  65 targeted tests and `make check` green. Full suite green on the worktree at `4bab358`: 3305 passed,
  3 skipped. Open: independent verification (the TODO evidence rule) and the API write half below.
- [ ] **10.7-fu1 — Custom-field API write parity and independent verification.** `PATCH/PUT
  /api/v1/case/{id}` still ignores `customFields`; the UI writes through
  `POST /cases/<id>/custom-field`. Route both through one shared service, add API contract tests (invalid
  types, foreign value id, no-op), then run the feature's independent verification. Full suite is already
  green (3305 passed, 3 skipped). Source: custom-fields plan §Non-goals/§Risks.
- [ ] **10.10 — No pre-auth per-IP throttle on the webhook (requested 2026-10-15, ADR-008).** The
  per-IP bucket (`wh:ip:{ip}`) is consulted only **after** the token verifies, so an unauthenticated
  flood still costs one indexed token lookup + one Argon2 attempt per request. Add a cheap pre-auth
  per-IP limiter ahead of `_authenticate_token` (same `cache.incr` idiom, own bucket/documented limit
  so it cannot mask the post-auth 429), then confirm the budget in `docs/spec/api.md`. Consider a
  fail-closed setting under `amalthea/settings/prod.py` style (mirrors AGENTS.md §5).
- [ ] **10.12-fu1 — (Medium, real, Postgres-only) the last-token reap can delete a token created concurrently.**
  `settings_token_delete` does `source_reaped = not source.tokens.exists()` → `source.delete()`. A concurrent
  `settings_token_create` targeting the same feed is **not** serialised: `exists()` can miss the `get_or_create`
  row, then the feed delete cascades it (`IngestionToken.source` is `CASCADE`) — the new token vanishes with **no
  audit row and no error**. The audit's suggested `select_for_update` is a start but must cover **both** sides:
  locking only the reap path converts silent loss into an FK violation on the loser's INSERT (a 500). Needs a
  Postgres harness to test (folds into §3.1). Source: `docs/reviews/SECURITY-2026-10-19-…` M1.
- [ ] **10.12-fu2 — The token prefix does double duty (Low + Low).** The 12-char prefix of the plaintext — 72 bits,
  and a unique lookup key — is also the webhook throttle bucket (`wh:token:{prefix}:{window}`), while creation
  retries only 3× on collision. Same collision therefore merges two rate-limit buckets. Either hash the cache key
  and widen the prefix (migration), or document the retry window as accepted. Source: security review M2 + L2.
- [ ] **10.12-fu4 — Three test-quality gaps in the wave's own guards (Low; no grade depends on them).**
  (a) `TestTokenActivationLifecycle._mint` passes `slug=` into `_mk_source` for a feed the view never uses — it
  binds via `slugify(source_name)`, so every token in that class lands on one shared feed and the `slug=` row is
  orphaned; the argument does not mean what it reads like (Plan B's `_mint` was fixed, Plan A's was not).
  (b) The **Revoke** affordance on an *active* row is rendered (`settings_sources.html`) but unpinned — the
  state-matrix test asserts only Deactivate/Activate/Delete. (c) The `source.delete()` rollback path (a raise
  mid-transaction must restore the token row) is correct by construction and untested. Source: verifier O1–O3.

**Open design questions (resolve in the plan before coding):**

> **Resolved 2026-10-14** — Q-D1…Q-D5 were all settled (and widened to Q-D1…Q-D10) in §12 of
> [`PLAN-2026-10-14-case-classification-fields.md`](./docs/planning/PLAN-2026-10-14-case-classification-fields.md),
> which shipped as commit `822d050`. The questions are kept below only as the pre-decision context; do not
> re-litigate them without reading §12 first.

- **Q-D1** — Scope / Type / Data-classification: first-class `Case` enum fields (needs `choices` + a
  `CheckConstraint` per `core/enums.py`: "enforce every closed set twice"), or `CustomField`s
  (`CaseCustomFieldValue` already exists)? Enum fields are queryable/indexable and become part of the
  TheHive `case_json` contract; CustomFields are flexible but not schema-enforced. Recommendation: enums
  for Scope/Classification, a `CaseType` table (or enum) for Type.
- **Q-D2** — `phase` vs the existing `CaseStatus`/`stage`. This is the biggest design risk (two
  overlapping lifecycle axes confuse both analysts and the automation triggers). Recommendation: model
  the IR phases as the *stages* of a redesigned `CaseStatus` vocabulary, not a parallel field.
- **Q-D3** — TheHive `compat/` parity: do these fields appear in `case_json`/`case_create`? Adding keys
  is contract-visible — extend the golden corpus + `test_enum_contracts.py` alongside.
- **Q-D4** — "Similar" tab semantics: shared observables (Module C) only, or theHive-style
  `case/links`(?) — check `identity/scoping` + existing correlation code before committing.
- **Q-D5** — Permissions: who may set Scope/Classification (any writer vs admin)? Classification may
  warrant a stricter gate + audit.

---

## 11. Security follow-ups from the P5 review (2026-10-12)

Source: `docs/reviews/REVIEW-2026-10-12-ui-completeness-p5.md` (0 Critical / 0 High; everything else in
the batch was cleared). These three are **decisions first, code second** — M1 and M3 change authorization
semantics, so they need a policy call before a fix.


### 11.4 — P3 (procedures/TTP UI) review follow-ups (2026-10-12)

From `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (0 Critical / 0 High; verification
`docs/verification/VERIFY-2026-10-12-ui-completeness-p3.md` = 5/5 ACs met).

- [ ] **L1 — procedure attach is unaudited.** Neither the single nor the bulk view writes an `AuditLog`
  row or a case-ledger event, so an attached procedure does not show up on the case timeline. §9 of the
  plan did not name procedures and the compat API behaves the same way (parity), so this is "audit it, or
  accept it explicitly". Decide together with the ledger-event work.
- [ ] **I1 — pre-existing leaky flash messages.** Seven `except Exception as exc: messages.error(...{exc})`
  sites reflect internal exception text to the analyst (`ui/views.py:367,985,1167,1228,1579,1959,1990`).
  P3 introduced no new ones (it logs the detail and flashes a generic string); this is a small mechanical
  cleanup, best done as one sweep.
- **Fixed in P3 (no action):** the unbounded/quadratic bulk item loop (now `MAX_BULK_PROCEDURES = 100` +
  hoisted `getlist`, Low L2); the undefined CSS tokens (Low→fixed, now guarded by
  `tests/conformance/test_ui_css_tokens.py`); the per-item savepoint bug in bulk create.
- **Accepted (Info):** `ttps=TTP.objects.order_by("name")` is global — `TTP` is shared reference data with
  no `owner_org` (intended); no rate limiting on the new mutations (P2 parity, tracked with §S-items).

---

## 12. Analyst-impact gaps (found 2026-10-12, post-viewer-wave UI audit)

Found while closing out the viewer wave. The **write** side is now well covered — every mutation is gated,
audited and enumerated by tests — but several **read/discovery** surfaces an analyst needs are missing,
truncated or decorative. None of these is a regression (they shipped that way) and none is in a plan yet.

Recommended: one small plan (`PLAN-…-analyst-ergonomics`) covering **§12.1 + §12.2 + §12.4** together, since
they share the list-view plumbing and are cheap. **§10.7/Q-D4 is now settled**: §12.3's "Similar" surface was
re-opened and shipped 2026-10-18 as a read-only, correlation-ranked panel plus a correlation-ranked, audited
attach path — **automatic** ingest-time bundling shipped 2026-11-26 as §12.3-fu1. §6.16 P6
(search) shipped 2026-11-02 (`6427beeb`) after the §12.1 pager fix; its verification/security/Postgres
follow-up was §6.16-fu1 and completed 2026-11-26. §6.16 P7 (realtime) shipped and verified 2026-11-26 in
`PLAN-2026-11-26-section6-realtime-and-search.md`.

- [ ] **12.12 — Fuzzy-similarity follow-ups (security FIND-05/03/06/07/11 + one discovered bug), 2026-10-21.**
  From `docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md` and Phase E verification:
  - [x] **12.12-fu1 (High, by design — FIND-05) — RESOLVED 2026-11-13 (Option B title gate).** Decision (A)
    still excludes `title` from the payload hash, but the Similar-alerts helper now requires payload SSDEEP
    similarity **and** normalized-title Jaccard similarity against the **same** case alert. Defaults: payload
    60, title 70, gate on; `AMALTHEA_ALERT_FUZZY_TITLE_GATE=false` restores payload-only behavior. The title
    fingerprint is computed on demand — no migration or backfill. Plan `PLAN-2026-11-13-alert-fuzzy-title-gate.md`,
    brief `IMPL-2026-11-13-alert-fuzzy-title-gate.md`, runbook `docs/runbook.md`, verification
    `VERIFY-2026-11-13-alert-fuzzy-title-gate.md` (PASS). Focused suites: 46 + 35 passed; `make check-fast`
    green. Full suite remains the standing CI gap (§12.11).
  - [ ] **12.12-fu2 (Medium — FIND-03).** `ppdeep.hash/compare` CPU is unbounded on large canonical text
    (5 MiB body × 500 candidates). Add `ALERT_FUZZY_MAX_TEXT_SIZE` truncation and/or lower the `case_hashes`
    cap; **measure on Postgres** (ties to risk R5, blocked on a Postgres harness). Consider async/cached
    computation for the Similar tab.
  - [ ] **12.12-fu3 (Medium — FIND-06).** `VOLATILE_KEYS` is a static list and may miss vendor-specific keys
    (`flow_id`, `session_id`, `correlation_id`, …), causing **false negatives**. Add
    `ALERT_FUZZY_EXTRA_VOLATILE_KEYS` (settings-extensible) and/or a value-pattern heuristic (UUID/timestamp).
  - [ ] **12.12-fu4 (Low — FIND-07/FIND-12).** Document that `backfill --force` skips when recomputation
    yields `""` (does not blank) and that scalar-list sorting collapses order-significant arrays (false-positive
    risk); consider a `--clear-on-failure` flag and per-source list-sort config later.
  - [ ] **12.12-fu5 (Low — FIND-11).** `ppdeep`'s hash was hand-added to `requirements/base.txt`; there is no
    `make lock` recipe (empty `.PHONY` target). Implement `pip-compile --generate-hashes` locking to prevent
    drift (overlaps the existing `make lock`/`make hooks` follow-up).
  - [ ] **12.12-fu6 (bug, pre-existing, out of scope).** `ui/views.py::case_alert_link` shows a false
    **success** banner on a refused cross-org merge: `record_alert_bundled` raises
    `PermissionDenied("Cross-org alert bundle refused")` for **every** role (superAdmin included), but the
    view's `messages.success(...)` runs **after** the `except PermissionDenied` branch, so the user is told
    "Alert linked to case N." while the transaction rolled back. The invariant is correct (cross-org bundling
    is refused regardless of role) — only the feedback is wrong. Fix: return/relay the error message instead
    of success when the merge is refused. Pinned by
    `test_alert_fuzzy_similarity.py::TestCrossOrgIsolation::test_superadmin_cross_org_link_is_refused_and_not_persisted`.
    **Not part of the fuzzy feature.**
---

## 13. Maintainability — oversized modules (added 2026-11-04)

Follow-up audit after the `ui/views.py` → `ui/views/` split (`64eef0c`). These are refactor candidates, not
product features. Priority: **P1** next wave; **P2** when the module is next touched or before adding
features; **P3** monitor only — do not split purely for line count. The P0 item (§13.1) and the small
template-hygiene cleanup (§13.21) are complete and recorded in `COMPLETED.md`.

**Definition of done for every §13 task:** move-only where possible; preserve public imports, URL names/order,
auth and tenant checks, status codes, request/response bodies, form field names and templates; add compatibility
re-exports where callers/tests patch the old path; no schema changes unless separately planned; `make check` and
the full `pytest` suite green; record any deviation in the plan.

### P1 — next wave

- [ ] **13.2 — Split `ui/taxonomy_views.py` into taxonomy-view modules.**
  Current: **787 lines** covering case statuses, alert statuses, observable types and taxonomy terms, with
  repeated error helpers. Target: `ui/taxonomy_views/` (or a `ui/views/taxonomy/` package) with `_common.py`,
  `case_statuses.py`, `alert_statuses.py`, `observable_types.py`, `terms.py`; keep URL imports and
  `ui/views/taxonomy.py` context helpers intact. Source: §13 audit 2026-11-04.
- [ ] **13.3 — Split `ui/views/cases.py` further.**
  Current: **1,002 lines** — the largest module left by the recent split — mixing case list/create/edit, status,
  close, tags, comments, custom fields, export, shares and merge. Target: `case_list.py`, `case_edit.py`,
  `case_actions.py`, `case_shares.py` (package or siblings; keep `ui/views/cases.py` as a compatibility shim).
  Watch: template/HTMX partial names and any tests that import or monkeypatch `ui.views.cases.*`.
  Source: §13 audit 2026-11-04.
- [ ] **13.4 — Split `alerts/views.py` into an `alerts/views/` package.**
  Current: **782 lines** mixing alert CRUD, observables, merge/import, bulk update, procedures, statuses, tags
  and comments. Target: `alerts/views/` with `alerts.py`, `observables.py`, `merge.py`, `bulk.py`,
  `procedures.py`, `statuses.py`, `comments.py`; mirror the `cases/views/` layout. Watch: `alerts/urls.py`
  ordering and the same tenant/auth contracts. Source: §13 audit 2026-11-04.

### P2 — when touched or before feature work

- [ ] **13.5 — Split `ingest/mapping.py` into a mapping package.**
  Current: **842 lines** combining JSONPath extraction, value coercion, config normalization, validation,
  transform application and correlation-key logic. Target: `ingest/mapping/` with `jsonpath.py`, `values.py`,
  `config.py`, `apply.py`, `correlation.py`. High regression risk: add/keep characterization tests for every
  public function before moving code. Source: §13 audit 2026-11-04.
- [ ] **13.6 — Split `core/serializers.py` by domain.**
  Current: **720 lines** of mostly independent serializers. Target: `core/serializers/` with `cases.py`,
  `alerts.py`, `observables.py`, `identity.py`, `taxonomy.py`, `automation.py`, plus compatibility re-exports.
  Low risk and a good cleanup when serializers are next changed. Source: §13 audit 2026-11-04.
- [ ] **13.7 — Split `ui/views/automation.py`.**
  Current: **688 lines** combining playbook-config parsing with list/detail/create/edit/delete/run views
  (plus the playbook-scripts endpoint added 2026-11-12).
  Target: `playbook_config.py` for parser helpers and `playbooks.py` for views (or an `automation/` package);
  preserve `_parse_playbook_config` / `_get_script_source` compatibility for tests. Source: §13 audit 2026-11-04.
- [ ] **13.8 — Split `identity/views.py` into organisation and user views.**
  Current: **617 lines** covering both admin domains. Target: `identity/views/organisations.py` and
  `identity/views/users.py` (or sibling modules) with compatibility exports. Watch: superadmin guards, audit
  rows and URL names. Source: §13 audit 2026-11-04.
- [ ] **13.9 — Split `ui/views/observables.py`.**
  Current: **609 lines** mixing observable list/detail/edit/delete with case link/update/remove actions.
  Target: `observables.py` for CRUD and `case_observables.py` for case-link actions. Watch: cross-case guard
  and `dataType` form contract. Source: §13 audit 2026-11-04.
- [ ] **13.10 — Split `ui/views/settings.py`.**
  Current: **594 lines** mixing organisation settings, user settings and the settings home. Target:
  `settings_orgs.py`, `settings_users.py`, `settings_home.py` with compatibility exports. Watch: org-blocker
  helper and superadmin-only paths. Source: §13 audit 2026-11-04.
- [ ] **13.11 — Split `query/engine.py` only if the query DSL is growing.**
  Current: **759 lines** but cohesive (field specs, Q building, sorting, execution). Target if needed:
  `fields.py`, `filters.py`, `execution.py`. Do not split purely for line count; preserve the public
  `QueryEngine` surface. Source: §13 audit 2026-11-04.
- [ ] **13.12 — Split `tests/conformance/test_tenant_scoping.py` by resource.**
  Current: **3,649 lines**, the largest test file in the repo. Target: cases, alerts, observables, identity and
  automation tenant-scoping suites (shared fixtures in a conftest/helper). Test-maintainability only; no
  production behaviour change. Source: §13 audit 2026-11-04.
### P3 — monitor; do not split for size alone

- [ ] **13.13 — `cases/models.py` (677 lines).** 17 Django models. Do **not** split for line count now: model
  module moves risk migration/app-registry churn. Revisit only if the case domain grows substantially.
  Source: §13 audit 2026-11-04.
- [ ] **13.14 — `observables/extractor.py` (570 lines).** Cohesive extraction/normalization/linking pipeline.
  Split only when adding a new extraction stage. Source: §13 audit 2026-11-04.
- [ ] **13.15 — `cases/correlation.py` (518 lines).** Cohesive correlation algorithms; leave unless correlation
  features expand. Source: §13 audit 2026-11-04.
- [ ] **13.16 — `ui/views/ingestion_sources.py` (1,172 lines).** Mapping-editor UI including the four source-detail
  tabs and submapping editor; split preview/apply from source detail when next touched. Source: §13 audit 2026-11-04.
- [ ] **13.17 — `observables/views.py` (488 lines).** Observable-type CRUD plus the suggestion workflow; split
  `types.py` and `suggestions.py` when touched. Source: §13 audit 2026-11-04.
- [ ] **13.18 — `identity/services.py` (471 lines).** Organisation/user services with shared locking logic;
  split when next modified. Source: §13 audit 2026-11-04.
- [ ] **13.19 — `ui/static/ui/app.src.css` (1,396 lines).** Cohesive Tailwind source; split with `@import` only
  if component CSS churn increases. Preserve `make css-check` and the `@source` scanning contract.
  Source: §13 audit 2026-11-04.
- [ ] **13.20 — Large templates: `ui/templates/ui/taxonomy.html` (647) and `_tab_observables.html` (479).**
  Prefer template partials over Python-style module splits, only when the templates are next edited.
  Source: §13 audit 2026-11-04.

---

## 14. Alert tag management follow-ups (2026-11-05 review)

The P1 defects found in the 2026-11-05 review are fixed: empty submissions now redirect instead of
500-ing, comma-separated names are split/clamped/deduped inside one transaction, the Add-tags card is
visible on linked alerts, and the API dedupes repeated names before inserting. Regression coverage:
`tests/conformance/test_alert_tag_add_regressions.py` (5 tests); targeted suite 108 passed and
`make check` green. The items below are the remaining P2/P3 findings, ordered by risk.
Closed follow-ups `14.1`, `14.2`, and `14.9`–`14.11` are recorded in `COMPLETED.md`.

### P2 — correctness, parity and tenant safety

- [ ] **14.3 — Reconcile the documented idempotency with the actual 400.**
  `alerts/views.py:730-731` says re-linking an existing tag is a no-op, but `alerts/views.py:751-758`
  returns 400 for an existing name. Choose one contract: return 201/the alert's tag set unchanged
  (true idempotency, matching `alert_tag_link`), or document the 400 as intentional. Add a test pinning
  the decision.
- [ ] **14.4 — Scope the picker to the alert's organisation, not the caller's.**
  This item is MOOT under the approved Q1 decision: the Tag catalog is global (no owner_org column),
  and the picker mirrors case detail exactly. See PLAN-2026-11-24-alert-tag-picker-parity.md §12.
- [ ] **14.6 — Give alert-tag writes case-tag parity.** `ui/views/alerts.py:487-510` writes
  `AlertTagLink` directly: no `AuditLog`, no ledger entry when the alert is linked to a case, and no
  WebSocket frame. Route through a shared service (or add the audit/ledger/broadcast explicitly) so the
  alert and case tag flows behave the same and the Audit tab is truthful.
- [ ] **14.7 — Restore the plan/reference and broaden the tests.** §12.14 points at
  `docs/planning/PLAN-2026-11-04-alert-tag-management.md`, which does not exist; `/alert/{id}/tag/add`
  is also absent from `docs/spec/api.md`. Write or restore the plan (or correct the reference), document
  the endpoint and its idempotency contract, and extend coverage beyond the P1 regressions: alert-detail
  available/current tag filtering, the modal's 400/404 paths, tenant scoping, and idempotency.
- [ ] **14.8 — Decide the fate of the redundant `/alert/{id}/tag/add` endpoint.** The route
  (`alerts/urls.py:28`) is undocumented, unused by the UI (which posts to the UI view), and duplicates
  the existing POST-on-`/tag` semantics. Prefer removing it unless a consumer needs the explicit
  endpoint; if kept, document it and add it to the API/test contract. If removed, update
  `tests/conformance/test_tenant_scoping.py` route enumeration.

### P3 — polish and housekeeping

- [ ] **14.12 — Purge the historical editor swap file.** `*.swp` is committed in `.gitignore`
  (`8fb33229`) and no swap files are tracked, but a `.swp` blob remains in git history; purge it per
  `SECURITY.md` before the repository is mirrored or copied.

---

## 15. Alert observables from source mappings (2026-11-06)

Plan: [`PLAN-2026-11-06-alert-observables.md`](docs/planning/PLAN-2026-11-06-alert-observables.md)
(APPROVED; decisions §14; 28 ACs). This is the P6 candidate from
`PLAN-2026-10-16-source-field-mapping.md`. Status: 15.1–15.6/15.9 shipped 2026-11-06 (28/28 ACs
verified; residual risk §15.10).

- [ ] **15.7 — Deferred API parity (Q6).** A future `GET /api/v1/alert/{alertId}/observable` read
  endpoint is intentionally omitted until a client needs it.
- [ ] **15.8 — Deferred provenance decision (Q7).** Explain and decide the trade-off for a per-link
  `AlertObservable.source_path` column before a migration-backed provenance wave.
- [ ] **15.10 — Security follow-up: redact raw values from mapping warnings.** The AC28 review found
  that `FieldWarning.value_any` can store raw payload values (emails, IPs) in
  `Alert.ingestion_warnings`. This is pre-existing (not introduced by §15) and not a blocker, but the
  warning text should redact or hash sensitive values. Source: `SECURITY-2026-11-06-alert-observables.md`
  §7 (Medium residual).

---

## 16. Source detail: Mapping + Raw data tabs (2026-11-06)

Plan: [`PLAN-2026-11-06-source-detail-raw-data.md`](docs/planning/PLAN-2026-11-06-source-detail-raw-data.md)
(IMPLEMENTED; decisions §4; ACs SRC-RAW-1..8). Impl brief:
[`IMPL-2026-11-06-source-detail-raw-data.md`](docs/planning/IMPL-2026-11-06-source-detail-raw-data.md).
Status: 16.1–16.5 shipped 2026-11-06 (8/8 ACs met; independent verification PASS WITH NOTES).

- [ ] **16.6 — Decide the empty-payload edge.** An alert whose `raw_payload` is the default `{}` renders
  `{}` in `<pre>` rather than the "No raw data yet." empty state (the plan never specified this case).
  Low priority; either show `{}` intentionally or treat a falsy payload as empty. Verifier observation (a).

---

## 26. Enrichment playbook script — RDAP domain lookup (added 2026-11-24)

Status: **BACKLOG — open questions below; defaults proposed, not yet confirmed.**
Scope: one deploy-only script plus tests/docs; no schema, API, or UI change.

### 26.1 Summary

Add `automation/scripts/rdap_domain_lookup.py` following the ADR-011 script contract
(`NAME` / `DESCRIPTION` / `TRIGGER_HINT`, `run(**values) -> str`). When a playbook with
`trigger_event="observable.created"` and `config.action_path="rdap_domain_lookup"` fires for an
observable whose `observable_type` is `domain`, the script performs an HTTPS RDAP lookup and returns
a bounded plain-text summary — registrar, creation date, computed domain age, expiry date, and
registration status — via the existing `AutomationRun.output_log` → case timeline feedback loop.

### 26.2 Goals

- Reuse the existing script loader/executor and feedback loop; no new endpoint, model, or migration.
- Skip non-domain observables with a short explanatory line instead of failing the run.
- Use HTTPS only, with bounded timeout/response size and no caller-supplied URLs.
- Keep CI offline: mock the bootstrap and RDAP responses in tests.
- No secrets in the script; any future configuration must come from environment variables.

### 26.3 Non-goals

- No write to `Observable.enrichment_data`; structured JSON storage is open question Q2.
- No seeded/auto-enabled playbook unless Q1 chooses one; no UI changes.
- No RDAP lookup for `ip`, `url`, `fqdn`, or `mail` observables unless Q4 expands the scope.
- No new third-party dependency; prefer the standard library for HTTP and JSON.

### 26.4 Tasks and acceptance criteria

- **T1 — Script.** Add `automation/scripts/rdap_domain_lookup.py` with module metadata and
  `run(**values) -> str`. Normalise the `observable` value to a lowercase hostname, gate on
  `observable_type == "domain"`, fetch RDAP over HTTPS, and return a compact summary.
  - **AC1.1:** `python manage.py list_playbook_scripts` discovers `rdap_domain_lookup`.
  - **AC1.2:** A non-domain observable returns a short skip line and makes no HTTP request.
  - **AC1.3:** A valid mocked RDAP response produces a summary with registrar, created date,
    computed age in days, expiry date, and status, bounded well under `MAX_OUTPUT_BYTES`.
  - **AC1.4:** Missing or malformed RDAP data produces a compact failure line, no traceback.
- **T2 — Network safety.** Resolve the RDAP server from an HTTPS IANA DNS bootstrap document,
  allowlist the resolved host, refuse redirects to non-allowlisted hosts, cap timeout and response
  body, and validate the domain value so it cannot become an internal/SSRF target.
  - **AC2.1:** No plain HTTP, arbitrary URL, or redirect outside the allowlist is followed.
  - **AC2.2:** No secret or credential is embedded in code, tests, or docs.
- **T3 — Tests.** Add offline conformance coverage for discovery, the type gate, normalisation,
  success summary, malformed response, network failure, and SSRF/allowlist guards.
  - **AC3.1:** All RDAP tests pass with mocked HTTP and no live network.
- **T4 — Docs.** Update the `docs/spec/automation.md` runbook example to mention the new script
  and record the resolved answers from §26.5 here.
  - **AC4.1:** Docs describe how to configure the playbook and the no-secrets rule.

### 26.5 Open questions (defaults proposed, unconfirmed)

1. **Q1 — Delivery:** script only, or also add a seeded inactive playbook? Default: **script only**.
2. **Q2 — Output:** plain-text `output_log` summary, or structured `enrichment_data` JSON?
   Default: **plain text only**; structured storage needs a separate write path.
3. **Q3 — RDAP discovery:** IANA DNS bootstrap + allowlisted RDAP host, or a public resolver such
   as `rdap.org`? Default: **IANA bootstrap + allowlist**.
4. **Q4 — Type scope:** `domain` only, or also `fqdn` / `url`? Default: **`domain` only**.
5. **Q5 — Failure policy:** return a short failure line and keep the run `Success`, or raise
   `ActionError` so the run is `Failed`? Default: **raise `ActionError`** for lookup/parse failure;
   simple skip line for non-domain observables.

### 26.6 Next action

Confirm or correct Q1–Q5, then implement T1→T4 in order. Record any deviation from the answers in
this section before merge.
