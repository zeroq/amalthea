# TODO — Amalthea

Open work only. Shipped work and closed-item history live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-11-22 — **MCP Phase 4 policy-bounded autonomy implemented and verified PASS** (§22.6). Also **§25 source mapping submappings decisions recorded** and **§24 alert detail Similar tab plan decisions recorded**. MCP Phase 0/1/2/3 complete and verified READY: read-only
`/mcp`, static-bearer credentials, org-bound scopes, audit/rate limits, read tools/resources/prompts,
and Settings → Agent access are in place. A live Daphne smoke exposed that Daphne sends no ASGI
lifespan; `McpLifecycleApp` now starts the session manager lazily on the first `/mcp` request, and
the no-lifespan path has a conformance test. CrewAI 1.15.41 smoke passed in an isolated Python 3.13
environment (13 tools; `list_cases` OK); the smoke credential was revoked. Phase 1 full suite:
**3773 passed, 3 skipped, 0 failed, 0 errors, 2 subtests passed** (`/tmp/amalthea-full-pytest.log`).
Focused suite: `167 passed`. Security review: **PASS**
(`docs/security/SECURITY-2026-11-15-mcp-phase1.md`). Independent verification:
**READY — PASS**. Phase 2 proposal-first writes, the `/approvals` inbox, and auto-posted
`ai-summary` timeline entries are implemented and verified: full suite **3873 passed, 3 skipped,
0 failed, 0 errors, 2 subtests passed** (`/tmp/amalthea-full-pytest.log`); security **PASS**
(`docs/security/SECURITY-2026-11-19-mcp-phase2-final.md`); final verification **READY — PASS**
(`docs/reviews/VERIFY-2026-11-19-mcp-phase2-final.md`). Decisions remain: **CrewAI** first
client;
**AI case summaries auto-post directly** as labelled `ai-summary` timeline entries. Reconciled
search (§6.16 P6): it shipped 2026-11-02 (`6427beeb`), so the priority row is now the narrow
**§6.16 P6-fu1** (Postgres measurement only; verification and T6 security review are complete). The
original header-search ACs are superseded by `PLAN-2026-11-02-search-page-rail-entry.md`. Also
archived §18/§19 and the playbook script picker to [`COMPLETED.md`](./COMPLETED.md); the §19
alert-import security review is complete (PASS). Requirements lock is current
(`scripts/lock.sh --check`). Full suite last run
2026-11-19 (**3873 passed, 3 skipped, 0 failed, 0 errors, 2 subtests passed**, 14m54s), covering MCP
Phase 0/1/2, search, the playbook picker, and the case-template layout fix. Merged upstream `992f316d`;
preserved the open backlog and imported its Rejected Observables section as §23. CI repaired 2026-11-16 (§12.11 closed): two independent jobs capped at five minutes —
fast static/smoke verification and the authoritative full-history secret scan. Local evidence:
`make check-ci` 52s (50 passed), `scripts/secret-scan.sh --history` 111s, lock check 105s. The full
suite, Postgres run and coverage remain local/manual gates and are no longer implied by CI step names.
The priority table
below is the source of truth for open work.

---

## 🎯 NEXT HIGHEST-IMPACT ITEMS (Priority Order)

Ordered by **impact on the analyst** — the daily IR loop first, then deployment security, then
engineering hygiene. Shipped rows were removed in the 2026-11-08 cleanup; the shipped search row moved
to its narrow follow-up on 2026-11-13 (see `COMPLETED.md`).

| Priority | Item | Description | Impact |
|----------|------|-------------|--------|
| **1** | **§12.3-fu1 — Automatic ingest-time bundling** | `correlation_key` is derived/indexed but only the manual Similar/attach path consumes it. Auto-bundle was deferred (Q1b). Depends on §12.1-fu1. | **High** — the orchestration promise of Modules B+C. |
| **2** | **§12.4 — No bulk actions in the UI** | `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk` exist in the API; the UI stub was deleted (Q2). | **Medium-High** — 200-alert mornings are single-item work. |
| **3** | **§6.16 P7 — Per-org realtime breadth + presence** | Scoped HTMX adoption (D4/D5), broader live events, presence. | **Medium** — collaboration polish; ledger already syncs. |
| **4** | **§10.1 — Role-scoped user management** | An `admin` manages own-org users (today `superAdmin`-only). | **Medium** — ops/onboarding, not triage. |
| **5** | **§6.13 / §6.16 P4 — Ingestion tokens + audit-log viewer** | **Partial:** token lifecycle shipped (10a–c); read-only `AuditLog` viewer remains. | **Medium** — audit viewer is the accountability half. |
| **6** | **S3 + S5 (+ M3 residual)** | **S3** per-account lockout on the API login + stop trusting client `XFF`. **S5** attachment aggregate caps + extension-from-content-type. M3 shipped; residual same-org cleartext secrets. | **Medium (security)** |
| **7** | **§12.1-fu1 — Measure filtered-list `COUNT` on Postgres** | Unmeasured `COUNT` on 12 paginated lists; prerequisite for auto-bundling. | **Medium** — pager truthfulness at scale. |
| **8** | **§2.6 + §2.7 + §7.4 + §8.5/§8.7 + §11.4-L1/§11.5** | Coverage ≥ 80%; Phase-12/T2 QA list; Tailwind source/artifact split; mutation coverage; unaudited procedure attaches; admin role model; log forging. | **None** — engineering hygiene. |
| **9** | **§10.9 — Status vocabularies have no human labels** | UI prints raw tokens (`InProgress`); add labels without breaking `stage`-keyed automation. | **Low** — cosmetic. |
| **10** | **§10.11 — Reload mints a second ingestion token** | 200 body re-submits on reload; one-shot nonce or explicit nudge. | **Low** — admin-only exposure. |
| **11** | **§10.12-fu1…fu4 — Token follow-ups** | Postgres reap race (Medium), prefix/throttle collision, admin paused-state display, test gaps. | **Low–Medium.** |
| **12** | **§14.1–14.12 — Alert tag follow-ups** | Id validation, parity, truncation text, a11y, query cleanup, swap-file purge. | **Low.** |
| **13** | **§15.7/15.8/15.10 — Observables follow-ups** | API parity (Q6), per-link provenance decision (Q7), redact raw values from mapping warnings (Medium residual). | **Low.** |
| **14** | **§16.6 — Empty-payload edge** | `{}` renders instead of "No raw data yet." | **Low.** |
| **15** | **Orphaned routes** | `ui-case-attachment-list` (+ dead template) and `ui-case-attachment-detail` (405) are URL-only. | **Low** — dead surface. |
| **16** | **§12.9 — UI layout/style standardization** | Input/button heights, compact action columns, table/filter convention; codify guidelines. Includes the new alert/case severity rows (`.severity-filter-row`, `.severity-filter--N`) as the canonical filter pattern. | **Low.** |
| **17** | **§13 — Oversized-module maintainability backlog** | 20 candidates after the `ui/views.py` split; `cases/views.py` (~2.8k lines) is P0. | **None** — hygiene. |
| **18** | **§6.16 P6-fu1 — Postgres search measurement** | Search shipped (`6427beeb`); verification and T6 security review are complete (PASS). Remaining: run `EXPLAIN` on seeded Postgres before deciding on a trigram index. | **Medium** — closes the last evidence gap; no user-facing feature is missing. |
| **19** | **§22 — MCP server for AI agents (Phases 1–4 implemented)** | CrewAI first; read-only context, proposal-first state changes, direct auto-posted AI summaries, OAuth 2.1 resource-server hardening, and shadow-first policy-bounded autonomy. All phases implemented and verified PASS (security PASS, independent verification PASS). | **Medium** — agent-assisted triage; Phase 4 adds narrow opt-in autonomy. |
| **20** | **§23 — Rejected Observables Management** | Standalone rejection records linked to alerts, suppression in extraction, admin review/promotion, org isolation, confirmation modal. | **Medium** — false-positive suppression; upstream plan still needs acceptance criteria. |
| **21** | **§24 — Alert detail Similar tab** | Decisions recorded: lazy Similar tab on alert detail, up to 20 recently closed similar alerts via adapted SSDEEP/title gate plus shared observables, count badge. | **Medium** — analyst discovery of prior closed alerts. |
| **22** | **§25 — Source mapping submappings** | Decisions recorded: master mapping plus v2 `submappings` keyed by raw alert JSON `type`, full replacement, master fallback, JSON-first UI. | **Medium-High** — correct multi-type feed ingestion. |
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
- [ ] **6.16-fu1 — Global search follow-up: Postgres measurement.**
  P6 shipped 2026-11-02 (`6427beeb`): `/search` + `GET /api/v1/search`, shared `query/search.py`, org
  scoping, highlighting, paging, and tests.
  - [x] **Independent verification** — `docs/verification/VERIFY-2026-11-01-global-search.md`: **PASS**
    (34/35 ACs met, 1 superseded, 0 deviations).
  - [x] **T6 security review** — `docs/reviews/REVIEW-2026-11-13-global-search.md`: no new High/Medium
    findings; residuals documented.
  - [ ] **Postgres measurement** — run `EXPLAIN (ANALYZE, BUFFERS)` on seeded Postgres for filtered
    case/alert/observable searches and add a trigram index only if the measurement warrants it.
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

- [x] **12.11 — CI repaired 2026-11-16; full suite remains a local/manual gate.**
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

  **Closed 2026-11-16 (minimum 5-minute repair).** `.github/workflows/ci.yml` now has two independent
  jobs, each `timeout-minutes: 5`: `verify` (install pinned closure, `make check-ci`, lock check) and
  `secrets` (full-history local scanner). `make check-ci` runs static checks plus a 50-test fast
  conformance subset; `make check` help no longer claims pytest. The full suite, Postgres conformance
  run, coverage gate and pip-audit are deliberately **not** CI steps — they exceed the budget (or, for
  pip-audit, report pre-existing dependency advisories) and remain local/manual. The secret scanner now
  scans each unique blob once via a bounded fixed-string candidate pass and exact regex rescan, and
  skips legacy dependency/cache blobs; the authoritative run is **111s** locally and passes. Local
  evidence: `make check-ci` **52s / 50 passed**, `scripts/lock.sh --check` **105s / current**,
  `tests/unit/test_secret_scan.py` **20 passed**. CI green confirmed on run `44158684927` (`f8abbf9f`): **success** in 2m24s; `Fast verify` 2m20s, `Secret scan (full history)` 1m07s.

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

## 22. MCP server for AI agents (Phase 1 implemented)

Status: **PHASE 0 COMPLETE / PHASE 1 VERIFIED READY — dependency, ASGI/protocol, live Daphne
and CrewAI spikes verified; read-only gateway, org-bound credentials, audit/rate limits, read
tools/resources/prompts, and Settings → Agent access implemented. Security review PASS; full suite
clean (3773 passed, 3 skipped, 0 failed, 0 errors); independent verification READY — PASS.**

Plan: [`PLAN-2026-11-13-mcp-server.md`](docs/planning/PLAN-2026-11-13-mcp-server.md).
Phase 0/1 brief:
[`IMPL-2026-11-13-mcp-server-phase-0-1.md`](docs/planning/IMPL-2026-11-13-mcp-server-phase-0-1.md).

**Intent.** Let an authorised AI agent read alert/case context, triage, summarise a case, suggest
next actions, and eventually handle a case through closure — with human approval for state changes
and one narrow direct-write exception for labelled AI summaries.

**Proposed shape.**

- Remote Streamable HTTP MCP endpoint at `/mcp/`, mounted in `amalthea/asgi.py` using the official
  `mcp` Python SDK v2; same Daphne/ASGI process as Django + Channels. No stdio transport in v1.
- Dedicated `McpCredential` bound to one **user + organisation**, with MCP scopes and expiry. A
  superAdmin may mint an org-scoped credential but cannot use MCP to read across organisations.
- Thin tools over shared Amalthea services — reuse `identity/scoping.py`, role rules, audit, and the
  case ledger; never call the platform's own HTTP API over loopback.
- Phase 1 read-only: search, alert/case lists and detail, case digest/timeline, observables, tasks,
  safe playbook metadata; MCP resources for case/alert/observable; prompts for triage, summary, and
  action suggestions. **CrewAI** is the first supported client (`MCPServerHTTP`, Streamable HTTP,
  bearer header).
- Phase 2 proposal-first state changes: the agent creates `McpActionProposal` rows; a human approves
  in a new `/approvals` inbox; the executor re-checks auth and calls the existing service once.
  **AI case summaries auto-post directly** as labelled, append-only `ai-summary` timeline entries
  with source-event provenance, sanitisation, rate limits, and idempotency. This is the sole direct
  write; it cannot change status, severity, owner, tasks, observables, or closure.
- Phase 3 OAuth 2.1 resource-server support before exposing the endpoint to untrusted clients.
- Phase 4 opt-in autonomy: deterministic policy, low-risk allowlist, shadow mode default, daily caps,
  kill switch. **Case closure always requires a human.**
- No server-side LLM, no model keys, no raw payloads/attachments, no destructive actions, no
  playbook authoring, no cross-tenant superAdmin reads.

**Recorded decisions.** Same ASGI app at `/mcp/`; dedicated org-scoped static-bearer
`McpCredential` (OAuth in Phase 3); dedicated service user per agent; Phase 2 proposal list only
(no delete/merge/share/user/token/playbook-authoring); Amalthea `/approvals` inbox is authoritative;
**summaries auto-post directly** (Q6); no raw payload/attachments; superAdmin credentials are
org-scoped; autonomy is shadow-mode only; **CrewAI is the first client** (Q10). Invocation audit
retention 90 days; agent credentials managed in Settings UI only; no stdio bridge; summaries use
`TimelineEvent(kind="ai-summary")` with source-event metadata.

**Next action.**

- [x] **22.1 — Approve the plan and produce the Phase 0/1 implementation brief.** Issued
  [`IMPL-2026-11-13-mcp-server-phase-0-1.md`](docs/planning/IMPL-2026-11-13-mcp-server-phase-0-1.md)
  with file paths, tasks, and ACs for backend/frontend/QA.
- [x] **22.2 — Phase 0 dependency/protocol/CrewAI spike.**
  - [x] Pin `mcp==2.3.0` + `mcp-types==2.3.0` with hashes; verify no dependency conflict and a clean
    runtime import. Added the reviewed closure additions to `scripts/lock.sh` `BASE_ALLOW`.
  - [x] Mount a minimal `MCPServer` at `/mcp` + `/mcp/` with lifecycle composition (lazy start for
    Daphne, lifespan when present); verify
    `initialize`/`tools/list`/`tools/call` and a non-MCP Django path. Results in ADR-012 and
    `tests/conformance/test_mcp_phase0.py`; a Channels websocket smoke also passes.
  - [x] Gate: MCP + realtime smoke (`3 passed`), `manage.py check`, `make check-fast`, full suite
    (`3699 passed, 3 skipped, 2 subtests passed`), and secret scan clean. `pip install --dry-run
    --require-hashes -r requirements/base.txt` passes; `scripts/lock.sh --check` passes and the committed
    requirements are current.
  - [x] Verify CrewAI `MCPServerHTTP` with a bearer header and one real tool call in an isolated
    **Python 3.13** environment. CrewAI 1.15.41 requires Python `<3.14`; the Amalthea runtime is
    3.14.6. Live Daphne smoke: 13 tools discovered and `list_cases(limit=5)` returned `is_error=false`;
    the smoke credential was revoked. Example: `examples/mcp/crewai_agent.py`; docs:
    `docs/integrations/crewai.md`. The smoke also found and fixed the Daphne no-lifespan startup
    path (`McpLifecycleApp`), covered by `test_mcp_phase0.py::test_mcp_round_trip_without_asgi_lifespan`.
- [x] **22.3 — Phase 1 read-only implementation.**
  - [x] `McpCredential` / `McpInvocation`, Argon2 mint/verify/revoke/expiry, org+active-user binding.
  - [x] ASGI `/mcp` + `/mcp/`, fail-closed gate, static-bearer verifier, host/origin/body limits.
  - [x] Credential-derived scoping: no org argument, cross-org/unknown ids return uniform
    `not_found`, and credentials cannot use the superAdmin break-glass path.
  - [x] Read tools (`search`, alerts, cases/digest/timeline, observables, tasks, playbooks), safe
    serializers, resources, and prompts (triage, summary, suggested actions).
  - [x] Settings → Agent access (`/settings/agent-access`): create/list/revoke, one-time plaintext
    modal with `Cache-Control: no-store`, org binding, audit rows, admin/superAdmin gates.
  - [x] Focused conformance: `167 passed` across MCP credentials/tools/prompts/ASGI/limits, the
    agent-access UI, UI URL/table/wire-boundary guards, and tenant-scoping conformance; `make check`
    passes (ruff, format, mypy, Django, migrations, CSS, scripts).
  - [x] Full suite green after the Phase 1 changes: **3773 passed, 3 skipped, 0 failed, 0 errors,
    2 subtests passed** (`/tmp/amalthea-full-pytest.log`). The earlier 35 setup errors were fixed by
    marking the MCP transaction tests `serialized_rollback=True`; the order-targeted reproduction
    was `209 passed, 2 skipped`.
  - [x] CrewAI 1.15.x `MCPServerHTTP` smoke in isolated Python 3.13 (13 tools; `list_cases` OK;
    smoke credential revoked).
  - [x] Security review: **PASS** (`docs/security/SECURITY-2026-11-15-mcp-phase1.md`); proxy-aware
    client IP and atomic `last_used_at` follow-ups are closed.
  - [x] Final independent verification: **READY — PASS** (23 ACs Met, 5 recorded naming/structure
    deviations, 0 blockers).
- [x] **22.4 — Phase 2 proposal-first writes + auto-posted summaries.** Shared service extraction,
  `McpActionProposal`, proposal tools, `/approvals` inbox, four-eyes/expiry/idempotency,
  `post_case_summary` direct write with AI label/source events/rate limits, tests, security review,
  verification.
  - [x] Implementation restored/repaired: proposal model/migration, scope separation, executor with
    revalidation, eight proposal tools, `post_case_summary`, rate limits, approvals UI, modal
    confirmation dialogs, and case-tab pending badge.
  - [x] Phase 2 focused conformance green: **78 passed** across proposals, summaries, scope
    separation and approvals UI (`.venv/bin/python -m pytest -q ...`).
  - [x] Static/CI gate green with the pinned interpreter: `make check-ci` (50 smoke tests passed;
    ruff, format, mypy, Django check, migrations in sync, CSS drift and playbook scripts all clean).
  - [x] Deviations recorded in `docs/spec/deviations.md` (MCP2-1 … MCP2-5).
  - [x] Final security re-audit: **PASS**; prior M-1/M-2 are closed with no new findings
    (`docs/security/SECURITY-2026-11-19-mcp-phase2-final.md`).
  - [x] Provisional verification recorded 35 Met, 0 Not met, 2 Not verifiable
    (`docs/reviews/VERIFY-2026-11-19-mcp-phase2-provisional.md`, now superseded).
  - [x] Initial full-suite log: **8 failed, 3864 passed, 3 skipped** — all 8 failures were Phase 2
    UI-conformance regressions (approvals route enumeration, Font Awesome subset, keyboard
    shortcuts, and table markup), not MCP logic.
  - [x] Those 8 failures fixed and locally re-verified: affected UI conformance/approvals suite
    **50 passed**, full `test_tenant_scoping.py` **103 passed**, Phase 2 suite **78 passed**, and
    `make check-ci` green (50 smoke tests, ruff, format, mypy, Django check, migrations, CSS, scripts).
  - [x] Final user-owned full-suite rerun on the fixed tree: **3873 passed, 3 skipped,
    15 warnings, 2 subtests passed** (`/tmp/amalthea-full-pytest.log`).
  - [x] Final independent verification: **READY — PASS** (37/37 ACs Met, 0 blockers)
    (`docs/reviews/VERIFY-2026-11-19-mcp-phase2-final.md`).
- [x] **22.5 — Phase 3 OAuth + external-client hardening.** OAuth 2.1 resource-server profile,
  protected-resource metadata, proxy/SSE/multi-worker hardening, client docs.
  - [x] Implemented external-IdP resource-server validation, binding UI, metadata/challenge,
    deployment hardening, docs, ADR, and audit/observability changes.
  - [x] Security audit **PASS** after two blocking findings were fixed (`legacy_static_use` logging
    and bounded JWKS refresh on unknown `kid`)
    (`docs/security/SECURITY-2026-11-20-mcp-phase3.md`).
  - [x] Targeted verification green: 216 passed across MCP/settings/prod-settings conformance tests;
    `manage.py check`, `makemigrations --check --dry-run`, tracked and changed-file secret scans clean.
  - [x] Final independent verification: **READY — PASS** (55 ACs evaluated, 0 Not Met, 5 documented
    deviations, 0 blockers) (`docs/planning/VERIFY-2026-11-20-mcp-phase3.md`).
- [x] **22.6 — Phase 4 policy-bounded autonomy.** `McpOrgPolicy`, shadow-mode evaluation, low-risk
  allowlist, kill switch, autonomy UI; no autonomous closure. Defaults accepted 2026-11-21. Plan:
  [`PLAN-2026-11-21-mcp-phase4-autonomy.md`](docs/planning/PLAN-2026-11-21-mcp-phase4-autonomy.md);
  implementation brief:
  [`IMPL-2026-11-21-mcp-phase4-autonomy.md`](docs/planning/IMPL-2026-11-21-mcp-phase4-autonomy.md).
  Focused suite: **284 passed**. Security review: **PASS**
  (`docs/reviews/MCP-PHASE4-SECURITY-REVIEW-2026-11-22.md`). Independent verification: **PASS**
  (42/42 ACs met, 0 deviations) (`docs/planning/VERIFICATION-2026-11-22-mcp-phase4-autonomy-144831.md`).

**Dependencies/constraints.** Phase 1 depends on 22.2 and on a first-class agent-credential UI.
Phase 2 depends on extracting write logic out of views without changing REST/UI behaviour. Phase 3
depends on the choice of external identity provider vs a maintained Django OAuth toolkit. Phase 4
depends on Phase 2 approval telemetry and a reviewed evaluation. All phases must keep the existing
REST/UI/WebSocket contracts and full test suite green; new dependencies must be pinned and hashed;
secrets never enter the repo.

---

## 23. Rejected Observables Management

> Imported from upstream `992f316d` (2026-11-14). Upstream `TODO.md` replaced the open backlog with
> this section only; preserved here as §23 so the existing open-work ledger is not lost and §16
> numbering stays intact. Plan:
> [`PLAN-2026-11-14-rejected-observables.md`](docs/planning/PLAN-2026-11-14-rejected-observables.md).

- [ ] **23.1 — Implement a standalone `RejectedObservable` model.**
  Create a record for observables rejected during analysis (not yet promoted to full `Observable` entities).
  Each must link to its source `Alert`, an optional "Reason" (dropdown/Enum), and a timestamp.
  The entity is for audit and suppression, not for cross-case correlation.
- [ ] **23.2 — Integrated Suppression Check.**
  Inject logic into the Extraction Engine to filter out values that exist in the `RejectedObservable`
  table for the same organization/source. This prevents rejected items from reappearing in analyst queues.
- [ ] **23.3 — Administrative Review & Promotion.**
  Allow admins to view a list of rejected records and "Promote" them.
  Promotion should not auto-create an `Observable` but re-enable the record for manual review
  (i.e., move it from the rejection list back to an "ignored" or "flagged" state).
- [ ] **23.4 — Organization-specific visibility.**
  Ensure that rejected items are only visible/actionable within their own organization (except for SuperAdmins).
  SuperAdmins can see all records across all organizations.
- [ ] **23.5 — Promotion Confirmation.**
  Ensure the "Promote" action triggers a confirmation modal to prevent accidental promotion of large sets of data.

---

## 24. Alert detail — Similar tab (plan draft, 2026-11-21)

Status: **Decisions recorded 2026-11-21 — ready for implementation brief.**
Plan location: this section (per user request; no separate `docs/planning/PLAN-…` file for this item).

### 24.0 Summary

Add a **Similar** tab to the alert detail page. Activating it shows a bounded list of up to **20 closed alerts**
considered similar to the alert being viewed. Each row exposes a clickable **title** linking to that alert's detail
page, the alert's **verdict**, the **closed** timestamp, and a **similarity** indicator. The tab shows a count badge.
The list is ordered by `updated_at` descending, then `date` / `pk` as tie-breakers.

Similarity is computed from two bounded signals, unioned together:

1. Adapted fuzzy matching from the case Similar implementation: `payload_ssdeep` via `ppdeep.compare` plus a
   normalized-title Jaccard gate and the existing configured thresholds.
2. Shared stored observables via `AlertObservable`, excluding observables with `ignore_similarity=True`.

### 24.1 Goals

- Add a `Similar` tab to `ui/alert_detail.html` using the existing server-rendered tab/lazy-fragment pattern,
  placed **after Observables and before Raw data**.
- Reuse and adapt the existing fuzzy similarity stack (`payload_ssdeep`, `ppdeep.compare`, normalized title Jaccard,
  configured thresholds).
- Also identify closed alerts sharing one or more stored observables with the source alert.
- Show only alerts with `Alert.status.stage == "Closed"`, regardless of verdict value.
- Show a tab count badge using the bounded match total.
- Limit displayed results to 20, with an honest “Showing 20 of N” disclosure when more matches exist in the
  bounded candidate pool.
- Keep the feature read-only, tenant-scoped, keyboard-accessible, and consistent with the case Similar tab.

### 24.2 Non-goals

- No alert merge/attach/link actions from this tab in this iteration.
- No REST/API endpoint; this is a UI fragment only.
- No new similarity model, no persisted similarity scores, no Celery job, no WebSocket event.
- No new `closed_at` field or migration; `updated_at` ordering is accepted for this feature.
- No cross-organisation comparison, even for superAdmins.
- No show of raw payloads or raw JSON in the fragment.

### 24.3 Current-state findings

- Alert detail view: `ui/views/alerts.py::alert_detail()`.
  - Current tabs are built at `ui/views/alerts.py:218-225`: `general`, `observables`, `raw-data`.
  - `active_tab` is allow-listed against those slugs.
  - Context already includes `alert` and `alert_verdict`.
- Alert detail template: `ui/templates/ui/alert_detail.html`.
  - Tab list at `ui/templates/ui/alert_detail.html:25-44`.
  - Panels: general, observables, raw-data; `tabs.js` is already loaded.
- Case Similar lazy pattern:
  - `ui/views/case_detail.py::case_similar_fragment()` (`ui/views/case_detail.py:462-490`).
  - Route `ui-case-similar` in `ui/urls.py:84`.
  - Fragment root `ui/templates/ui/_similar_tab_fragment.html` carries `data-similar-count` / `data-similar-*`.
  - `ui/static/ui/tabs.js:66-97` lazy-loads `data-lazy-src` panels and updates count badges.
- Similarity primitives:
  - `cases/correlation.py::alerts_fuzzy_similar_to_case()` (`cases/correlation.py:350-462`).
  - SSDEEP canonicalization/hash: `alerts/hashing.py::compute_payload_ssdeep()`.
  - Title normalization/Jaccard: `alerts/title_similarity.py::normalize_title_tokens()` / `jaccard_similarity()`.
  - Thresholds/settings:
    - `ALERT_FUZZY_SIMILARITY_THRESHOLD` default 60.
    - `ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD` default 70.
    - `ALERT_FUZZY_TITLE_GATE_ENABLED` default True.
    - Env overrides documented in `docs/runbook.md`.
- Stored alert observables:
  - `alerts/models.py::AlertObservable` links alerts to deduped `Observable` rows.
  - `observables/models.py:110` has `ignore_similarity`; case correlation already excludes it in
    `cases/correlation.py` (`ignore_similarity=True` must be excluded here too).
- Alert closure/verdict:
  - `Alert.verdict` exists (`alerts/models.py:88-90`) but is a plain string, not choices.
  - Closing verdicts are `true_positive` / `false_positive` (`core/enums.py:190-208`).
  - Direct close sets `verdict` and `updated_at` (`ui/views/alerts.py::alert_close()` area).
  - Case-close propagation updates linked alerts with `QuerySet.update(status=..., verdict=...)` and does **not**
    update `updated_at` (`cases/update.py::close_case()` area).
  - There is **no** `Alert.closed_at` field.

### 24.4 Architecture / implementation shape

#### 24.4.1 Similarity helper

Add an alert-to-alert helper beside the existing fuzzy helper, preferably in `cases/correlation.py` to reuse
constants and imports:

```python
SIMILAR_CLOSED_ALERTS_LIMIT = 20


@dataclass(frozen=True)
class SimilarAlertMatch:
    alert: Alert
    similarity: int | None  # fuzzy score 0-100, or None when only observables matched
    shared_observable_count: int  # 0 when only fuzzy matched


def alerts_similar_to_alert(
    source: Alert,
    *,
    user: User,
    limit: int = SIMILAR_CLOSED_ALERTS_LIMIT,
) -> tuple[list[SimilarAlertMatch], int]: ...
```

Shared base scope for both channels:

- `Alert.objects.filter(status__stage="Closed")`.
- `.exclude(pk=source.pk)`.
- `.filter(org_q("owner_org", user))`.
- Add `owner_org_id=source.owner_org_id` so a superAdmin viewing a cross-org source alert cannot compare against
  another tenant's alerts.
- `select_related("status", "owner_org")`.
- Order by `("-updated_at", "-date", "-pk")`; this is the accepted “most recently closed” proxy.

Fuzzy channel:

1. If `source.payload_ssdeep` is empty, produce no fuzzy candidates.
2. Otherwise, take the base scope ordered by recency and slice to `MAX_FUZZY_COMPARISONS` before scoring.
3. For each candidate with non-empty `payload_ssdeep`:
   - `score = ppdeep.compare(candidate.payload_ssdeep, source.payload_ssdeep)`.
   - When the title gate is enabled, require
     `jaccard_similarity(normalize_title_tokens(candidate.title), normalize_title_tokens(source.title)) >= threshold`.
   - Use the same settings/env thresholds as the case Similar helper.
   - Treat `ppdeep` import failure or comparison exceptions as score 0, matching existing behaviour.
4. Keep qualifying candidates with `similarity=score`.

Shared-observable channel:

1. Read source `AlertObservable` rows and collect `observable_id` values, excluding
   `observable__ignore_similarity=True`; cap source observable IDs at `MAX_CORRELATION_OBS`.
2. If there are no source observable IDs, produce no observable candidates.
3. Query `AlertObservable` rows whose `observable_id` is in that set, excluding
   `observable__ignore_similarity=True`, joined to the shared base scope, annotating
   `shared_observable_count=Count("observable_id", distinct=True)`.
4. Cap candidate alert rows at `MAX_FUZZY_COMPARISONS` after ordering by recency.
5. Keep candidates with `shared_observable_count > 0`.

Union and result contract:

- Merge fuzzy and observable candidates by `alert.id`.
- If a candidate matched both channels, keep the maximum fuzzy score and the shared observable count.
- `total_matching` is the size of the union after both bounded channels, before the final 20-row slice.
- Final rows are sorted by `("-updated_at", "-date", "-pk")`, not by similarity score.
- Return `(rows[:limit], total_matching)`.
- Similarity display rules:
  - fuzzy only: `NN%`
  - observables only: `N shared obs`
  - both: `NN% · N shared obs`

#### 24.4.2 View and route

- `ui/views/alerts.py`:
  - Import `alerts_similar_to_alert`, `SIMILAR_CLOSED_ALERTS_LIMIT`, and `SimilarAlertMatch`.
  - Add `{"slug": "similar", "label": "Similar", "count": similar_total}` after `observables` and before
    `raw-data`; the count is the bounded union total.
  - In `alert_detail()`, call `alerts_similar_to_alert(alert, user=request.user)` once to populate the badge.
    This cost is accepted by decision Q24-4.
  - Add `alert_similar_fragment(request, alert_id)`:
    - `@login_required`, `@require_GET`, viewer-allowed (read-only).
    - `resolve_alert(request.user, alert_id, write=False)`; unknown/cross-org → 404.
    - Call the helper with `limit=SIMILAR_CLOSED_ALERTS_LIMIT`.
    - Render `ui/_alert_similar_tab_fragment.html`.
- `ui/urls.py`: add `alerts/<uuid:alert_id>/similar` → `ui-alert-similar`.
- `ui/views/__init__.py`: export the new fragment view if the package re-exports alert views.

#### 24.4.3 Templates / UI

- `ui/templates/ui/alert_detail.html`:
  - Add the `similar` tab entry **after Observables and before Raw data**, using the existing tab badge rendering
    (`{{ tab.count }}`) and icon branch style; use `fa-diagram-project`.
  - Add `panel-similar` with `data-lazy-src="{% url 'ui-alert-similar' alert.id %}"` when inactive.
  - If active via `?tab=similar`, server-render the fragment into the panel and mark it `data-loaded`.
- New `ui/templates/ui/_alert_similar_tab_fragment.html`:
  - Root carries `data-similar-count="{{ similar_total }}"` and `data-similar-alerts` for `tabs.js` parity.
  - Includes `ui/_alert_similar_alerts_card.html`.
- New `ui/templates/ui/_alert_similar_alerts_card.html`:
  - `.card` with `<h2>Similar alerts</h2>`.
  - `.table-wrap` + `<table class="data-table data-table-tight">`.
  - `<caption class="sr-only">Closed alerts similar to this alert</caption>`.
  - Columns: **Title**, **Verdict**, **Closed**, **Similarity**.
  - Title is a link to `ui-alert-detail` for the candidate alert.
  - Verdict is human-readable via `core.taxonomy_read` helper; raw token fallback only if no label exists.
  - Closed uses `updated_at` and visible format `Y-m-d H:i`.
  - Similarity uses a template helper/context string:
    - fuzzy only: `NN%`
    - observables only: `N shared obs`
    - both: `NN% · N shared obs`
  - Honest truncation: `Showing {{ rows|length }} of {{ similar_total }} similar alerts` only when truncated.
  - Empty state: `No closed alerts are similar enough right now.`

### 24.5 Data model

No schema change and no migration. Use existing `Alert.status`, `Alert.verdict`, `Alert.updated_at`, `Alert.date`,
`Alert.payload_ssdeep`, `Alert.title`, `Alert.owner_org`, `AlertObservable`, and `Observable.ignore_similarity`.
Ordering by `updated_at` is an accepted approximation for closure time; do not add `closed_at` in this feature.

### 24.6 Phases and acceptance criteria

#### Phase 1 — Similarity helper

- **T1.1 — Add `alerts_similar_to_alert()`, `SimilarAlertMatch`, and cap constant.**
  - AC1.1a: Helper lives in `cases/correlation.py` and reuses existing SSDEEP/title settings/thresholds.
  - AC1.1b: Base candidate set is closed-only, excludes the source alert, and is scoped to the caller's org and the
    source alert's org; superAdmin cannot cross tenants.
  - AC1.1c: Fuzzy channel requires non-empty source and candidate `payload_ssdeep`, applies the normalized-title
    Jaccard gate when enabled, and caps comparisons at `MAX_FUZZY_COMPARISONS`.
  - AC1.1d: Observable channel uses source `AlertObservable` IDs, excludes `ignore_similarity=True`, caps both
    source observables and candidate alerts, and counts distinct shared observables.
  - AC1.1e: Fuzzy and observable candidates are unioned by alert ID; dual matches preserve both score and shared
    observable count.
  - AC1.1f: Return value is `(rows, total_before_final_limit)` and `rows` are ordered by `-updated_at`, `-date`,
    `-pk`.
  - AC1.1g: Source with neither SSDEEP nor usable observables returns `([], 0)` without raising.
  - AC1.1h: A result row carries the candidate alert, optional fuzzy score, and shared observable count.

#### Phase 2 — UI route and rendering

- **T2.1 — Add route and fragment view.**
  - AC2.1a: `ui-alert-similar` resolves to a GET-only view.
  - AC2.1b: Unauthenticated requests redirect to login; viewer reads are allowed; no mutation occurs.
  - AC2.1c: Unknown/cross-org alert IDs return 404.
  - AC2.1d: Fragment context includes `similar_alerts`, `similar_total`, and `alert`.
- **T2.2 — Add tab, badge, and lazy panel.**
  - AC2.2a: `alert_detail()` accepts `?tab=similar` and marks the Similar tab active.
  - AC2.2b: Default tab remains `general`; invalid tab values still fall back to `general`.
  - AC2.2c: The Similar tab is after Observables and before Raw data and renders the bounded similarity count badge.
  - AC2.2d: Inactive Similar panel has `data-lazy-src` pointing at `ui-alert-similar`.
  - AC2.2e: Active Similar panel is server-rendered and marked loaded.
  - AC2.2f: Tab icon and label follow the existing dark-theme/accessibility conventions.
- **T2.3 — Add result table.**
  - AC2.3a: Table uses `.data-table data-table-tight`, has `sr-only` caption, and all headers use `scope="col"`.
  - AC2.3b: Each row has a clickable title linking to the candidate alert detail.
  - AC2.3c: Each row shows a human-readable verdict.
  - AC2.3d: Each row shows Closed as `updated_at` in `Y-m-d H:i`.
  - AC2.3e: Each row shows Similarity as `NN%`, `N shared obs`, or `NN% · N shared obs` as applicable.
  - AC2.3f: At most 20 rows render.
  - AC2.3g: Truncation disclosure appears only when `similar_total > rows|length`.
  - AC2.3h: Empty state renders when no matches exist.
  - AC2.3i: No native `confirm()`/`alert()`/`prompt()` or mutation controls are introduced.

#### Phase 3 — Tests and verification

- **T3.1 — Add `tests/conformance/test_alert_similar_tab.py`.**
  - AC3.1a: Helper tests cover closed-only, exclude-source, org scoping, superAdmin cross-org source,
    SSDEEP/title thresholds, empty SSDEEP, and bounded honest total for the fuzzy channel.
  - AC3.1b: Helper tests cover shared-observable matches, `ignore_similarity=True` exclusion, dual-channel union,
    and observable-only matches when SSDEEP is empty.
  - AC3.1c: Ordering test proves results are newest-by-`updated_at` first even when similarity scores differ.
  - AC3.1d: Limit test proves 20-row cap and honest total.
  - AC3.1e: Fragment tests cover login, viewer read, 404s, rendered title link, verdict label, Closed, Similarity,
    and empty state.
  - AC3.1f: Alert detail tests cover tab position, badge count, `?tab=similar` activation, invalid-tab fallback,
    and lazy `data-lazy-src`.
  - AC3.1g: Table markup guard passes (`.data-table`, caption, `scope="col"`).
  - AC3.1h: No font/icon/table/keyboard conformance regressions.
- **T3.2 — Verification.**
  - AC3.2a: Targeted suites green.
  - AC3.2b: `make check` / full static gate green.
  - AC3.2c: Full pytest green per the standing DoD rule.
  - AC3.2d: Secret scan clean.
  - AC3.2e: Verification report records any deviation from Q answers.

### 24.7 Security

- The fragment is read-only and uses the same `resolve_alert(..., write=False)` contract as case Similar.
- Candidate queries are tenant-scoped twice: caller org and source-alert org.
- No raw `raw_payload`, no mapping warnings, and no secret material are rendered.
- Similarity computation remains bounded: fuzzy comparisons by `MAX_FUZZY_COMPARISONS`, source observable IDs by
  `MAX_CORRELATION_OBS`, and observable candidate alerts by `MAX_FUZZY_COMPARISONS`; do not remove the caps.
- Observables marked `ignore_similarity=True` are excluded from the shared-observable channel.
- Title/verdict values are escaped by Django templates.
- No cross-org superAdmin comparison path.
- No write audit events are needed because the tab performs no mutation.

### 24.8 Testing plan

Primary new suite: `tests/conformance/test_alert_similar_tab.py`.

Existing suites to rerun:

- `tests/conformance/test_alert_fuzzy_similarity.py`
- `tests/conformance/test_ui_similar_tab_lazy.py`
- `tests/conformance/test_alert_observables_tab.py`
- `tests/conformance/test_ui_table_markup.py`
- `tests/conformance/test_ui_icons.py`
- `tests/conformance/test_ui_icon_font.py`
- `tests/conformance/test_ui_alert_detail_tags.py`
- `tests/conformance/test_ui_urls_smoke.py`
- `tests/conformance/test_tenant_scoping.py`

Verification gates:

- `.venv/bin/pytest <targeted suites> -q`
- `make check`
- secret scan
- full pytest

### 24.9 Risks

- **R1 — Approximate closure time.** Ordering uses `updated_at`; case-propagated closure does not update it, so
  “most recently closed” is approximate by accepted decision Q24-2.
- **R2 — False positives/negatives.** SSDEEP plus title gate is heuristic; vendor-specific volatile fields may
  suppress fuzzy matches (already tracked in §12.12-fu3). Shared observables mitigate but do not eliminate this.
- **R3 — Missing SSDEEP.** Historic alerts or degraded payloads may have empty `payload_ssdeep`; they can still
  match via shared observables, but fuzzy-only matches are impossible.
- **R4 — CPU/database cost.** Both channels are bounded, but `ppdeep.compare` is CPU-bound and observable fan-out
  can be large; do not raise caps without measurement.
- **R5 — Candidate cap honesty.** `similar_total` is honest for the bounded candidate pools, not necessarily the
  whole database. UI copy must not imply exhaustive search.
- **R6 — Tab-badge cost.** Computing similarity on every alert detail load for the badge is accepted by Q24-4; if
  it proves slow, cache or lazy-load the badge as a follow-up rather than silently changing this decision.

### 24.10 Decisions (answered 2026-11-21)

- **Q24-1 — Similarity signals:** both adapted SSDEEP + normalized-title gate **and** shared stored observables.
  `correlation_key` is not included as a separate signal.
- **Q24-2 — “Most recently closed”:** order by `updated_at` descending; no `closed_at` field or migration.
- **Q24-3 — Candidate definition:** every alert with `status.stage == "Closed"`, regardless of verdict.
- **Q24-4 — Count badge:** show a badge with the bounded similarity total.
- **Q24-5 — Columns:** include Title, Verdict, Closed, and Similarity.
- **Q24-6 — Tab position:** after Observables, before Raw data.

### 24.11 Next action

- Produce an implementation brief for `django-backend` / `django-frontend` / `qa-tester`.
- Implement T1→T3 in order, recording any deviation from the decisions above in this section before merge.

---

## 25. Source mapping — per-type submappings (plan draft, 2026-11-21)

Status: **Decisions recorded 2026-11-21 — ready for implementation brief.**
Plan location: this section (per user request; no separate `docs/planning/PLAN-…` file for this item).

### 25.0 Summary

Today each `IngestionSource` has exactly one `mapping_config` object. This plan adds optional **type submappings**
inside that same source mapping config. For alerts whose raw JSON has a top-level `type` field, the ingest pipeline
selects a submapping by that raw type. A matching submapping becomes the effective mapping for that alert and
**fully replaces** the source's master mapping. If `type` is absent, non-string, or no submapping matches, the
master mapping is used.

No database migration is required; this is an extension of the mapping config language plus runtime resolution.

### 25.1 Goals

- Support a master mapping per ingestion source plus named submappings keyed by alert JSON `type`.
- Select the submapping from the **raw payload** before field mapping, correlation-key extraction, and observable
  extraction.
- Preserve the existing webhook contract: source/org still come from the ingestion token (ADR-008).
- Keep `apply_mapping()` pure and unchanged; add a small pure resolver before it.
- Validate submappings recursively with the existing v2 rule/transform/JSONPath limits.
- Ensure remap, preview, and observable apply use the same per-alert effective mapping as live ingest.
- Keep first iteration UI work small unless Q25-7 chooses a first-class per-type editor.

### 25.2 Non-goals

- No new mapping model/table, no DB migration, no per-type records in the database.
- No configurable JSONPath for the submapping selector in v1; selector is raw top-level `payload["type"]`.
- No partial/merge semantics: submappings fully replace the master config.
- No nested submappings inside submappings.
- No change to webhook authentication, routing, throttling, or alert token model.
- No change to alert import/direct API creation paths; those already require explicit mapped fields.
- No automatic reclassification of existing alerts when a submapping changes mapped `type` during remap, unless
  Q25-9 overrides the current MAP-9 rule.

### 25.3 Current-state findings

- Source/mapping storage:
  - `ingest/models.py`: `IngestionSource.mapping_config = JSONField(blank=True, default=dict)`.
  - Source is org-owned; `slug` unique per organisation; ADR-009 confirms mapping config is tenant data.
- Webhook routing and initial mapping:
  - `ingest/views.py::webhook_alerts()` (`~164-288`) authenticates via token, then applies the source mapping.
  - Token identifies both source and organisation; no source in URL (ADR-008).
  - Parsing guards: body-size, content type, token verification, throttle, body re-check, JSON object check, depth check.
- Mapping engine:
  - `ingest/mapping.py`
    - `apply_mapping()` (`~587-696`)
    - `config_shape()` (`~715-731`)
    - `read_rules()` (`~734-756`)
    - `validate_config()` (`~759-851`)
    - `extract_correlation_key()` (`~857-884`)
  - v2 config sections include `version`, `fields`, `value_maps`, `observables`, `correlation_key`, `on_missing`.
  - `fields` targets include `title`, `description`, `summary`, `severity`, `date`, `type`, `source_ref`.
  - Existing caps: `MAX_FIELDS = 20`, `MAX_PATHS_PER_FIELD = 8`, `MAX_PATH_LEN = 200`, compiled JSONPath cache cap `512`.
- Persistence re-reads mapping:
  - `ingest/pipeline.py::store_ingested_alert()` calls `extract_correlation_key(source.mapping_config, payload)` and
    later observable sync.
  - If only `apply_mapping()` is changed, correlation/observables would still use the master config, producing a split
    interpretation of one alert.
- Remap / preview:
  - `ingest/remap.py::remap_alerts()`, `preview_alerts()`, and `_evaluate()` apply one config to every alert.
  - With submappings, resolution must happen per alert because one source can receive several raw types.
- Observable apply:
  - `ingest/observable_apply.py` and `observables/alert_sync.py::sync_alert_observables()` also use the source config.
  - `ui/views/ingestion_sources.py::_observables_only_change()` currently rejects changes outside `observables`.
- Editor:
  - `ui/templates/ui/source_detail.html` has Mapping and Raw data tabs, field table, observable rules, mapping health,
    advanced JSON, apply dialogs.
  - `ingest/editor.py` parses/validates JSON, canonicalizes configs, computes changed keys, and preserves unknown
    top-level keys on table saves.
- Existing type examples:
  - `tests/conformance/test_mapping_engine.py` CrowdStrike payload has raw top-level `"type": "ldt"`.
  - `tests/conformance/test_webhook_hardening.py` phishing payload has no top-level `type`.
- Audit:
  - `record_ingestion_source_updated()` stores nested dot paths such as `submappings.ldt.fields.title.paths`; no values.

### 25.4 Architecture / implementation shape

#### 25.4.1 Pure resolver

Add to `ingest/mapping.py`:

```python
@dataclass(frozen=True)
class MappingResolution:
    config: dict[str, Any]
    matched_type: str | None = None
    used_submapping: bool = False


def resolve_mapping_config(
    mapping_config: dict[str, Any] | None,
    payload: Any,
) -> MappingResolution: ...
```

Behaviour:

1. Return the master config unchanged if:
   - config is empty/`None`,
   - config has no `submappings`,
   - payload is not a mapping,
   - payload has no top-level `type`,
   - `type` is not a string,
   - no submapping key exactly matches the raw type.
2. On exact match:
   - use the submapping object as `resolution.config`,
   - set `matched_type` and `used_submapping=True`.
3. Do not mutate `mapping_config` or `payload`.
4. Do not recurse into nested submappings; validation rejects them.
5. Keep `apply_mapping()` unchanged. Callers resolve first, then apply:

```python
resolution = resolve_mapping_config(source.mapping_config, payload)
draft = apply_mapping(resolution.config, payload, default_severity=source.default_severity)
```

#### 25.4.2 Config language

Default config shape:

```json
{
  "version": 2,
  "fields": {
    "title": {"paths": ["$.title"], "transform": "str"}
  },
  "observables": [
    {"type": "IP", "paths": ["$.src_ip"]}
  ],
  "correlation_key": "$.sourceRef",
  "submappings": {
    "ldt": {
      "version": 2,
      "fields": {
        "title": {"paths": ["$.display_name"], "transform": "str"},
        "severity": {"paths": ["$.severity"], "transform": "severity_int"},
        "type": {"paths": ["$.type"], "transform": "str"},
        "source_ref": {"paths": ["$.id"], "transform": "str"}
      },
      "observables": [
        {"type": "Hash", "paths": ["$.sha256"]}
      ],
      "correlation_key": "$.id"
    }
  }
}
```

Rules:

- `submappings` is a v2-only top-level object.
- Keys are raw alert type strings; default max 50 submappings and 100-char keys.
- Each submapping value is a complete v2 config with `version: 2`.
- A matching submapping fully replaces the master config. There is no field-by-field merge.
- Submappings may contain all normal v2 sections, including `observables`, `value_maps`, `correlation_key`, and
  `on_missing`, subject to Q25-6.
- Nested `submappings` inside a submapping are rejected.
- Legacy flat configs with `submappings` are rejected by validation.
- Exact, case-sensitive string match; no trimming/normalisation unless Q25-4 changes this.

#### 25.4.3 Validation and editor helpers

Extend `ingest/mapping.py::validate_config()`:

- Validate `submappings` type, count, key length/emptiness, and version marker.
- Recursively validate each submapping with the same v2 rules as the master.
- Reject nested submappings.
- Ensure `config_shape()` / `read_rules()` either remain master-only or gain an explicit “type overrides” summary;
  source list must not silently hide submappings.

`ingest/editor.py` should mostly inherit this via `parse_json_config()`:

- `changed_keys()` should produce nested paths such as `submappings.ldt.fields.title.paths`.
- Canonicalization/no-op detection should compare nested submappings.
- Table/observable writers must preserve `submappings` through `_preserved()`.

#### 25.4.4 Runtime threading

The resolved config must be passed everywhere an alert payload is interpreted.

- Live webhook (`ingest/views.py`):
  - Resolve once after JSON parsing and before mapping.
  - Pass effective config into `store_ingested_alert(...)`.
- `ingest/pipeline.py`:
  - Accept optional `mapping_config`/`effective_mapping_config`.
  - Use it for `extract_correlation_key(...)` and observable sync.
  - Keep backwards-compatible default to source mapping for non-webhook callers, if any.
- `ingest/remap.py`:
  - `_evaluate()` resolves per alert via `resolve_mapping_config(config, alert.raw_payload)`.
  - Use resolved config for `apply_mapping()` and `sync_alert_observables()`.
  - `preview_alerts()` inherits the fix.
- `ingest/observable_apply.py` / `observables/alert_sync.py`:
  - Extend preview/apply to resolve per alert.
- `ui/views/ingestion_sources.py::_observables_only_change()`:
  - Update or bypass so top-level `observables`-only saves do not discard submappings.
  - If Q25-7 chooses JSON-only editing, simply preserving submappings is enough.

#### 25.4.5 UI

Default (Q25-7 default): **advanced JSON only** in v1.

- `ui/templates/ui/source_detail.html`:
  - Add help text under Advanced JSON documenting `submappings`, exact raw type match, full replacement, and master fallback.
  - Show submapping count near mapping health/source list.
  - Show raw payload `type` in Raw data tab so analysts can see the selector value.
  - Keep existing mapping table editing master-only; table saves must preserve submappings.
- No new per-type editor, no scope selector, no raw-click-map scope changes in default scope.

If Q25-7 chooses first-class editing:

- Add a scope selector (`Master` + one option per submapping key) to mapping/observable editor forms.
- Extend `_source_mapping_rows()`, `_source_mapping_post()`, `build_table_config()`, `editor_rows()`,
  `build_observable_config()`, and raw-click-map prefill to carry the scope.
- Add forms for adding/renaming/deleting submapping keys.

#### 25.4.6 Audit / warnings

- Config edits continue to be audited through existing `changed_keys` nested paths.
- Default: do **not** write an ingestion warning merely because a submapping was selected or because type was absent/unknown.
- If Q25-8 chooses observability, add either:
  - a free-text warning such as `submapping_selected: ldt`, or
  - a structured `FieldWarning.reason` extension plus template labels.

### 25.5 Data model

No new model and no migration. `IngestionSource.mapping_config` continues to store the whole object. All submapping data is tenant data by virtue of belonging to the org-owned source.

### 25.6 API contracts

- Webhook request/response contract unchanged.
- Mapping config JSON shape gains optional `submappings`; API/serializer fields that expose it remain JSONField values.
- Preview/apply endpoints do not change names; their behaviour becomes per-alert-type aware.
- Docs to update:
  - `docs/spec/api.md` ingest mapping and mapping editor sections.
  - `docs/spec/data-model.md` `mapping_config` description.
  - `docs/spec/identity-auth.md` mapping editor description.
  - `docs/spec/deviations.md`: add MAP-10 documenting submapping semantics and MAP-9 interaction.

### 25.7 Phases and acceptance criteria

#### Phase 1 — Resolver + validation

- **T1.1 — Add `resolve_mapping_config()` and `MappingResolution`.**
  - AC1.1a: Missing/empty config, missing `type`, non-string `type`, and unknown type return master.
  - AC1.1b: Exact raw type match returns the submapping config with `used_submapping=True`.
  - AC1.1c: Resolver does not mutate config or payload.
  - AC1.1d: Nested submappings are not resolved recursively.
- **T1.2 — Validate submappings.**
  - AC1.2a: `submappings` must be an object with bounded count/key length.
  - AC1.2b: Each submapping is a complete valid v2 config.
  - AC1.2c: Nested submappings are rejected.
  - AC1.2d: Legacy flat config with `submappings` is rejected.
  - AC1.2e: Existing nested editor helpers produce dotted changed-key paths and preserve submappings.

#### Phase 2 — Runtime consistency

- **T2.1 — Webhook/pipeline.**
  - AC2.1a: Webhook resolves before `apply_mapping()`.
  - AC2.1b: Correlation key extraction uses the effective resolved config.
  - AC2.1c: Observable extraction/sync uses the effective resolved config.
  - AC2.1d: Typeless/unknown-type payloads continue to use master exactly as today.
- **T2.2 — Remap/preview/observable apply.**
  - AC2.2a: Remap resolves mapping per alert from raw payload type.
  - AC2.2b: Preview and confirmed apply agree on the effective config.
  - AC2.2c: Observable preview/apply resolves per alert.
  - AC2.2d: Existing `type` and `source_ref` remain unchanged during remap unless Q25-9 changes the rule.

#### Phase 3 — UI/docs

- **T3.1 — JSON-first editor updates.**
  - AC3.1a: Advanced JSON accepts valid submappings and rejects invalid nested configs with readable errors.
  - AC3.1b: Source list/detail show submapping count without hiding master rules.
  - AC3.1c: Raw data tab shows raw `type` used for selection.
  - AC3.1d: Table/observable-only saves preserve submappings.
- **T3.2 — Docs/deviations.**
  - AC3.2a: API/data-model/identity docs updated.
  - AC3.2b: MAP-10 deviation recorded.
  - AC3.2c: README example either remains labelled legacy or gains a v2 submapping example.

#### Phase 4 — Tests and verification

- **T4.1 — Conformance coverage.**
  - AC4.1a: Pure resolver tests cover match/fallback/non-string/unknown/no-mutation/nested rejection.
  - AC4.1b: Webhook end-to-end test with CrowdStrike `type: "ldt"` proves submapping affects title, severity,
    correlation key, and observables.
  - AC4.1c: Typeless phishing payload still uses master.
  - AC4.1d: Remap/preview/apply use per-alert resolution.
  - AC4.1e: Editor validation/no-op/preservation/audit tests cover nested submapping paths.
  - AC4.1f: Tenant isolation unchanged.
- **T4.2 — Gates.**
  - AC4.2a: Targeted suites green.
  - AC4.2b: `make check` / full static gate green.
  - AC4.2c: Full pytest per standing DoD rule.
  - AC4.2d: Secret scan clean.
  - AC4.2e: Verification report records any deviation from answered questions.

### 25.8 Security

- No change to ADR-008 token routing; source and org still come from verified token.
- Submapping config remains tenant-owned through `IngestionSource`.
- Validation must apply existing JSONPath/path/transform caps to every submapping independently.
- Bound submapping count/key length to prevent oversized configs and surprising editor/audit cost.
- Do not log payload contents or submapping values in warnings/audit; audit only dotted changed keys.
- Raw type strings are used only as object keys; no query construction or code execution from type values.
- Reject nested submappings to avoid unbounded recursive resolution/validation.

### 25.9 Testing plan

Primary suites:

- `tests/conformance/test_mapping_engine.py`
- `tests/conformance/test_webhook_hardening.py`
- `tests/conformance/test_mapping_editor.py`
- `tests/conformance/test_settings_source_mapping.py`
- `tests/conformance/test_mapping_remap.py`
- `tests/conformance/test_ingest_alert_observables.py`
- `tests/conformance/test_ingest_observable_remap.py`
- `tests/conformance/test_source_observable_editor.py`
- `tests/conformance/test_source_observable_apply.py`
- `tests/conformance/test_observable_apply.py`
- `tests/conformance/test_raw_click_map.py` (only if Q25-7 chooses first-class editor)
- `tests/conformance/test_source_detail_raw_data.py`
- `tests/conformance/test_tenant_scoping.py`

Verification gates:

- `.venv/bin/pytest <targeted suites> -q`
- `make check`
- secret scan
- full pytest

### 25.10 Risks

- **R1 — Split config interpretation.** If correlation/observable paths keep using master while field mapping uses a
  submapping, alerts become internally inconsistent. T2.1/T2.2 are mandatory.
- **R2 — Config complexity.** Full replacement is easier to reason about but duplicates common fields across submappings.
- **R3 — Silent fallback.** Missing/unknown type uses master; without warnings, misspelled submapping keys can go unseen.
  Q25-8 decides whether to surface this.
- **R4 — Remap identity fields.** MAP-9 keeps `type`/`source_ref` stable on existing alerts; submapping changes to those
  targets affect only newly ingested alerts unless Q25-9 changes policy.
- **R5 — Audit size.** Nested changed-key paths are safe (no values), but large configs still increase audit row counts.
- **R6 — Editor affordance.** JSON-only editing is cheaper but less discoverable; Q25-7 decides.

### 25.11 Decisions (answered 2026-11-21 — all defaults accepted)

- **Q25-1 — Config key:** `submappings`.
- **Q25-2 — Legacy compatibility:** submappings are v2-only; reject them on legacy flat configs.
- **Q25-3 — Selector source:** raw top-level `payload["type"]` only.
- **Q25-4 — Matching:** exact case-sensitive string match; no trim/casefold/normalisation.
- **Q25-5 — Override semantics:** full config replacement; no field/section merge with master.
- **Q25-6 — Submapping sections:** submappings may contain all normal v2 sections except nested `submappings`.
- **Q25-7 — UI scope:** advanced JSON only for v1, with help text and validation; no first-class per-type editor yet.
- **Q25-8 — Observability:** no new ingestion warning for selected/unknown type; existing config-change audit only.
- **Q25-9 — Remap type:** keep MAP-9; do not rewrite existing alert `type` / `source_ref` during remap.
- **Q25-10 — Caps:** max 50 submappings and 100-character keys.

### 25.12 Deferred improvement ideas (explicitly later, not in this implementation)

- First-class per-type mapping editor: scope selector (`Master` / submapping key), add/rename/delete submapping keys,
  and raw-click-map target selection inside a submapping.
- Configurable submapping selector JSONPath instead of raw top-level `payload["type"]`.
- Case-insensitive / trimmed / normalised type matching for noisy feeds.
- Partial override semantics with explicit per-section merge rules.
- Ingestion warnings or mapping-health output showing selected submapping and unknown/missing type counts.
- Revisit MAP-9 if remap should be allowed to rewrite mapped `type` / `source_ref` on existing alerts.
- Higher cap configuration and source-list total-rule counts across master + submappings if real configs justify it.
- ADR for the submapping config language if the shape proves stable and needs formal cross-project documentation.

### 25.13 Next action

- Implementation brief: `docs/planning/IMPL-2026-11-21-source-mapping-submappings.md`.
- Implement T1→T4 in order, recording any deviation from the decisions above in this section before merge.
