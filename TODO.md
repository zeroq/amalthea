# TODO — Amalthea

Open work only. Shipped work and closed-item history live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-11-13 — reconciled search (§6.16 P6): it shipped 2026-11-02 (`6427beeb`), so the
priority row is now the narrow **§6.16 P6-fu1** (independent verification, security review, Postgres
measurement). The original header-search ACs are superseded by
`PLAN-2026-11-02-search-page-rail-entry.md`. Also archived §18/§19 and the playbook script picker to
[`COMPLETED.md`](./COMPLETED.md); the §19 alert-import security review remains §21. Full suite last run
2026-11-13 (**3673 passed, 3 skipped, 2 subtests passed**, 18m16s), covering search and the playbook
picker. The priority table below is the source of truth for open work.

---

## 🎯 NEXT HIGHEST-IMPACT ITEMS (Priority Order)

Ordered by **impact on the analyst** — the daily IR loop first, then deployment security, then
engineering hygiene. Shipped rows were removed in the 2026-11-08 cleanup; the shipped search row moved
to its narrow follow-up on 2026-11-13 (see `COMPLETED.md`).

| Priority | Item | Description | Impact |
|----------|------|-------------|--------|
| **1** | **§12.11 — CI red; full suite never run** | Secrets job times out (full-history scan not running); the "migrations, suite" step does not run the suite. | **Highest leverage** — every other claim is unbacked by CI. |
| **2** | **§12.3-fu1 — Automatic ingest-time bundling** | `correlation_key` is derived/indexed but only the manual Similar/attach path consumes it. Auto-bundle was deferred (Q1b). Depends on §12.1-fu1. | **High** — the orchestration promise of Modules B+C. |
| **3** | **§12.4 — No bulk actions in the UI** | `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk` exist in the API; the UI stub was deleted (Q2). | **Medium-High** — 200-alert mornings are single-item work. |
| **4** | **§21 — Alert-import security review** | Implementation committed (`ff139692`) and AIMP-1..AIMP-7 MET; the P5 security review is the only remaining step. | **Medium** — closes the §19 exchange/debug surface safely. |
| **5** | **§6.16 P7 — Per-org realtime breadth + presence** | Scoped HTMX adoption (D4/D5), broader live events, presence. | **Medium** — collaboration polish; ledger already syncs. |
| **6** | **§10.1 — Role-scoped user management** | An `admin` manages own-org users (today `superAdmin`-only). | **Medium** — ops/onboarding, not triage. |
| **7** | **§6.13 / §6.16 P4 — Ingestion tokens + audit-log viewer** | **Partial:** token lifecycle shipped (10a–c); read-only `AuditLog` viewer remains. | **Medium** — audit viewer is the accountability half. |
| **8** | **S3 + S5 (+ M3 residual)** | **S3** per-account lockout on the API login + stop trusting client `XFF`. **S5** attachment aggregate caps + extension-from-content-type. M3 shipped; residual same-org cleartext secrets. | **Medium (security)** |
| **9** | **§12.1-fu1 — Measure filtered-list `COUNT` on Postgres** | Unmeasured `COUNT` on 12 paginated lists; prerequisite for auto-bundling. | **Medium** — pager truthfulness at scale. |
| **10** | **§2.6 + §2.7 + §7.4 + §8.5/§8.7 + §11.4-L1/§11.5** | Coverage ≥ 80%; Phase-12/T2 QA list; Tailwind source/artifact split; mutation coverage; unaudited procedure attaches; admin role model; log forging. | **None** — engineering hygiene. |
| **11** | **§10.9 — Status vocabularies have no human labels** | UI prints raw tokens (`InProgress`); add labels without breaking `stage`-keyed automation. | **Low** — cosmetic. |
| **12** | **§10.11 — Reload mints a second ingestion token** | 200 body re-submits on reload; one-shot nonce or explicit nudge. | **Low** — admin-only exposure. |
| **13** | **§10.12-fu1…fu4 — Token follow-ups** | Postgres reap race (Medium), prefix/throttle collision, admin paused-state display, test gaps. | **Low–Medium.** |
| **14** | **§14.1–14.12 — Alert tag follow-ups** | Id validation, parity, truncation text, a11y, query cleanup, swap-file purge. | **Low.** |
| **15** | **§15.7/15.8/15.10 — Observables follow-ups** | API parity (Q6), per-link provenance decision (Q7), redact raw values from mapping warnings (Medium residual). | **Low.** |
| **16** | **§16.6 — Empty-payload edge** | `{}` renders instead of "No raw data yet." | **Low.** |
| **17** | **Orphaned routes** | `ui-case-attachment-list` (+ dead template) and `ui-case-attachment-detail` (405) are URL-only. | **Low** — dead surface. |
| **18** | **§12.9 — UI layout/style standardization** | Input/button heights, compact action columns, table/filter convention; codify guidelines. Includes the new alert/case severity rows (`.severity-filter-row`, `.severity-filter--N`) as the canonical filter pattern. | **Low.** |
| **19** | **§13 — Oversized-module maintainability backlog** | 20 candidates after the `ui/views.py` split; `cases/views.py` (~2.8k lines) is P0. | **None** — hygiene. |
| **20** | **§6.16 P6-fu1 — Search verification + security + Postgres measurement** | Search shipped (`6427beeb`); write `VERIFY-2026-11-01-global-search.md`, record the T6 security review, and run `EXPLAIN` on seeded Postgres before deciding on a trigram index. | **Medium** — closes the last evidence gap; no user-facing feature is missing. |

---

## 2. Quality gates not yet met

- [ ] **2.6 — Restore coverage ≥ 80%** Open since 2026-10-11: the tenant-isolation + org-management code
  (scoping/permissions/services, the Settings UI guards) took coverage to **79%**, below the configured
  `fail_under = 80` (informational until now). Add conformance tests for the new identity/scoping/UI
  surfaces (guards, cross-tenant 404s, `members=reassign|delete` matrix, the no-JS Settings CRUD).
  Source: `make coverage` 2026-10-11.
- [ ] **2.7 — Phase 12 / T2 test hardening (the still-open QA list).** From `VERIFY-2026-10-10-t2-and-phase12.md`
  (3 MET / 3 DEVIATED / 10 NOT MET): delete-with-runs row survival, the merge children matrix, attachment
  boundaries (exact cap / empty / batch-partial), the timeout cap (30 vs 31), run-now `Success` + `output_log`,
  `SESSION_COOKIE_HTTPONLY`, the UI login throttle; **§8.5** mutation guards for blast-radius / share / TTP /
  delete-with-runs / attachment; and extend `test_ui_urls_smoke.py` to Python-only `redirect` names
  (verifier N3). Independent of §2.6 (coverage % vs specific assertions).

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

- [ ] **6.13 — Backlog: ingestion-token management + audit-log viewer (superAdmin Settings)** Split out of §6.12 (decision Q1/Q8). Settings → **Ingestion tokens** (per-org webhook tokens: create/revoke, show-once secret) and read-only Settings → **Audit log** (the `AuditLog` already records superAdmin cross-org reads). Not scheduled.
- [ ] **6.16 — UI completeness wave (all 24 gaps)** Plan `PLAN-2026-10-11-ui-completeness.md`. Restores the analyst loop and
  completes the entity surface: **P1** wire up built-but-unreachable features (attachments, merge, export,
  apply-template, taxonomy) + rail entries for Observables/Tags/Case-templates/Taxonomy + tenant indicator +
  case-template edit + password change + 500/403 pages + cheat-sheet as a server-rendered `<dialog>` + Font Awesome
  + keyboard coverage; **P2** alert triage, case edit, observable edit/delete; **P3** procedures/TTP UI; **P4**
  Settings→Ingestion tokens + Audit log (pulls §6.13 forward); **P5** playbook action round-trip + sanitized
  Markdown; **P6** global search — **SHIPPED 2026-11-02** (`6427beeb`; see §6.16-fu1 and `COMPLETED.md`);
  **P7** per-org realtime + presence + scoped HTMX adoption. Open questions Q1 (Markdown dependency) gate
  P5 and Q2 (bulk stub) gates P1. Source: post-§6.15 UI audit (user decision "everything", 2026-10-11).
- [ ] **6.16-fu1 — Global search follow-up: verification, security review, Postgres measurement.**
  P6 shipped 2026-11-02 (`6427beeb`): `/search` + `GET /api/v1/search`, shared `query/search.py`, org
  scoping, highlighting, paging, and tests. Remaining: (a) write
  `docs/verification/VERIFY-2026-11-01-global-search.md` against the original plan's T7 checklist;
  (b) run the T6 security review (scoping, caps, highlight/XSS, CSP, viewer, superAdmin API audit) and
  record the result; (c) run `EXPLAIN (ANALYZE, BUFFERS)` on seeded Postgres for filtered
  case/alert/observable searches and add a trigram index only if the measurement warrants it. Full
  suite is green (2026-11-13: **3673 passed, 3 skipped, 2 subtests**); CI itself remains red (§12.11).
  The header-search ACs in `PLAN-2026-10-11-ui-completeness.md` are superseded by
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

- [ ] **8.5 — Extend mutation coverage to the remaining contract assertions**
- [ ] **8.7 — Timestamp-unit wording in plan §7.1** Verifier (2026-10-07), POST-closure note

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
- [ ] **10.9 — Status vocabularies have no human `label`, so the UI prints raw stored tokens.** Found
  2026-10-15 while fixing TLP/PAP display (the TLP/PAP row rendered raw integers; now uses a zero-safe
  `choice_name` filter). Every *closed set* except statuses renders a human label
  (`choice_label` / `choice_name`, `core/enums.py`), but `CaseStatus` exposes only `value`/`stage`/`order`/
  `hidden` and its `__str__` is `value`, so `_tab_general.html` and `case_list.html` print
  `{{ case.status.value }}` — and `value` is a free `CharField(64)`, so whatever an operator seeded is what
  the analyst sees ("InProgress", not "In Progress"). The same holds for `AlertStatus.value`
  (`alert_detail.html`, `alert_list.html`, `_tab_alerts.html`) and for `Task.status`, whose labels are
  literally identical to their tokens (`TASK_STATUS_CHOICES`: `Waiting`/`InProgress`/`Completed`/`Cancel`).
  Fix options: add a `label` to `CaseStatus`/`AlertStatus` (+ data migration, keeping ADR-002 §D11-tolerant
  `resolve_case_status`), or a `*_LABELS` mapping keyed by token, or fix `TASK_STATUS_CHOICES` labels.
  **Deferred**: purely cosmetic today, and touching `CaseStatus` risks the automation triggers that key on
  `stage` (see §10.6/Q-D2) — which is exactly why it is its own item, not a rider on 10.3–10.6.
- [ ] **10.11 — Reloading the "create ingestion token" POST mints a second live token.** Success is a
  200 body, not a redirect — deliberately, because a PRG would have to park the plaintext somewhere
  server-side (session, cache, signed URL) to re-render it, and the whole point is that the secret
  exists in exactly one response. The side effect: a browser reload of that response re-submits the
  form and creates *another* token, invisible until someone looks at Settings → Sources. Fix options:
  a one-shot nonce in a hidden field (server-cached; the reload returns the same 200 without
  re-creating), or an explicit "create another token?" nudge. Exposure is low (authenticated admin,
   their own org, the token is listed) — hence deferred rather than fixed blind.
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
- [ ] **10.12-fu3 — Django admin renders a paused token as neither readable state (Low).**
  `IngestionTokenAdmin.list_display` omits `deactivated_at`, so in the read-only admin a paused credential looks
  like a live one. Found **independently** by both passes (security L1; verifier O6). Source: security review L1.
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

### 11.5 — Viewer-role review follow-ups (2026-10-12)

From `docs/reviews/REVIEW-2026-10-12-viewer-role-enforcement.md` (0 Critical / 0 High; round 1 L1
fixed in the same batch). These two are **accepted, Low/Info** — recorded here so they are tracked
rather than only described in `docs/spec/deviations.md`.

- [ ] **L2 — Django admin sits outside the role model.** `role` is orthogonal to `is_staff` /
  `is_superuser`, and `/admin/` (`amalthea/urls.py`) is reached by none of the three enforcement
  layers (global DRF permission, `not_viewer`, or the resolvers). A `viewer` who is also `is_staff`
  keeps whatever the admin site grants them, so the ADR-006 guarantee must not be read as covering
  `/admin/`. Deployment mitigation today: never grant `is_staff` to a viewer. Preferred fix (cheap,
  structural): refuse or warn on the `role=viewer` + `is_staff` combination in
  `identity/services.py` (the same place the other role/privilege invariants live), plus an admin
  `has_permission` guard. Add a test asserting the combination is rejected at the service layer.
- [ ] **I1 — `django.request` logs `request.path` verbatim.** The round-1 CWE-117 fix removed the
  sink in `ui/views.py::not_viewer`, but Django's own 404 logger still writes the percent-decoded
  path, so an authenticated caller can forge log lines there regardless of role. Fix with a logging
  filter that escapes CR/LF (or log `request.resolver_match`), applied in `amalthea/settings/`.
  Pre-existing and role-independent.

---

## 12. Analyst-impact gaps (found 2026-10-12, post-viewer-wave UI audit)

Found while closing out the viewer wave. The **write** side is now well covered — every mutation is gated,
audited and enumerated by tests — but several **read/discovery** surfaces an analyst needs are missing,
truncated or decorative. None of these is a regression (they shipped that way) and none is in a plan yet.

Recommended: one small plan (`PLAN-…-analyst-ergonomics`) covering **§12.1 + §12.2 + §12.4** together, since
they share the list-view plumbing and are cheap. **§10.7/Q-D4 is now settled**: §12.3's "Similar" surface was
re-opened and shipped 2026-10-18 as a read-only, correlation-ranked panel plus a correlation-ranked, audited
attach path — **automatic** ingest-time bundling was deliberately deferred (see §12.3 and §12.3-fu1). §6.16 P6
(search) shipped 2026-11-02 (`6427beeb`) after the §12.1 pager fix; its remaining verification/security/
Postgres work is §6.16-fu1. §6.16 P7 (realtime) remains in its own plan.

- [ ] **12.1-fu1 — Measure the filtered-list `COUNT` on Postgres before trusting it (plan §11 Q7).**
  `observable_list`'s `q` filter is `data__icontains` over a JSONB column with no supporting index; pagination
  adds a `COUNT` per filtered request (plus the unfiltered-base `COUNT` for the "filtered from N" disclosure).
  On SQLite this is unanswerable, so the measurement was **honestly deferred** rather than assumed — the
  verification report records it as the sole unmet AC (`docs/verification/VERIFY-2026-10-18-ui-pagination.md`),
  and the security review keeps it as open item O1. Do it when the Postgres suite lands (§3.1): seed ~100k
  observables, `EXPLAIN (ANALYZE, BUFFERS)` a filtered list request, and if the plan or the latency is
  unacceptable add a GIN/`pg_trgm` index (or move to a `search_vector`) — recording the before/after plan in
  the plan doc. Low risk of *correctness* failure; a real risk of a slow list on a large instance.
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
- [ ] **12.3-fu1 — Automatic ingest-time bundling was deferred by decision Q1b (the "more urgent half" of
  Module B).** Plan: `docs/planning/PLAN-2026-10-18-correlation-similar-cases.md` §5.x (scope) and §11 (the
  pre-settled answers, so the trigger design is **not** re-litigated when this starts).
  Pre-settled: **Q3c** — a rule matches on `correlation_key` **or** a shared observable, configurable,
  defaulting to `correlation_key` within a window; **Q4a** — the rule lives in a minimal per-org
  `CorrelationRule` (`enabled=False` by default, `window_minutes=10`, capped), and it was deliberately
  **not** built in the §12.3 wave because a model with no consumer is exactly the inert-artifact problem
  tracked as TODO item 1; **Q5a** — evaluate synchronously in the ingest request, behind `enabled=False`,
  with a bounded candidate set (the seek is `alert_corrkey_date_idx`, already indexed).
  AGENTS.md's example — "identical destination IP within a 10-minute window" — is exactly this rule.
  Hard requirements when it lands: it must **never** be silent (ledger entry naming the key + window, plus
  an `AuditLog` row — the machinery now exists as `cases/correlation.py::record_alert_bundled`); it must
  never choose a **closed** case; and the §12.1-fu1 Postgres measurement should land first, since the rule
  adds a query to the hot ingest path. Source: §12.3 decision 2026-10-18.
- [ ] **12.4 — No bulk actions in the UI.** `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk`
  (PATCH) exist in the API with per-item isolation + status reporting (`compat/bulk.py`,
  `test_t2_p4_surface.py`), but the UI stub was deleted under Q2 (P1) and nothing replaced it — triaging a
  200-alert morning is single-item work. Fix: selection-based bulk triage (status / assignee / dismiss) that
  calls the same service, keyboard-reachable, `<dialog>` confirmation (ADR-003: no native `confirm()`), and a
  per-item result summary so partial failures are visible. Source: audit 2026-10-12.
- [ ] **12.9 — UI layout/style standardization (global).** (1) **Input/button height alignment:** inputs must
      match button height next to them (form control sizing). (2) **Action columns use fitting icons:** prefer
      compact icon-only actions with accessible names (aria-label/title), not full text buttons in tight action
      columns; keep text buttons for primary standalone actions. (3) **Consistent table layout:** every list
      table lives inside a `.card`, and filtering options live in a separate card above the table (reference:
      **Observables List View**). Capture these as reusable guidelines (e.g. `docs/spec/ui-style.md` or
      `ui/README.md`) and apply to existing/new views (Cases, Alerts, Tasks, Users, Sources/Tokens) to avoid
      regressions. **Note (2026-10-17):** the aligned-action-row pattern already exists as `.case-actions`
      (`app.css`: `margin-top:0`, centred, 34px equal-height controls) and is now reused outside Case
      templates (`source_detail.html`, and the Settings → Sources token row) — generalize/rename it as part
      of this item rather than inventing a second utility. Source: user request 2026-10-17.

- [ ] **12.11 — CI has failed on every run for at least a week, and the full suite is never run locally.**
  Found 2026-10-19 while chasing §12.3-fu2. Evidence (read from the public `actions` API — `gh` is not
  installed here): every non-cancelled run since at least Oct 15 is `failure`, for two independent reasons.
  **(a) The `secrets` job hits its own `timeout-minutes: 10`** (`.github/workflows/ci.yml:88`): the step
  *Full-history scan (local scanner, authoritative)* is `cancelled` on **every** run, and the gitleaks backstop
  is then `skipped`. So the "no secret is ever pushed" property the workflow comments call enforced is in fact
  **not being checked at all** — a *security* regression, not just noise — and it turns every run red
  regardless of code.
  **(b) The `DoD gate` job fails.** Oct 15–17 it failed at the step named *"SQLite DoD gate (lint, types,
  migrations, suite)"*; from `4370900`/`e17c45d` onwards that step passes and the next one, *"Postgres
  conformance suite"* (`DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg pytest -q`), fails; the coverage,
  lock-currentness and supply-chain steps therefore never ran. One cause is now known and fixed (§12.3-fu2);
  the remainder is **unmeasured**, because the failing step's log needs a signed-in session.
  **Why §12.3-fu2 got through:** the full suite is never run locally, and three names imply it is.
  `make check` is lint/types/migrations/css only, yet its own help text claims "…css compile, pytest"; the
  pre-push hook calls it a "full check"; and the locally installed `.git/hooks/pre-commit` even announces
  *"running check-fast (ruff + pytest)"*. That last one is a divergence: `c7f3db5` ("skip pytest in hooks", for
  speed) removed pytest from both hooks and updated `scripts/pre-commit.sh` to say "(ruff)", but `make hooks`
  was never re-run here, so the installed hooks are still the older scripts. (The installed pre-push hook does
  run `tests/conformance/test_schema_mutation_guards.py` — 15 tests, ~2s — but nothing runs the other ~500, so
  a stale test elsewhere is still invisible.) The *decision* to drop the suite from the hooks is defensible (a
  ~15-minute suite per commit is not), but the effect stands: the only automated full-suite run is CI, which is
  red — so a broken test can sit on `main` indefinitely.
  Proposed, in order: (1) fix the `secrets` timeout (raise the cap or bound the scan) and confirm the
  full-history scan completes — it is the only automated secret check; (2) make that step run the suite, or
  rename the step and the Makefile help text so nobody trusts a green "suite" step again; (3) get the Postgres
  suite green (locally and in CI) — it needs a Postgres harness (§3.1); (4) only then does "CI is green" mean
  anything. Until then no plan or verification report should cite CI as evidence.
  **Parked by user decision 2026-10-19** — feature work takes priority. When it is picked up: the user's local
  Postgres run failed only because Postgres is not installed locally but runs in the compose container
  (`docker-compose.yml`), so the container is the way to reproduce; CI *does* provide a `postgres:16-alpine`
  service, so the CI-side Postgres failure is still unmeasured (its log needs a signed-in session).

---

## 13. Maintainability — oversized modules (added 2026-11-04)

Follow-up audit after the `ui/views.py` → `ui/views/` split (`64eef0c`). These are refactor candidates, not
product features. Priority: **P0** next structural change; **P1** next wave; **P2** when the module is next
touched or before adding features; **P3** monitor only — do not split purely for line count. §13.21 is a small
template-hygiene cleanup found during the same audit, not a module split.

**Definition of done for every §13 task:** move-only where possible; preserve public imports, URL names/order,
auth and tenant checks, status codes, request/response bodies, form field names and templates; add compatibility
re-exports where callers/tests patch the old path; no schema changes unless separately planned; `make check` and
the full `pytest` suite green; record any deviation in the plan.

### P0 — next structural refactor

- [ ] **13.1 — Split `cases/views.py` into a `cases/views/` package.**
  Current: **2,821 lines**, 100+ handlers covering case CRUD, tasks, observables, tags, statuses, comments, pages,
  shares, attachments, bulk operations, templates, procedures, TTPs and export — the most mixed module in the
  repo. Target: `cases/views/` with `_common.py`, `cases.py`, `tasks.py`, `observables.py`, `tags.py`,
  `statuses.py`, `comments.py`, `pages.py`, `shares.py`, `attachments.py`, `bulk.py`, `templates.py`,
  `procedures.py`, `export.py`; keep `__init__.py` compatibility re-exports or update `cases/urls.py` in one
  pass. Watch: `cases/urls.py` ordering is load-bearing (specific sub-resources must precede the bare case
  detail route), and every handler keeps its existing auth/tenant checks. Source: §13 audit 2026-11-04.

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
- [ ] **13.21 — Remove redundant pagination guards from list templates (small cleanup).**
  After the `alert_list.html` legacy fallback removal, several paginated lists still wrap their content in
  `{% if page_obj.paginator %}` or `{% if page_obj %}` even though the view always passes a `Page`:
  `case_list.html:33`, `observable_list.html:22`, `automation_list.html:23`, `case_share_list.html:19`,
  `settings_users.html:85` (admin branch only), `tag_list.html:14`, `sources_list.html:13`,
  `playbook_list.html:14`, `settings_sources.html:20`, `settings_organisations.html:13`.
  Remove each guard only where the view unconditionally calls `paginate()`; leave the intentional
  superAdmin per-organisation card path in `settings_users.html` untouched. No behaviour change. Verify the
  empty-state copy and pager still render, then run the list/pagination conformance tests and `make check`.
  Source: 2026-11-04 alert-list cleanup audit.

### P3 — monitor; do not split for size alone

- [ ] **13.13 — `cases/models.py` (677 lines).** 17 Django models. Do **not** split for line count now: model
  module moves risk migration/app-registry churn. Revisit only if the case domain grows substantially.
  Source: §13 audit 2026-11-04.
- [ ] **13.14 — `observables/extractor.py` (570 lines).** Cohesive extraction/normalization/linking pipeline.
  Split only when adding a new extraction stage. Source: §13 audit 2026-11-04.
- [ ] **13.15 — `cases/correlation.py` (518 lines).** Cohesive correlation algorithms; leave unless correlation
  features expand. Source: §13 audit 2026-11-04.
- [ ] **13.16 — `ui/views/ingestion_sources.py` (525 lines).** Mapping-editor UI; split preview/apply from source
  detail only if it grows. Source: §13 audit 2026-11-04.
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

### P2 — correctness, parity and tenant safety

- [ ] **14.1 — Validate `existing_tag_ids` in the UI tag form.** `ui/views/alerts.py:473` accepts any
  POST value, then passes it to `Tag.objects.filter(id__in=...)` at `ui/views/alerts.py:506`; a non-UUID
  raises `ValidationError` → 500 instead of a form error. A valid UUID not shown in the picker can also
  be attached, including one from another tenant. Parse UUIDs, drop invalid values with a message, and
  re-resolve the surviving IDs through the same allowed-tag queryset the picker uses. Add a regression
  test for malformed and out-of-picker IDs.
- [ ] **14.2 — Reject a non-object JSON body on `POST /alert/{id}/tag/add` with 400, not 500.**
  `alerts/views.py:740` calls `tag_names_from_payload(request.data)`; a JSON array makes `request.data` a
  list, and the helper calls `.get()` on it (`cases/tagging.py:23`) → `AttributeError`. Either reject
  non-mapping bodies in the view or make the helper accept `Mapping | list`. Test with `[]` and
  `["tag"]`.
- [ ] **14.3 — Reconcile the documented idempotency with the actual 400.**
  `alerts/views.py:730-731` says re-linking an existing tag is a no-op, but `alerts/views.py:751-758`
  returns 400 for an existing name. Choose one contract: return 201/the alert's tag set unchanged
  (true idempotency, matching `alert_tag_link`), or document the 400 as intentional. Add a test pinning
  the decision.
- [ ] **14.4 — Scope the picker to the alert's organisation, not the caller's.**
  `ui/views/alerts.py:188-192` builds `available_tags_qs` with `user_org_id`, so a superAdmin viewing a
  cross-org alert sees their own org's tags (or none). Decide the superAdmin policy, then use
  `alert.owner_org_id` for normal callers and validate submitted tag IDs against the exact picker
  queryset. This also closes the arbitrary-ID gap in 14.1.
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

- [ ] **14.9 — Fix the truncation message.** `_alert_tag_add_modal_body.html:18` renders the
  `available_tags_truncated` boolean as "Showing the first True tags". Render the shown count
  (`available_tags|length`) like `_case_tag_add_form.html:19`, or pass a dedicated total.
- [ ] **14.10 — Fix the picker's accessibility markup.** `_alert_tag_add_modal_body.html:9` has a
  `<label for="alert-tag-add-existing">` that targets no element. Use a `<fieldset><legend>` around the
  checkbox chips, matching the case form.
- [ ] **14.11 — Simplify the picker query.** `ui/views/alerts.py:182-198` adds three `Count`
  annotations the template never displays, slices the queryset, then calls `.count()` on the sliced
  queryset (a capped second query) and misnames the result `alert_tags_total` (it is the available
  count). Follow `case_detail.py:316-325`: fetch one row past `PICKER_PAGE_SIZE` to detect truncation,
  drop unused annotations, and select/prefetch existing links for the current-tags row in 14.5.
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

## 21. Alert JSON import — security review (open; follows retired §19)

Status: **OPEN — the only remaining item from the §19 alert JSON export/import wave.** The
implementation is committed (`ff139692`) and independently verified (AIMP-1..AIMP-7 MET:
`docs/verification/VERIFY-2026-11-08-alert-json-import.md`), but the P5 security review has not been
run.

- [ ] **21.1 — Run the P5 security review of the alert import surface.** Scope: upload parsing and
  size/node/depth caps; atomicity and duplicate-conflict handling; assignee/tag/custom-field/
  observable resolution (no cross-org or privilege escalation); XSS via payload/filename; secrets
  never emitted in the export; CSRF/auth gate (`is_admin`, uniform 404). Record findings and
  disposition in the verification doc or a new `SECURITY-*` note.
