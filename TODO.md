# TODO — Amalthea

Open work only. Completed items live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-11-06 (**alert observables from source mappings shipped and verified (§15) — 28/28 ACs, 56 focused tests, relevant suites and `make check` green; security review PASS with one pre-existing Medium residual risk filed as §15.10; deferred Q6 API parity and Q7 per-link provenance remain open**). Earlier: **oversized-module refactor backlog added (§13); case custom-fields tab shipped — detail-only `customFields`, conditional editable tab, case-scoped edit endpoint; 65 targeted tests and `make check` green; full suite green on the worktree at `4bab358` (plus six unrelated dirty template edits) — 3305 passed, 3 skipped; independent verification still pending**). Earlier: **correctness wave landed — and CI has been red all week**. The wave closed `§12.10`
(severity pills rendered unstyled: the filter emitted `sev-high` while `app.css` defines only `.sev-1`…`.sev-4`),
`§12.5-fu1` (a dialog heading and its Title input shared one DOM `id`, so the input lost its accessible name) and
`§12.5-fu2` (no committed cross-tenant enumeration for the three case-page write routes) — each with a
regression test; `make check` is green; **the pytest run is the user's and is still pending**. The wave also
turned up a **broken test already sitting on `main`** (§12.3-fu2: §12.3 repointed a view context to `SimilarCase`
and left an older assertion behind), and chasing that exposed a bigger problem: **CI has failed on every run for
at least a week** (**§12.11**) — the `secrets` job times out at its 10-minute cap, and the full suite is never run
locally — which is how a broken test reached `main` unseen. Earlier: **§10.12 closed** — the
ingestion-token wave §10a–§10c finally has *both* passes, not
just the self-graded one: an **independent** re-verification (**22/22 ACs**, 16/16 design decisions, at
`44282f8`) and its **first `security-auditor`** review (**PASS** — 0 Critical / 0 High, 2 Medium / 2 Low / 2
Info). One caveat is recorded in the new report: its *Mutation check* section is carried over from the
2026-10-17 provisional report rather than performed independently. Findings filed as §10.12-fu1…fu4. Earlier:
**§12.8 is fully closed** — its last blocker §12.8-fu4 landed 2026-10-19, so the
shipped case-verdict/close-flow feature flips to `[x]`; note its first fu4 verification report is
**invalidated** — the tests it cites had been deleted from the tree during that run — so the wave's evidence
is a **54-test user-run `pytest`** plus a planner diff review; see §12.8-fu4. Earlier: **two
verification debts closed out** — §12.5 is **closed** (independent verification,
23/23 checks: `docs/verification/VERIFY-2026-10-18-case-pages-ui-independent.md`, superseding the planner-authored
2026-10-17 report; F11–F15 confirmed fixed) and §12.8's two caveats are **discharged**: the independent re-run graded
**18/18 ACs** and the security pass returned **CONDITIONAL PASS**
(`docs/reviews/SECURITY-2026-10-18-case-verdict-close-flow.md`; 0 Critical / 0 High), whose three conditions
(§12.8-fu4) landed 2026-10-19). Earlier: **UI icons fixed** — the Font Awesome subset stylesheet never set
`font-family` on `.fa-solid`, the class every template icon uses, so the codepoints rendered as
`.notdef` tofu boxes (most visible in Safari); a malformed last line also swallowed
`.fa-comments`/`.fa-list-check`/`.fa-paperclip`. Both fixed in
`ui/static/vendor/fontawesome/css/fontawesome-subset.min.css`, guarded by
`tests/conformance/test_ui_icon_font.py`). Earlier: **htmx CSP violation fixed** — `ui/templates/ui/base.html` now declares
`<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>`, so htmx stops injecting an
indicator `<style>` into `<head>` that our `style-src 'self'` refused; guarded by
`tests/conformance/test_ui_inline_assets.py` (source pin) + a rendered-response check in
`test_ui_urls_smoke.py`). Earlier: **case verdict + enforced close flow shipped** — §12.8: `Case.verdict` +
`Alert.verdict` (snake_case tokens), a Verdict card on the Taxonomy page, General-tab inline edit
icons, a Tags row, and a close rule that requires a non-`undefined` verdict + a closing comment and
propagates closure to every linked alert; guarded by `tests/conformance/test_case_verdict_close.py`,
`test_ui_case_verdict.py`). **ACs verified 18/18** in
[`docs/verification/VERIFY-2026-10-17-case-verdict-close-flow.md`](./docs/verification/VERIFY-2026-10-17-case-verdict-close-flow.md) —
the inline-verification caveat is now **discharged** (independent re-run at `79793f5`, re-issued in `554bb0d`; one new
low deviation D-N1 → §12.8-fu3), and the security pass returned **CONDITIONAL PASS**
([`SECURITY-2026-10-18-case-verdict-close-flow.md`](./docs/reviews/SECURITY-2026-10-18-case-verdict-close-flow.md)):
0 Critical / 0 High, with the hardening conditions tracked in §12.8-fu4. Earlier: **ingestion-token lifecycle shipped** — Settings → Sources can now pause
(deactivate) and resume (activate) an ingestion token, and delete an inactive one; deleting a feed's
last token now deletes the feed too, and the stale "open accepts unauthenticated posts" copy is gone
(§10a/§10b/§10c, §10.8a; guarded by `tests/conformance/test_settings_ingestion_tokens.py`). **ACs verified 22/22** in
[`docs/verification/VERIFY-2026-10-17-ingestion-token-lifecycle.md`](./docs/verification/VERIFY-2026-10-17-ingestion-token-lifecycle.md) —
**provisional**: the `verifier` subagent was down (503), so the pass was run inline by the planner and still needs an
independent re-run + a `security-auditor` pass on this scope. Earlier: **the per-source
field-mapping wave shipped** — the mapping engine, the
re-map control and the P5 health card; §6.17 retro-mapping done, §6.18 dead code removed 2026-10-17 —
`PLAN-2026-10-16-source-field-mapping.md`). Earlier: **case pages are authorable in the UI** — §12.5 code shipped
(`PLAN-2026-10-14-case-pages-ui.md`): `ui-case-page-create/-edit/-delete`, a `can_mutate`-gated Pages card,
modal create/edit/delete, Markdown-rendered content, `live.js` re-fetch; **independently verified 2026-10-18 at
23/23 checks — closed** (`docs/verification/VERIFY-2026-10-18-case-pages-ui-independent.md`, which supersedes the
planner-authored 2026-10-17 report), one new low-severity modal-id finding V1 (→ §12.5-fu1), and five
shared-layer defects found and fixed on the way through the modal-contract + tab stylesheet — see the plan
§11 and `docs/spec/deviations.md` Wave 10.4). Earlier: **cases view fixes** — `GET /cases/` no longer 404s (new trailing-slash
middleware, 301/308) and **New case moved off the left rail into a modal** on the cases list
(`PLAN-2026-10-13-cases-view-fixes.md`; independently verified **Pass** 16/16 ACs + security-reviewed
0 Critical / 0 High / 0 Medium; `make check` 1689 passed / 3 skipped). Earlier: **§10.2 manual case
creation shipped** — `PLAN-2026-10-13-ui-case-create.md`; new `GET|POST /cases/new` + the `n` shortcut on
the shared applier with a `case.created` audit row; independently verified (**Partial**) +
security-reviewed, which found and closed a Medium regression (**M1** — `/api/v1/case` `status`
`""`/`null` 500) and an accepted Low (**L1** — viewer rail gating); `make check` 1662 passed / 3 skipped.
Earlier: **viewer role enforcement shipped** (ADR-006 +
`PLAN-2026-10-12-viewer-role-enforcement.md`, independently verified + security-reviewed over 3 rounds;
closes §11.1 and the P3-review M1/L3; follow-ups in §11.4/§11.5; `make check` 1617 passed / 3 skipped).
**NEW — §12 records the analyst-impact gaps found in the post-wave UI audit** (no cross-case/correlation
surface, no search, a decorative observables filter, API-only case pages; the "every list silently capped
at 200 with no pager" item **shipped 2026-10-18**). Earlier: §6.16 P3 procedures/TTP UI + P5 D1/D3 shipped (P3 follow-ups §11.4, P5
follow-ups §11.1–11.3); README Quickstart webhook flow refreshed for the per-org `IngestionToken`
(ADR-004) and §10.8 filed; §6.16 UI wave P1 + P2 shipped + verified + security-remediated; §6.12
org/user Settings and §6.14 Wave 6.3A hardening done 2026-10-11; §10 backlog captured.

**Conventions** — every item carries a `Source` (plan AC, review finding ID, or decision ID) so it can
be traced, and `Blocks` when it gates other work. Review IDs (`C1`, `H2`, `M5`…) refer to
`docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md` (round 1) and
`REVIEW-2026-10-05-phase3-schema-gate-round3.md` (round 3).

**Status legend** — `[ ]` open · `[~]` in progress · `[!]` blocked

---

## 🎯 NEXT HIGHEST-IMPACT ITEMS (Priority Order)

Ordered by **impact on the analyst** — the daily IR loop first, then deployment security, then engineering
hygiene. Every row cites its source; the new audit findings live in **§12**. Shipped work is not listed here
(see [`COMPLETED.md`](./COMPLETED.md)): §6.4 tenant isolation, §6.12 org/user Settings, §6.14/§6.15 CSP
hardening, §6.16 P1/P2/P3/P5, §10.2 manual case creation (2026-10-13), and the viewer role wave (ADR-006).

**New (2026-11-07, planning only):** **§18 — case JSON import** and **§19 — alert JSON export/import**. Not yet ranked in the table below.

| Priority | Item | Description | Impact on the analyst |
|----------|------|-------------|-----------------------|
| **1** | **§12.3 — No cross-case / correlation surface (Modules B + C) — SHIPPED 2026-10-18** | The gap: `correlation_key` was derived, indexed and queryable while **nothing consumed it**. Now a read-only **Similar** panel (cases sharing an observable, correlation-ranked) plus a manual, correlation-ranked and **audited** attach path. **Automatic ingest-time bundling was deliberately deferred** (decision Q1b) → **§12.3-fu1**. Also settled §10.7 "Similar" + Q-D4. | **Done** — the manual half; auto-bundling is §12.3-fu1. Follow-up §12.3-fu2 (an older cross-tenant test this wave left stale on `main`) repaired 2026-10-19. |
| **2** | **§6.16 P6 — Global search (B3)** | No search surface anywhere in the UI; the only affordance is browsing lists (now paginated since §12.1, but still browse-only). Plan: `PLAN-2026-10-11-ui-completeness.md` B3/Q5 (cases + alerts + observables). | **Highest** — "have we seen this hash / IP / user before?" is the most asked triage question and has no answer. |
| **3** | **§12.1 — Lists are silently truncated at 200, with no pager — SHIPPED 2026-10-18** | A shared `paginate()` primitive (50/page, `?per_page` bounded, hostile `?page` never 500s) now covers all 12 list surfaces; rails are capped at `RAIL_CAP = 200` with an honest count line; every primary list has a pager, a "showing X–Y of Z" count and an empty state that separates *no rows* from *filtered to none* (with Clear filters). Plan `docs/planning/PLAN-2026-10-18-ui-pagination.md`; verified 33/34 ACs (`docs/verification/VERIFY-2026-10-18-ui-pagination.md`) and security PASS (`docs/reviews/REVIEW-2026-10-18-ui-pagination.md`). | **Done** — the remaining half-answer is the unmeasured Postgres `COUNT` (§12.1-fu1). |
| **4** | **§12.2 — The observables-list filter does nothing — SHIPPED 2026-10-15** | `observable_list` now reads `q`/`type` from `request.GET`, passes `observable_types` (the dropdown is no longer empty), stays org-scoped, and tolerates a malformed `type` id. The pager + honest empty state shipped with §12.1 (2026-10-18). | **Done** — the control no longer lies, inside a list that now pages and counts honestly. |
| **5** | **§12.4 — No bulk actions in the UI** | `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk` (PATCH) exist in the API with per-item isolation; the UI stub was deleted (Q2) and not replaced. | **Medium-High** — a 200-alert morning is single-item work. |
| **6** | **§10.3–10.6 — Case scope / type / data-classification / phase — SHIPPED 2026-10-15** | `PLAN-2026-10-14-case-classification-fields.md`, commit `822d050`, verified 21/21 ACs. `data_classification` is admin-only and audited; `phase` is a separate field from `CaseStatus` (Q-D2). | **Done** — follow-up is §10.9 (status labels). |
| **7** | **§12.5 — Case pages: renderable, not authorable** | **Code shipped 2026-10-14** (`ecbcef6`); **independently verified 2026-10-18 at 23/23 checks** (`docs/verification/VERIFY-2026-10-18-case-pages-ui-independent.md`, superseding the planner-authored 2026-10-17 report); F11–F15 confirmed fixed. Open follow-ups: V1 modal-id a11y (§12.5-fu1) and cross-tenant enumeration (§12.5-fu2) — **both closed 2026-10-19**. | **Done** 2026-10-18. |
| **8** | **§6.16 P7 — Per-org realtime breadth + presence** | Scoped HTMX adoption (D4/D5), broader live events, presence. | **Medium** — collaboration polish; the ledger already syncs on the case page. |
| **9** | **§10.1 — Role-scoped user management** | An `admin` manages own-org users (today `superAdmin`-only). | **Medium** — an ops/onboarding concern, not triage. |
| **10** | **§6.13 / §6.16 P4 — Settings: ingestion tokens + audit-log viewer** | Per-org webhook tokens (create/revoke, show-once) + read-only `AuditLog` viewer; also retires the stale webhook copy (§10.8). | **Medium** — self-serve ingestion; the audit viewer is the accountability half. |
| **10a** | **§6.13 / §6.16 P4b — Settings: Sources → token activate/deactivate — SHIPPED 2026-10-17** | In Settings → Sources, a token row now offers **Deactivate** (active → modal) and **Activate** (deactivated → direct POST). Only an `admin` for that org’s tokens, or a `superAdmin`, may change state. A new reversible `IngestionToken.deactivated_at` (migration `0012_ingestiontoken_deactivated_at`) keeps the pause distinct from the terminal `revoked_at`; the webhook lookup and `IngestionToken.verify()` both refuse a paused token (401), and a *revoked* token can never be re-activated. | **Done** — pause/resume without revoking and recreating. Guards: `TestTokenActivationLifecycle`, `TestTokenLifecycleIsolation`. |
| **10b** | **§6.13 / §6.16 P4c — Settings: Sources → delete inactive token — SHIPPED 2026-10-17** | In Settings → Sources, an inactive row (revoked **or** deactivated) now offers **Delete** behind a confirm modal; an active token is refused until it is deactivated. Only an `admin` for that org’s tokens, or a `superAdmin`, may delete. The `ingestion_token.deleted` audit row is written *before* the row is removed, in the same transaction, so the trail keeps the token id and owning org. | **Done** — removes stale credentials and keeps the token list tidy. |
| **10c** | **§6.13 / §6.16 P4d — Settings: Sources → deleting a feed's last token deletes the feed — SHIPPED 2026-10-17** | Deleting a token that is a feed's **last** credential now also deletes the feed and its mapping profile, so the **Sources** list stops advertising a feed that can never receive anything. The feed's alerts are kept (`Alert.ingestion_source` is `SET_NULL` — evidence outlives the profile); the confirm modal states the consequence before the click; `ingestion_source.deleted` is audited. Also fixes two false UI claims found on the way: the source page rendered a *deactivated* token as **Active**, and `sources_list.html` said an open feed "accepts unauthenticated posts" (false since strict token auth) — closes §10.8(a). | **Done** — tidies the Sources list without losing evidence. Guards: `TestFeedReapedWithLastToken`, `TestSourceDetailTokenStatus`. |
| **11** | **Security: §11.2 (M3) + S3 + S5** | **M3** playbook secrets are global + cleartext to any authenticated user of any org. **S3** per-account lockout on the **API** login; stop trusting client `XFF`. **S5** attachment aggregate caps + extension-from-content-type. | **Medium (security)** — M3 blocks multi-tenant trust; S5's caps are the analyst-facing half (upload robustness). |
| **12** | **§2.6 + §2.7 (test hardening) + §7.4 + §8.5/§8.7 + §11.4-L1/§11.5** | Coverage back ≥ 80% (79% since the tenant wave) + the Phase-12/T2 QA list; Tailwind source/artifact split (§7.4 — the committed `app.css` still drifts from a fresh `make css`); mutation coverage; timestamp wording; unaudited procedure attaches; Django admin outside the role model; `django.request` log forging. (§11.3 protocol-relative `href` **fixed 2026-10-17**.) | **None** — engineering hygiene + accepted Low/Info follow-ups. |
| **13** | **Orphaned routes** | `ui-case-attachment-list` (+ its now-dead `ui/case_attachments.html`) and `ui-case-attachment-detail` (405) are reachable by URL only — the case page renders its own attachment table. `ui-case-export` / `ui-taxonomy` are now wired (P1), so this row supersedes the old claim. | **Low** — dead surface; delete or build an entry point. |
| **14** | **§12.6 — Taxonomy: three cards break the mono convention for their values — SHIPPED 2026-10-17** | `ui/templates/ui/taxonomy.html` now renders the **Case Statuses**, **Alert Statuses** and **Observable Types** value / name cells as `<td class="mono">`, matching the four code cards (**Case Scope**, **Case Phase**, **Case Type**, **Data Classification**). | **Done** — visual consistency on the Taxonomy page; display-only, no behaviour change. |
| **15** | **§12.7 — Cases list: make the case title clickable — SHIPPED 2026-10-17** | `ui/templates/ui/case_list.html` now links the **case title** to `ui-case-detail` (same URL as the **case number**); it is a real `<a>`, so keyboard access needs no JS. | **Done** — small UX improvement. |
| **16** | **§12.8 — Case Details (General) + verdict taxonomy + close flow — SHIPPED 2026-10-17** | Code shipped (`PLAN-2026-10-17-case-verdict-close-flow.md`, `2476edb`); **independently verified 2026-10-17 at 18/18 ACs** (`docs/verification/VERIFY-2026-10-17-case-verdict-close-flow.md`, re-issued in `554bb0d`; one new low deviation **D-N1** → §12.8-fu3); **security pass 2026-10-18 = CONDITIONAL PASS** (`SECURITY-2026-10-18-case-verdict-close-flow.md`). Inline metadata edits, Tags row, Verdict on Case + Alert + Taxonomy, enforced close (Verdict + closing comment) and close-propagation to linked alerts. | **Done** 2026-10-19 — the §12.8-fu4 hardening conditions (M1/L2/M2) landed. |
| **17** | **§12.9 — UI layout/style standardization** | Establish and apply a small set of UI/layout rules across the UI: (1) Input field height must match the button height next to it. (2) For action columns in tables, prefer compact icon-only actions with accessible names (not full text buttons) where space-limited and common. (3) Standardize table markup/styles: the table lives inside a `.card`, and filters go in a separate card above the table (use **Observables List View** as the reference). Codify in a short "UI Layout Guidelines" note (e.g. in `ui/README.md` or `docs/spec/ui-style.md`) and apply to affected views (e.g. Cases, Alerts, Tasks, Users, Tokens) to avoid drift. | **Low** — global consistency; reduces CSS/markup churn. |
| **18** | **§13 — Oversized-module maintainability backlog** | 20 module-refactor candidates identified after the `ui/views.py` split, plus one small template-hygiene cleanup (§13.21); `cases/views.py` (2,821 lines) is the next P0. | **None** — engineering hygiene; no analyst-visible change. |


---

## 1. Phase 3 gate remediation

Status: **complete on SQLite and Postgres.** `491 passed, 3 skipped` on SQLite, `470 passed,
24 skipped` on Postgres (2026-10-07), `mypy` clean on 95 files (strict), `manage.py check` 0
issues, migrations in sync, `make check` green. Round-3 Highs H3-1 (observable re-hash), H3-3 (R11
AST standard), H3-4 (correlation_key derivation) are **FIXED** and hold on Postgres; the
`db-postgres` re-gate (§1.11) is **DONE**. H3-5 (coverage) is folded into §2.1. **Only H3-2
remains: the forward-only migration gap is documented but deliberately unfixed on SQLite** — its
verdict is recorded in §3.1 (Postgres re-verification gave it the same call: forward-only
discipline, no code change is safe on SQLite).

- [x] **1.1 — `Case.save()` number allocation** `C1` — **DONE, verified**
- [x] **1.2 — `manage.py` default settings module** `H2` — **DONE, verified**
- [x] **1.3 — Replace the four vacuous schema tests** `H1`, `R11` — **DONE, verified**
- [x] **1.4 — `Alert.source_ref` digest fallback** `C3` — **DONE, verified**
- [x] **1.5 — Enum `choices` + `CheckConstraint`s** `H5`, `L1` — **DONE, verified** (`test_enum_contracts.py`)
- [x] **1.6 — Drop the redundant indexes** `M1` — **DONE, verified**
- [x] **1.7 — Rename `Alert.source_id` → `ingestion_source`** `M2`, `M3` — **DONE, verified**
- [x] **1.8 — `Observable.data_hash`** `H3` — **DONE, verified**
- [x] **1.12 — Mutation guards could not mutate UNIQUE/CHECK constraints** — **SUPERSEDED by 1.13**
- [x] **1.13 — Mutation 5 was a no-op; the guard lied about itself** `round2 C-1` — **FIXED**
- [x] **1.14 — FK `on_delete` audit is blind to `CASCADE`** `round2 C-2` · **CRITICAL** — **FIXED**
- [x] **1.15 — `M1` over-pruned: two indexes are still needed** `round2 H-5` — **FIXED**
- [x] **1.16 — Postgres-only corruption: sequence not advanced for explicit numbers** `round2 H-6` — **FIXED (SQLite-blind, see below)**
- [x] **1.17 — `data_hash` backfill uses a different hash function than the runtime** `round2 H-7` — **FIXED**
- [x] **1.18 — Pin a runnable static-analysis command; "mypy clean" was not one** `round2` — **FIXED**
- [x] **1.19 — Smaller round-2 items** `round2 H-1`, `H-3`, `H-4`, `L-2` — **FIXED**
- [x] **1.20 — Round-3 Highs** `round3 H3-1..H3-5` — **H3-1 · H3-2 · H3-3 · H3-4 CLOSED; H3-5 closed via §2.1**
- [x] **1.11 — Re-run the `db-postgres` gate** — **DONE (2026-10-06)**

---

## 2. Quality gates not yet met

- [x] **2.1 — Coverage → ≥ 80%** Plan §11, `pyproject` `fail_under = 80` — **DONE (2026-10-07, Phase 10a)**
  **83.10%** at the Phase 10 gate; `fail_under=80` kept. **⚠ Regressed 2026-10-11 → 79%** after the
  tenant-isolation + org-management waves landed (new `identity/scoping.py`, `identity/permissions.py`,
  `identity/services.py`, UI views). Non-blocking by decision, but it is below the configured gate — see §2.6.
- [x] **2.2 — mypy strict clean** Plan DoD — **DONE** (95 source files, strict, 0 errors)
- [x] **2.3 — Enforce the one-way dependency rule with a lint check** Brief §0.4, `R3` — **DONE (2026-10-09)**
- [x] **2.4 — Reverse-direction link-table indexes** `M5`, plan §6.3 — **DONE**
- [x] **2.5 — Remaining Medium/Low cleanups** `M4`–`M14`, `L4`–`L6` — **DONE (2026-10-09)**
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

- [x] **3.1 — Postgres verification** `R4`, `R10`, plan §5 · **DONE — verified 2026-10-06, re-confirmed 2026-10-07**
- [x] **3.2 — Multi-tenancy timing** Plan §14 Q4 — **DECIDED 2026-10-06: single-tenant + nullable `Organisation` FK**
- [x] **3.3 — Markdown dialect** Plan §14 Q5 — **DECIDED 2026-10-06: sanitized CommonMark**
- [x] **3.4 — `Alert.type` vs `dataType` taxonomy** Plan §14 Q3 — **DECIDED 2026-10-06: independent, exposed as a tag**
- [x] **3.5 — Alert severity default when unmapped** Plan §14 Q1 — **DECIDED 2026-10-06: `2` (Medium) + warning**
- [x] **3.6 — Multi-signal correlation default** Plan §14 Q2 — **DECIDED 2026-10-06: per-source `correlation_key`, configurable window; default 10 minutes**

---

## 4. Environment & repository

- [x] **4.1 — `README.md` missing but referenced by `pyproject.toml`** — **DONE (2026-10-06)**
- [x] **4.2 — Local git repository** — **DONE**
- [x] **4.5 — Move to a real GitHub-hosted remote** — **CLOSED 2026-10-08**
- [x] **4.6 — Production secret hardening + repo-wide secret-hygiene guard** — **CLOSED 2026-10-08**
- [x] **4.3 — Hash-pin the requirements** `R9`, plan §5 — **CLOSED 2026-10-07 (Phase 11)**
- [x] **4.4 — `docs/spec/` populated with the implemented-system spec** (2026-10-07)

---

## 5. Remaining phases

Phases 4–10 are complete and pass their gates on SQLite and Postgres; Phase 10 shipped in
commit `e3a94d8` (2026-10-07, §5.7). This section is now fully historical.

- [x] **5.1 — Phase 4: Ingestion** (AGENTS.md §4.1) — **DONE**
- [x] **5.2 — Phase 5: Escalation, Observables, Tasks, Timeline** (AGENTS.md §4.2–4.3) — **DONE**
- [x] **5.3 — Phase 6: Orchestration Gateway** (AGENTS.md §4.4) — **DONE — closes the MVP loop**
- [x] **5.4 — Phase 7: Realtime Ledger** (AGENTS.md §2 Module B) — **DONE (2026-10-06)**
- [x] **5.5 — Phase 8: Query API** — **DONE (2026-10-06)**
- [x] **5.6 — Phase 9: Minimal UI** — **DONE (2026-10-06)**
- [x] **5.6 — Phase 9: Conformance, Security & Performance** — **DONE (2026-10-07)**
- [x] **5.7 — Phase 10: Conformance, Security & Performance** — **DONE (2026-10-07)**
- [x] **5.8 — AC1.3 / AC1.4 verification** — **DONE (2026-10-06, via §3.1)**
- [x] **5.9 — §3.1 Postgres gate executed (2026-10-06)** — **DONE**

---

## 6. Deferred scope (committed, not yet scheduled)

- [x] **6.1 — T2 endpoints** Plan §7.3 — **P1–P5 SHIPPED 2026-10-09** (the full T2 wave)
- [x] **6.2 — Postgres-only indexes** `L3`, plan §6.3 — **SHIPPED 2026-10-09**
- [x] **6.3 — Per-link tags on observable link tables** Plan §13 #11 — **SHIPPED 2026-10-09**
- [x] **6.4 — Tenant isolation** Phase 10 SEC-AUDIT F2, plan §13 deferred (a) — **SHIPPED 2026-10-10** (strict multi-tenant, ADR-004; see `PLAN-2026-10-10-tenant-isolation.md`)
- [x] **6.5 — Observable PATCH/DELETE cross-case blast radius** Phase 10 SEC-AUDIT F3, plan §13 deferred (b) — **SHIPPED 2026-10-09**
- [x] **6.6 — Production cookie/secret hardening** Phase 10 SEC-AUDIT F9, plan §13 deferred (c) — **CLOSED 2026-10-08 via §4.6**
- [x] **6.7 — Paged case-detail timeline** Phase 10 PERF F2, plan §13 deferred (d) — **SHIPPED 2026-10-09**
- [x] **6.8 — Orchestration authoring surface** — **SHIPPED 2026-10-09**
- [x] **6.9 — Analyst UI catch-up to the T2 surface** — **SHIPPED 2026-10-09**
- [x] **6.10 — Frontend stack actually in place (HTMX + Tailwind + Font Awesome)** — **SHIPPED 2026-10-09**
- [x] **6.11 — Login rate-limiting / brute-force hardening** §6.6 leftover — **SHIPPED 2026-10-09** (note: per-account lockout is UI-only so far — see priority #1, S3)
- [x] **6.12 — Settings: organisations + users (superAdmin)** Plan `PLAN-2026-10-10-org-management.md` — **DONE 2026-10-11** (O1–O5 shipped; verifier 24/24 ACs `VERIFY-2026-10-11-org-management.md`; security review `SECURITY-2026-10-11-org-user-management.md` clear). `superAdmin`-only **Settings**: **Organisations** (list/create/edit/delete) + **Users** (list/create/edit/delete) API + UI (uniform 404, audit, guarded atomic delete with `members=reassign|delete`, last-superAdmin/self guards; rail item + `,` shortcut, `<dialog>` modals, blocked-delete + member-choice). **Q4: UI + Amalthea API only — no TheHive `compat/` parity.**
- [ ] **6.13 — Backlog: ingestion-token management + audit-log viewer (superAdmin Settings)** Split out of §6.12 (decision Q1/Q8). Settings → **Ingestion tokens** (per-org webhook tokens: create/revoke, show-once secret) and read-only Settings → **Audit log** (the `AuditLog` already records superAdmin cross-org reads). Not scheduled.
- [x] **6.14 — Wave 6.3A: Settings UI hardening — scoped fix (CSP / progressive enhancement)** Scope **(A)**: `ui/templates/ui/_modal.html` + the two Settings templates (+ `base.html` asset wiring). From `SECURITY-2026-10-11-org-user-management.md` (F3/F5/F6/F7/F8/F9/F10). **DONE 2026-10-11**: externalised `modal.js`/`modal.css`, server-rendered the four modal bodies as partials (HTMX **and** no-JS create/edit/delete → O4-b met), removed the `innerHTML`/`escapejs` sink and inline `style=`, added `lock_active_superadmins`/decorators for the last-superAdmin TOCTOU (incl. the org-delete path), fixed F6 (uniform 404), F7 (malformed ids/oversize names no-500), F8 (`parse_bool`), F9 (`secret_hash` excluded from admin). Security re-audit **clear** (`SECURITY-2026-10-11-wave63a.md`); verifier 17/17 met (`VERIFY-2026-10-11-wave63a.md`). Plan §13A.
- [ ] **6.16 — UI completeness wave (all 24 gaps)** Plan `PLAN-2026-10-11-ui-completeness.md`. Restores the analyst loop and
  completes the entity surface: **P1** wire up built-but-unreachable features (attachments, merge, export,
  apply-template, taxonomy) + rail entries for Observables/Tags/Case-templates/Taxonomy + tenant indicator +
  case-template edit + password change + 500/403 pages + cheat-sheet as a server-rendered `<dialog>` + Font Awesome
  + keyboard coverage; **P2** alert triage, case edit, observable edit/delete; **P3** procedures/TTP UI; **P4**
  Settings→Ingestion tokens + Audit log (pulls §6.13 forward); **P5** playbook action round-trip + sanitized
  Markdown; **P6** global search; **P7** per-org realtime + presence + scoped HTMX adoption. Open questions Q1
  (Markdown dependency) gate P5 and Q2 (bulk stub) gates P1. Source: post-§6.15 UI audit (user decision
  "everything", 2026-10-11).
  - [x] **P1 — DONE 2026-10-11** (A1/A2/A3a/A3b/B1/B2/B4/C4/C7/D2/D6/D7/D8 + Q2). Verifier: 11 met / 3 partial /
    0 not met (`docs/verification/VERIFY-2026-10-11-p1-ui-completeness.md`). All partials and the one MEDIUM
    security finding (case-template mutations had no role gate) were fixed in-wave. New guards:
    `test_ui_nav.py` (rail ⇄ `keys.js` `ROUTES` ⇄ cheat-sheet ⇄ server table) and `test_ui_icons.py` (every
    `fa-*` used has a subset rule). Fixed en route: the mypy gate regressed because `amalthea/urls.py` imported
    `ui.views` (now dotted-string handlers); `.btn-err` was undefined app-wide; `500.html` made standalone so the
    500 path issues **0** queries even for an authenticated request. Gate: `make check` **1430 passed / 3 skipped**.
    Details: `docs/spec/deviations.md` “Wave 6.16-P1”.
  - [x] **P2 — DONE 2026-10-11** (C1 alert triage, C2 case edit, C3 observable edit/delete). Gate: `make check`
    **1464 passed / 3 skipped**. New guards: `tests/conformance/test_ui_p2_wiring.py` (30 cases: rendered form
    contract, bounded status vocabulary, CSRF, uniform 404, cross-case force on both edit and delete, audit
    pk integrity, cross-org assignee refusal, out-of-range enum → form error, audit-failure rollback).
    **Independently verified** — `docs/verification/VERIFY-2026-10-11-p2-ui-completeness.md` (C1/C2/C3 Met) and
    security-reviewed — `docs/reviews/REVIEW-2026-10-11-p2-ui-mutations.md`. **All security findings fixed +
    re-audited (FIXED, no regression):** M1 cross-tenant assignee injection (`_resolve_login(..., org=...)` scopes
    login+UUID; applier/task path thread the owner org), L1 out-of-range `severity`/`tlp`/`pap` → 400/form error
    (was a 500), L2/NEW-2 the audit row now shares the mutation transaction (`case_edit`, `observable_edit`,
    `alert_status`), NEW-1 the case-edit assignee select prefilled a UUID while its options are logins (an
    untouched save silently unassigned the case) — now prefills the login, N1 re-render reads `QueryDict` scalars
    via `.get()`. Fixed en route: ... the case-edit TLP/PAP/severity selects never preselected (`form_values` is a
    str, the choices are ints — `==` in a template is a Python `==`); the triage `<select>` submitted `AlertStatus.pk`
    while the view validated `.value`; `apply_case_update` was never imported into `ui/views.py`; `observable_delete`
    logged `target_id="None"` (pk read after `delete()`); the observable delete acknowledgement lived outside the
    form so it neither gated nor forced; a literal `Response` import was missing; `resolve_alert`/`resolve_observable`
    raised `ValidationError` (500) on a non-UUID id where `resolve_case` already returned 404.
    Details: `docs/spec/deviations.md` "Wave 6.16-P2".
  - [x] **P3 — DONE 2026-10-12** (A5 Procedures/TTP UI: attach form + list on case detail, single + bulk
    POST views; `@require_GET` routes → `@require_POST` mutations). Gate: `make check` **1539 passed /
    3 skipped**. New guards: `tests/conformance/test_ui_procedures.py` (22 cases) and
    `tests/conformance/test_ui_css_tokens.py` (an undefined `var(--color-*)` — the silent
    invalid-at-computed-value-time failure that shipped 7 broken declarations — is now a test failure).
    **Independently verified** — `docs/verification/VERIFY-2026-10-12-ui-completeness-p3.md` (5/5 ACs Met)
    and security-reviewed — `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (0 Critical / 0 High).
    Fixed in-wave: 7 undefined CSS tokens + a dropped focus ring (WCAG 2.4.7); the bulk-create per-item
    **savepoint** bug (a DB-level failure in one item poisoned the batch so every later item died with
    `TransactionManagementError` — one bad row silently became N; regression test confirmed to fail
    without the fix); duplicated `_resolve_ttp_value` in the UI layer; exception text leaking into flash
    messages; an unbounded/quadratic bulk item loop (`MAX_BULK_PROCEDURES = 100` + hoisted `getlist`).
    Follow-ups: `TODO §11.4`. Details: `docs/spec/deviations.md` "Wave 6.16-P3".
  - [x] **P3–P7** — **P5 done** 2026-10-12 (D1 + D3; verified + security-reviewed — follow-ups in §11); P4/P6/P7 not started. (P5 was unblocked 2026-10-12: Q1 decided → Option A, `markdown-it-py` +
    `nh3` pinned in `requirements/base.txt` and governed by **ADR-005**). The other open questions are
    decided by the plan's recommendations (Q2 = stub deleted, Q3 = HTMX scope, Q4 = audit viewer
    read-only, Q5 = search over cases/alerts/observables).
- [x] **6.15 — Wave 6.3B: repo-wide ADR-003 compliance (externalise all inline scripts/styles)** Scope **(B)**, follow-up to §6.14: audit the whole `ui/templates/` tree and externalise **every** remaining inline `<script>`/`<style>`/`style="…"`/`on*=` so `script-src 'self'`/`style-src 'self'` hold without `unsafe-inline`. Plan §13B. **DONE 2026-10-11**: removed all inline `<script>` from `playbook_form.html`, `playbook_detail.html`, `observable_detail.html`, `case_template_form.html`, `case_template_detail.html`; added `prettyjson` filter (server-side JSON pretty-printing replaces 4 `json_script`-plus-inline-`<script>` pairs, one of which was invalid HTML inside a `<textarea>`); new external `ui/static/ui/playbook_form.js` (no `innerHTML` — clones a `<template>`) replacing inline handlers; removed every `style="…"` from `404.html`, `case_tags.html`, `observable_list.html`, `playbook_form.html`; added component classes to `app.css`. Fixed a latent bug: the playbook header rows were rendered from a broken `{% for i in "1234" %}`-less block so **no** header row was ever submitted without JS — now four indexed, server-rendered rows. New CI guard `tests/conformance/test_ui_inline_assets.py` (116 cases) fails on any future inline asset. Plan §13B.

- [x] **6.17 — Retro-mapping: re-apply a feed's field mapping to its existing alerts.** Plan §8 P7/P7.1
  (the **§11 Q7** deferral). **DONE 2026-10-16** — requested directly, so it landed out of order as P7 +
  P7.1 instead of staying deferred. The **"Apply to unlinked alerts…"** control on the Settings → Sources
  detail page (beside "Save mapping") is a **validate-and-count** step (`intent=apply` writes nothing;
  renders the exact counts, the field list and the per-target dry run) then a **confirm** step
  (`intent=apply_confirmed`) that saves the rules and re-maps **inside one transaction** (audit
  `ingestion_source.mapping_applied`; `ingestion_source.updated` only when the config changed). Re-mapped:
  `title`/`description`/`summary`/`severity`/`date`, equal to what ingest would produce today.
  **The safety shape a future reader must not undo:** the candidate predicate is exactly *this feed*
  (`ingestion_source=source`) **and** `case IS NULL` **and** `status.stage in (New, InProgress)`;
  `type`/`source_ref` are the dedupe+replay key and are **never** rewritten (**MAP-9**); a text field the
  mapping cannot answer keeps its stored value (**MAP-10**); `MAX_REMAP_ALERTS = 5000` per request with a
  **refusal rather than a silent half-apply** (over it, neither the config nor the alerts are written)
  (**MAP-11**); a field-less but record-changing pass is reported as `refreshed` (**MAP-12**). **Still NOT
  done:** a feed over `MAX_REMAP_ALERTS` has no background-task path (it needs a status surface). Plan:
  `docs/planning/PLAN-2026-10-16-source-field-mapping.md` §8 P7/P7.1; deviations **MAP-9…MAP-12** in
  `docs/spec/deviations.md`. Evidence: `tests/conformance/test_mapping_remap.py` (32 cases) +
  `tests/conformance/test_settings_source_mapping.py` (53 cases).
- [x] **6.18 — Dead-code observation from the field-mapping plan (P5): `ingest/views.py::_verify_secret` — DONE 2026-10-17.**
  Verified 2026-10-16: `ingest/views.py` defined `_verify_secret` (was `:105`) and it had **no callers** (the
  webhook token path is `_authenticate_token` → `IngestionToken`), so that copy was **dead** — the live
  verifier is `identity/models.py::_verify_secret` (called from `IngestionToken.verify`). The plan's
  companion `canonical_payload` is **not** in `ingest/views.py`: it lives in `ingest/references.py` and is
  **live** (`payload_digest` → `resolve_source_ref` → `ingest/pipeline.py`; also
  `tests/conformance/test_ingest_pipeline.py`), so it must not be deleted — only the plan's file pointer is
  stale. **Fixed 2026-10-17:** the dead function is gone, along with its unused `_ph`,
  `argon2.PasswordHasher` and `constant_time_compare` imports, and the module docstring bullet that
  described a legacy plaintext fallback the receiver no longer has (that path was retired when migration
  `identity.0006_create_default_org` adopted `IngestionSource.webhook_secret_hash` rows as hashed
  `IngestionToken`s). **Drift found on the way:** commit `0d1d541` carried this removal in its *message*
  but its diff never touched `ingest/views.py` — the file was unchanged until the follow-up commit, so both
  this item and `docs/spec/deviations.md` asserted a removal that had not happened. Two dated 2026-10-10
  artifacts (`docs/planning/VERIFY-2026-10-10-tenant-isolation-P1P2.md`,
  `docs/reviews/SECURITY-2026-10-10-tenant-isolation.md`) cite `ingest/views.py` as the verifier that kept
  migrated plaintext secrets authenticating; the mechanism was real but lived in `identity/models.py` —
  those are point-in-time snapshots and are left as they are.

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

- [x] **8.1 — Every mutation guard must prove its own mutation landed**
- [x] **8.2 — The standard is enforced mechanically, for all tests, not by convention**
- [x] **8.3 — `make check` is the single Definition of Done**
- [x] **8.4 — Pre-commit and pre-push hooks installed** (`make hooks`)
- [ ] **8.5 — Extend mutation coverage to the remaining contract assertions**
- [x] **8.6 — Guard the CI invocation itself** — **CLOSED 2026-10-07 (Phase 11)**
- [ ] **8.7 — Timestamp-unit wording in plan §7.1** Verifier (2026-10-07), POST-closure note

---

## 9. Scratch/probe hygiene

- [x] **9.1 — Stale `__pycache__` from deleted scratch tests** Verifier recommendation 7 — **DONE (2026-10-07)**
- [x] **9.2 — `tests/conformance/test_zzz_probe.py`** (scratch probe) — **REMOVED 2026-10-10.** Its one unique assertion was subsumed by `test_seed_migrations.py::test_the_seed_rows_are_present_after_a_normal_migrate`, and its delete-seed-row test was invalid on SQLite by construction (H3-2), making the DoD gate permanently red. This was the sole cause of the CI `gate` job failing (`make check` + the coverage step).

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

- [x] **10.1 — Role-scoped user management.** **DONE 2026-10-20** — `PLAN-2026-10-20-org-scoped-user-management.md`
  (ADR-010), implemented, independently verified 24/24 ACs (`docs/verification/VERIFY-2026-10-20-org-scoped-user-management.md`)
  and security-reviewed (`docs/verification/SECURITY-2026-10-20-org-scoped-user-management.md`; 0 Critical/High;
  SA-01 fixed, SA-06 rejected as a false positive). An `admin` now manages every user of **their own org**
  (list/create/edit/delete/deactivate, change role within the org); `superAdmin` keeps cross-org reach via a
  per-org table layout. All §6.12 guards hold per-scope (uniform 404 for out-of-scope ids; no escalation to
  `superAdmin`; last-superAdmin guard; audit every mutation via `identity.services`). A lower role never
  *sees* a higher-role user (admin list `.exclude(role=SUPER_ADMIN_ROLE)`). Original request: §6.12 shipped Settings → Users but **superAdmin-only**
  (`ui/views.py::_require_superadmin_ui`); extension kept that cross-org reach while adding own-org admin scope.
- [x] **10.2 — Create a case manually from the UI.** **DONE 2026-10-13** — `PLAN-2026-10-13-ui-case-create.md`,
  implemented and independently verified (`VERIFY-2026-10-13-ui-case-create.md`, verdict **Partial**) +
  security-reviewed (`REVIEW-2026-10-13-ui-case-create.md`). `GET|POST /cases/new` (`ui-case-create`, above
  the `cases/<str:case_id>` route) renders a `can_mutate`-gated form (title, description, severity, TLP/PAP,
  status, assignee, tags), POSTs through the shared `cases/create.py::create_case` applier, writes a
  transaction-shared `AuditLog(action="case.created")` and redirects to the case; a new `n` shortcut + rail
  link. The review found (and closed) a Medium regression — `/api/v1/case` `status` `""`/`null` returned 500
  instead of the `InProgress` default — plus an accepted Low (viewer rail/cheat-sheet gating). `10.3–10.6`
  fields were **not** included (that row still needs its own plan); extracted observables come from the
  shared applier's title/description extraction (no extraction checkbox — deviation W102-2). See
  [`COMPLETED.md`](./COMPLETED.md).
> **10.3–10.6 SHIPPED 2026-10-15** — `PLAN-2026-10-14-case-classification-fields.md`, commit `822d050`,
> verified 21/21 ACs at [`docs/verification/VERIFY-2026-10-14-case-classification-fields.md`](./docs/verification/VERIFY-2026-10-14-case-classification-fields.md)
> (security audit clean). Q-D1…Q-D5 below were answered as Q-D1…Q-D10 in §12 of that plan — note **Q-D2 went
> the opposite way** to this section's recommendation: `phase` shipped as a *separate* field, deliberately not
> folded into `CaseStatus`/`CASE_STAGES`. The bullets are kept verbatim as the original requirement text.

- [x] **10.3 — Case `scope`.** New enum field: *Single Asset/User*, *Multiple Assets/Users*, *Single
  Department*, *Multiple Departments*, *Complete Enterprise*, *Third Party/Supplier*. (Values TBC.)
- [x] **10.4 — Case `type`.** New enum/taxonomy: *Malware*, *Phishing*, *Password Leak*, *Ransomware*,
  *Business Email Compromise*, … — the list is explicitly open; decide free-form vs seeded vocabulary vs
  a `CaseType` table (and how it relates to the existing alert `type` and the `tags` model).
- [x] **10.5 — Case `data classification`.** New enum field: *No Data*, *Public Data*, *Personal Data
  (PII)*, *Sensitive Data (Credentials, Intellectual Property)*. **Note:** distinct from TLP/PAP; label is
  a *classification*, not a sharing directive. Per §6 severity rule, never signal by colour alone.
- [x] **10.6 — Case `phase` (IR lifecycle).** *Initial Triage*, *Containment*, *Analysis*, *Remediation*,
  *Cleanup*, *Close*. **Reconcile with `CaseStatus` first** (see Q-D2) — the existing `CaseStatus`
  (`value`/`stage`∈{New,InProgress,Closed}/`order`/`hidden`) plus `Case.stage` already model a coarse
  lifecycle, and `resolve_case_status` is ADR-002 §D11-tolerant. Decide: new `phase` field, new
  `CaseStatus` rows, or replace/rename. Whatever ships must stay parity-consistent across the API,
  `case_json`, the UI, and the automation triggers that key on `stage`.
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
- [x] **10.8 — Ingestion-token surface + stale webhook copy (mostly absorbed by §6.16 P4).** *(DONE
  2026-10-17.)* (a) **Done, both parts.** The `sources_list.html` URL copy already showed the
  source-less `POST /api/v1/alerts/webhook/` (ADR-008); the remaining debt — the `open` / `required`
  pills and the sentence *"A source marked open accepts unauthenticated posts"*, false since strict
  token auth (2026-10-10) — is fixed: the pill is now three credential states, `required` / `paused` /
  `none` (a token is usable only while neither revoked nor deactivated, §10a), and the false security
  claim is gone. Copy **and** the pinning test
  (`test_ui_loop.py::test_the_sources_page_shows_whether_a_secret_is_required`) were changed together
  (WTR-2 resolved). (b) **Done**: the **Ingestion tokens** section of `docs/spec/identity-auth.md`
  exists and was extended for the §10a/§10b lifecycle (deactivate/activate/delete, the feed reap on the
  last token, `ingestion_source.deleted`); `docs/spec/data-model.md` gained `deactivated_at`.
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
- [x] **10.12 — Ingestion-token lifecycle (§10a–§10c): both outstanding passes discharged 2026-10-19.** *(DONE
  2026-10-19.)* The wave shipped 2026-10-17 with its verification graded by *the same session that wrote the
  code* and with **no** security review at all — the §12.5/§12.8 debt class, simply never given a checkbox. Both
  passes are now delivered:
  - **Independent verification** —
    [`VERIFY-2026-10-19-ingestion-token-lifecycle-independent.md`](./docs/verification/VERIFY-2026-10-19-ingestion-token-lifecycle-independent.md):
    **22/22 ACs Met** (12 Plan A + 10 Plan B), **16/16 design decisions** implemented, re-derived from source at
    `44282f8`, ~30 commits after the build. The 2026-10-17 report is kept as the historical record and now points
    forward. *Caveat recorded in the new report:* its **Mutation check** section is carried over from the
    2026-10-17 report rather than run here (writing was forbidden), so that section is the **provisional**
    report's evidence; unaffected are the AC grades, which this pass made.
  - **Security review** —
    [`SECURITY-2026-10-19-ingestion-token-lifecycle.md`](./docs/reviews/SECURITY-2026-10-19-ingestion-token-lifecycle.md):
    **PASS** — 0 Critical / 0 High / **2 Medium** / 2 Low / 2 Info. Confirms: the two paused-token refusals
    (`ingest/views.py:121` **and** `identity/models.py:333`) are consistent and are the **only** token-resolution
    paths anywhere; all four mutation routes go through one `_org_scoped_token` and return a uniform 404 *before
    any write*; both audit rows precede the row they describe; Argon2 + one-time plaintext, no secret material in
    any audit row. `/`
  - **Findings are filed as §10.12-fu1…fu4 below** — nothing was silently folded into "Done".
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

- [x] **11.1 — M1: does `read-only` mean read-only?** — **RESOLVED 2026-10-12** (ADR-006 +
  `PLAN-2026-10-12-viewer-role-enforcement.md`; role renamed **`viewer`**). The policy call was made
  and enforced in one place per layer rather than per view: `ViewerReadOnly` in
  `DEFAULT_PERMISSION_CLASSES` (every endpoint, present and future),
  `resolve_case/alert/observable(write=True)` (the resource path), and `not_viewer` on all 38
  mutating UI views. Route-enumeration sweeps (`tests/conformance/test_viewer_readonly_ui.py`,
  `test_viewer_readonly_api.py`) fail when a new mutating route is added without a gate. Every surface
  named below is now denied; the exemption list is exactly `{login, logout, ui-password-change}`.
  Verification: `docs/verification/VERIFY-2026-10-12-viewer-role-enforcement.md`.
- [x] **11.2 — M3: playbook action secrets are global and rendered in cleartext — DONE 2026-11-01.** `Playbook` had **no
  `owner_org`** (`automation/models.py:13`) and HTTP playbooks keep credentials in `config["headers"]`
  (`Authorization: Bearer …`); `playbook_edit`/`playbook_detail` render the full map to **any authenticated
  user of any org**. *Partial mitigation from 11.1:* a `viewer` can no longer reach `playbook_detail`'s
  GET (ADR-006 decision 7) — but a cross-org **analyst** still can, so the leak stands. Options (see the
  review): (1) scope `Playbook` with `owner_org` + `org_q`
  (matches ADR-004 — also removes the four `ui-playbook-*` entries from `_UI_GLOBAL_READ_ROUTES`);
  (2) keep global but stop storing plaintext secrets — store a secret reference and render a masked
  placeholder; (3) minimum: gate the GET views to `analyst`+ **and** mask header values. Note (3)
  conflicts with the D1 AC "round-trips headers", so the D1 test must change with it. Add a test asserting
  a viewer (and cross-tenant) GET never returns raw header values.
  **PLANNED 2026-10-30:** [`PLAN-2026-10-30-playbook-tenant-scoping.md`](docs/planning/PLAN-2026-10-30-playbook-tenant-scoping.md).
  Approved: Option A (org scope + viewer API denial); default-org backfill; per-org names; event-org
  dispatch. Residual accepted: same-org analyst/admin cleartext until secret references/masking ship.
  **DONE 2026-11-01:** 65/65 ACs met; full suite `3206 passed, 3 skipped`; `make check` green;
  security audit **CONDITIONAL PASS** with unchanged accepted residuals
  ([report](docs/security/SECURITY-2026-11-01-playbook-tenant-scoping.md)).
- [x] **11.3 — L1: protocol-relative `href` survives the sanitizer — FIXED 2026-10-17.** `ui/markdown.py`
  registers a `markdown-it` core rule (`_strip_protocol_relative_links`, after the `inline` pass) that
  drops the `href` of any link whose target has an empty `urlsplit()` scheme **and** a `//` prefix: the
  link text stays readable but is inert. Root-relative (`/path`) and scheme-bearing (`https:`, `mailto:`)
  links are untouched, and `nh3` remains the single trusted boundary (the filter runs *before* it, adding
  no new trusted code). Guards: `test_ui_markdown.py::test_protocol_relative_link_is_neutralised`,
  `::test_protocol_relative_link_with_port_and_path_is_neutralised`,
  `::test_scheme_bearing_and_root_relative_links_survive`.

### 11.4 — P3 (procedures/TTP UI) review follow-ups (2026-10-12)

From `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (0 Critical / 0 High; verification
`docs/verification/VERIFY-2026-10-12-ui-completeness-p3.md` = 5/5 ACs met).

- [x] **M1 (duplicate of §11.1)** — **RESOLVED 2026-10-12.** Both views carry `@not_viewer`
  (`ui/views.py`), and both are asserted by the route-enumeration sweep in
  `tests/conformance/test_viewer_readonly_ui.py`. The `.btn` affordances are hidden for a viewer
  (`can_mutate`), so an analyst-only surface no longer looks available.
- [ ] **L1 — procedure attach is unaudited.** Neither the single nor the bulk view writes an `AuditLog`
  row or a case-ledger event, so an attached procedure does not show up on the case timeline. §9 of the
  plan did not name procedures and the compat API behaves the same way (parity), so this is "audit it, or
  accept it explicitly". Decide together with the ledger-event work.
- [x] **L3 — the attach form is visible to read-share viewers.** — **RESOLVED 2026-10-12** by the
  viewer policy (ADR-006). The form (and every other write affordance) is now rendered under `can_mutate`,
  which is `not is_viewer(request.user)` — the predicate the views themselves enforce, so the form and the
  gate cannot disagree. A viewer holding a write share is still denied (role beats share) and the form is
  hidden from them. The residual case — a *non-viewer* org with only a read share, who sees the form and
  gets a 404 on submit — is unchanged from P3 and is tracked below.
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
(search) and P7 (realtime) stay in their own plan and should land after
§12.1/12.2 — searching a truncated list is a half-answer.

- [x] **12.1 — Every UI list was silently truncated at `PAGE_SIZE = 200`, with no pager — FIXED 2026-10-18.**
  Plan: `docs/planning/PLAN-2026-10-18-ui-pagination.md` (APPROVED; decisions §11, frozen interface §11a,
  deviations §13). Verified: `docs/verification/VERIFY-2026-10-18-ui-pagination.md` (33/34 ACs met, 0 not met,
  0 deviated — Q7 deferred) and `docs/reviews/REVIEW-2026-10-18-ui-pagination.md` (PASS).
  Shipped: a single `ui/pagination.py::paginate()` primitive (default 50/page, `?per_page=25|50|100|200`
  allow-listed, `MAX_PAGE_SIZE=200` hard ceiling, hostile `?page` never 500s, out-of-range clamps to the last
  page) applied to **all 12** accounts of the working list surfaces — not just the seven named above; the
  alert-detail and case-detail rails are pickers/rails, capped (not paged) at the new `RAIL_CAP = 200` with an
  honest "showing latest N of M" line; `_pager.html` / `_list_summary.html` give every primary list a real
  pager plus a "showing X–Y of Z" count; and the empty states now distinguish *no rows* from *filtered to
  none* (the latter offers Clear filters). Regression tests: `tests/conformance/test_ui_pagination.py` (48)
  and `tests/conformance/test_ui_pagination_edge_cases.py` (50), the latter pinning five defects found in
  review (plural "alertss", a dead falsy-`Page` gate, a missing escape hatch, a `filtered_from` that counted
  the filtered set, and an unbalanced pager tag) — all fixed. The API remains untouched (§11 Q8).
  **Still open:** the filtered-observables `COUNT` cost was never measured at scale (SQLite cannot answer it);
  see the new item below. The `?page=` cap is gone but the **picker/vocabulary** search gap (F1) remains.
- [x] **12.2 — The observables-list filter is decorative — FIXED 2026-10-15.** `observable_list` now reads
  `request.GET`: `q` filters the artifact value (`data__icontains`) and `type` narrows to one `ObservableType`
  id, with `query` / `observable_types` / `type_filter` passed to the context so the dropdown is populated and
  the submitted type stays selected. The filter stays org-scoped (`org_q`) and a non-UUID `type` is dropped
  rather than 500-ing out of the UUID column. Regression tests in `tests/conformance/test_ui_p2_wiring.py`
  (value, type, malformed id, org scoping). **Closed by §12.1 (2026-10-18):** the pager and the honest
  empty state now exist, so filtering a paginated list is a full answer rather than a half one — the empty
  state distinguishes *no rows* from *filtered to none*. Source: audit 2026-10-12.
- [ ] **12.1-fu1 — Measure the filtered-list `COUNT` on Postgres before trusting it (plan §11 Q7).**
  `observable_list`'s `q` filter is `data__icontains` over a JSONB column with no supporting index; pagination
  adds a `COUNT` per filtered request (plus the unfiltered-base `COUNT` for the "filtered from N" disclosure).
  On SQLite this is unanswerable, so the measurement was **honestly deferred** rather than assumed — the
  verification report records it as the sole unmet AC (`docs/verification/VERIFY-2026-10-18-ui-pagination.md`),
  and the security review keeps it as open item O1. Do it when the Postgres suite lands (§3.1): seed ~100k
  observables, `EXPLAIN (ANALYZE, BUFFERS)` a filtered list request, and if the plan or the latency is
  unacceptable add a GIN/`pg_trgm` index (or move to a `search_vector`) — recording the before/after plan in
  the plan doc. Low risk of *correctness* failure; a real risk of a slow list on a large instance.
- [x] **12.3 — No cross-case / correlation surface (AGENTS.md §2 Modules B + C) — SHIPPED 2026-10-18 (read-only Similar panel + correlation-ranked, audited attach; automatic bundling deferred).** `Alert.correlation_key`
  is derived at ingest (`ingest/mapping.py::extract_correlation_key`), indexed (`(correlation_key, date)`)
  and exposed to the query engine — but **no code consumes it**: there is no automatic bundling of
  correlated alerts into a `Case` (Module B: "automatically bundled … identical destination IP within a
  10-minute window") and no "cases sharing this observable" view. The observable detail page *does* list its
  linked cases (`observable_detail.html:178`, "Linked Cases (N)"), so the pivot exists one hop away — but
  nothing links *from* a case to it and nothing ranks or heuristically scores the overlap. Decision first
  (§10.7 "Similar" tab semantics, **Q-D4**); then: an automated-bundle service that is **explicit and
  auditable** (silent auto-merges are unacceptable — every bundle must write a ledger entry + `AuditLog` row,
  per §9 of the plan and ADR-002), plus a case-side "related cases" panel reusing
  `case_observables__observable` (no new model). Source: audit 2026-10-12; AGENTS.md §2. **Q-D4 was decided
  2026-10-14 as deferred** (`PLAN-2026-10-14-case-classification-fields.md` §12) — then **re-opened and
  settled by shipping** on 2026-10-18.
  Plan: `docs/planning/PLAN-2026-10-18-correlation-similar-cases.md` (APPROVED; decisions §11 = Q1b–Q8b,
  frozen interfaces §6, deviations §13). Verified: `docs/verification/VERIFY-2026-10-18-correlation-similar-cases.md`
  (all P1/P2 ACs met; 20/20 tests) and `docs/reviews/REVIEW-2026-10-18-correlation-similar-cases.md`
  (CONDITIONAL PASS → material findings M1–M4 fixed before merge).
  Shipped: `cases/correlation.py` — **one** ranking primitive (`cases_sharing_findings`) feeding both halves,
  plus `candidate_cases_for_alert` (a **pure** dry-run extraction over the alert payload via
  `observables/extractor.py::find_observables`, with the ingest size/depth bounds re-applied) and
  `record_alert_bundled` (one ledger entry `kind="correlation-bundle"` + one `AuditLog` row
  `action="case.alert_bundled"`, both inside the caller's `transaction.atomic()`).
  `_tab_similar.html` is now a real ranked panel with two distinct empty states; `alert_detail`'s merge
  candidates are correlation-ranked rather than "newest open cases"; **`alert_escalate` and `alert_merge`
  both now write ledger + audit** (they previously wrote only a `TimelineEvent`), closing the ADR-002 §9 gap
  on that path; and `ignore_similarity` is consumed for the first time. Under decision **Q1b**, automatic
  ingest-time bundling was **deferred** — see §12.3-fu1. Tests:
  `tests/conformance/test_correlation_similar_cases.py` (20).
  **Addendum 2026-10-20:** the Similar tab gained a second **"Similar alerts"** card
  (`PLAN-2026-10-20-similar-alerts-card.md`; verified 11/11 ACs) — unlinked, same-org alerts ranked by signal
  overlap with the case's attached alerts, with a one-click **Link to case** action
  (`alerts_similar_to_case` in `cases/correlation.py`; `ui-case-alert-link` POST view). This is the
  analyst-driven complement to the deferred automatic bundling (§12.3-fu1 still open).
  **Addendum 2026-10-21:** the "Similar alerts" card's **matching basis was replaced** by **SSDEEP fuzzy
  similarity** (`ppdeep`) — see `PLAN-2026-10-20-alert-fuzzy-similarity.md` (verified 22/22 ACs; security
  APPROVE WITH NOTES). `payload_ssdeep` is computed at ingest; the card ranks by
  `alerts_fuzzy_similar_to_case`; the old exact-signal function was deleted. Follow-ups below
  (§12.12).
- [ ] **12.12 — Fuzzy-similarity follow-ups (security FIND-05/03/06/07/11 + one discovered bug), 2026-10-21.**
  From `docs/security/AUDIT-2026-10-20-alert-fuzzy-similarity.md` and Phase E verification:
  - [ ] **12.12-fu1 (High, by design — FIND-05).** Decision (A) excludes `title` from the hash, so two
    different events with similar payloads can score high; the 80 threshold is the only guard. Decide between
    an opt-in `ALERT_FUZZY_INCLUDE_TITLE` setting and a require-both **title-fingerprint** pass. **Document
    the trade-off in the deployment runbook** regardless of which is chosen. (This is a design decision, not
    a defect — needs a product call.)
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
- [x] **12.13 — Similar tab was empty until a manual refresh; now lazy-loads on click + combined-count
  badge. DONE 2026-10-21.** `docs/planning/PLAN-2026-10-21-similar-tab-lazy-badge.md` (21/21 ACs MET;
  `docs/verification/VERIFY-2026-10-21-similar-tab-lazy-badge.md`). Root cause: `tabs.js` intercepted the
  tab click and only toggled `hidden` (no fetch) while the server computed the similar data **only** when
  `active_tab == "similar"`. Fix: fragment endpoint `ui-case-similar` + `_similar_for_case` helper +
  `tabs.js` one-shot lazy fetch; the **Similar** tab label now shows a `.count` badge =
  `similar cases + similar alerts` (only when > 0). No schema/API/CSS change; undefined-class backlog 42.
  Known minor edge: `tabs.js` collapse-`return` can skip `pushState` on a rapid second activation
  (visual state correct) — plan §14.6, low impact.
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
- [x] **12.3-fu2 — §12.3 left a cross-tenant test behind: `test_ui_alert_detail_merge_candidates_scoped`
  was broken on `main` and nothing noticed.** The view repointed `merge_candidates` from `Case` to
  `SimilarCase` (`cases/correlation.py`), but the older test in `tests/conformance/test_tenant_scoping.py` still
  read `{c.id for c in candidates}` → `AttributeError`. Net effect: for that whole period the alert-detail
  merge-candidate context had **no functioning org-scoping test** (the real invariant still held — the view +
  `alert_detail.html` use `SimilarCase` consistently and `candidate_cases_for_alert` filters via
  `case_visible_q`; it was the guard that was dead, not the behaviour). **FIXED 2026-10-19** — assertions read
  through `.case`, intent unchanged. Why it survived is the actual finding: **§12.11**.
- [ ] **12.4 — No bulk actions in the UI.** `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk`
  (PATCH) exist in the API with per-item isolation + status reporting (`compat/bulk.py`,
  `test_t2_p4_surface.py`), but the UI stub was deleted under Q2 (P1) and nothing replaced it — triaging a
  200-alert morning is single-item work. Fix: selection-based bulk triage (status / assignee / dismiss) that
  calls the same service, keyboard-reachable, `<dialog>` confirmation (ADR-003: no native `confirm()`), and a
  per-item result summary so partial failures are visible. Source: audit 2026-10-12.
- [x] **  12.5 — Case pages are renderable but not authorable in the UI.** **Code shipped 2026-10-14**
  (`PLAN-2026-10-14-case-pages-ui.md`, commit `ecbcef6`): `ui-case-page-create/-edit/-delete` (all `@not_viewer`
  `@require_POST`), a `can_mutate`-gated Pages card with modal create/edit/delete, Markdown rendering, and
  `live.js` re-fetching `#case-pages` on a `page` frame. **Verified 2026-10-17, 14/14 ACs + 7 supporting
  checks — **superseded 2026-10-18 by the independent re-run below**** (`docs/verification/VERIFY-2026-10-17-case-pages-ui.md`): all five documented
  shared-layer defects (F11–F15) confirmed present in current code, and a 20-check behavioural harness
  (outside the repo) passed with two discriminating mutations. The report is **planner-authored inline**
  because the `verifier` subagent returned 503 — it is **not independent**, so the `[~]` stays until an
  independent re-run. One **new, low-severity finding (V1)**: `_modal.html` gives the dialog heading
  `id="{{ modal_id }}-title"` and `_page_form.html` gives the Title input the same id, so the `<label for>`
  resolves to the non-labelable `<h2>` and the input loses its accessible name (the same collision already
  exists in `_task_form_modal_body.html`, so it is a replicated pre-existing smell, not introduced here).
  Two gaps recorded in the plan (§11 `W104-5`, `W104-8`): a page edit/delete writes **no `AuditLog`** row
  (invisible in the case Audit tab, like tags/tasks), and a **delete publishes no WebSocket frame** (API
  parity — another analyst sees it only on their next load). Also: cross-tenant 404s for the three UI
  routes are enforced by the shared `resolve_case(write=True)` guard but **not enumerated** by any
  committed test (the write-route sweeps filter to API-only) — observation O1. **Post-merge note:** §12.8
  (merged as `1f718e5`) replaced the standalone Tags card with an inline `.case-tags` row, so AC 2.1's
  *Tags* parenthetical is superseded (Pages is unaffected); the report carries a dated post-merge note.
  Source: audit 2026-10-12;
  plan 2026-10-14; verification 2026-10-17.
  **CLOSED 2026-10-18** — independent verification re-run discharged:
  [`VERIFY-2026-10-18-case-pages-ui-independent.md`](./docs/verification/VERIFY-2026-10-18-case-pages-ui-independent.md)
  is a genuinely independent supersede of the planner-authored 2026-10-17 report and grades **23/23 checks Met**
  (14 plan ACs + 3 data-model conventions + 6 security), **0 Not Met, 0 Deviated**; the five shared-layer defects
  **F11–F15 are confirmed fixed** at HEAD; `ruff format --check` / `ruff check` / `mypy` / `manage.py check` were
  green first-hand. Residual items are follow-ups, not AC failures — see §12.5-fu1 (V1 a11y) and §12.5-fu2 (O1).
- [x] **12.5-fu1 — Modal heading and its Title input share one DOM `id`, so the input loses its accessible name
  (shared layer; pre-existing).** `ui/templates/ui/_modal.html:73` gives every dialog heading
  `id="{{ modal_id }}-title"` and points `aria-labelledby` at it, but the modal **body** templates give the Title
  *input* the same id (`_page_form.html:20-21`; `_task_form_modal_body.html:23-33`). One `id` cannot identify two
  elements, so `<label for="{{ modal_id }}-title">` resolves to the heading — a **non-labelable** element: the input
  loses its programmatic accessible name and clicking the visible "Title" label no longer focuses it (WCAG 3.3.2 /
  4.1.2). Confirmed by the independent §12.5 re-run (finding **V1**) on the page create/edit modals and on the
  pre-existing task modals — **replicated, not introduced**, by §12.5 and §12.8. Fix: suffix modal-body field ids
  (`{{ modal_id }}-title-field`) in `_page_form.html` **and** `_task_form_modal_body.html`, then add a guard test
  asserting one element per `id` per rendered modal. Source: independent verification 2026-10-18.
  **FIXED 2026-10-19.** The Title *input* ids became `{{ modal_id }}-title-field` in both
  `_page_form.html` and `_task_form_modal_body.html` (whose two Title inputs are mutually exclusive
  `{% if form_values %}` / `{% else %}` branches, so only one ever renders — the heading keeps
  `{{ modal_id }}-title` as `aria-labelledby`'s target, and the error paragraph moved to
  `…-title-field-error` so it still describes the field it belongs to). Checked first: nothing else
  depended on the old ids (`modal.js` drives every dialog from `data-modal-*` attributes, not ids).
  Guarded by `test_modal_ids_are_unique_and_accessible`, which asserts exactly one element per id across
  the page-create/edit **and** task-create/edit modals and that the heading is an `<h2>` while the
  labelled element is the `<input>`.
- [x] **12.5-fu2 — Enumerate cross-tenant 404 for the three page write routes (observation O1).** The three
  `ui-case-page-create|-edit|-delete` routes are correctly gated by the shared `resolve_case(..., write=True)` guard
  (harness-verified for a foreign case *and* a foreign page id), but **no committed test enumerates it**:
  `tests/conformance/test_tenant_scoping.py`'s three write-route sweeps (`:1856`, `:1940`, `:2052`) all filter to API
  routes (`not name.startswith("ui-")`), and the three names appear only in `_UI_WRITE_ONLY_ROUTES` (`:880-882`),
  which merely excludes them from the *read* sweep. Add per-route UI cross-tenant tests, as that file already does
  for other routes. Source: independent verification 2026-10-18.
  **DONE 2026-10-19.** Eight tests added to `TestUITenantScoping` in `tests/conformance/test_tenant_scoping.py`
  (the only file touched): per route, a foreign case *and* — for `edit`/`delete` — a foreign page id embedded in
  the caller's **own** case URL, plus a **read-only-share** twin of each. The share twins are the load-bearing
  ones: a plain foreign case still returns `None` even at `write=False`, so only the share path distinguishes
  `write=True` from `write=False` — i.e. only it pins the guard the TODO was actually about. Each test asserts
  the exact **404** (a 403/redirect would leak existence), that the target row is untouched, and that no
  `AuditLog` row is claimed. Non-vacuity was demonstrated by runtime monkeypatching (a throwaway plugin in
  `/tmp`, no app code edited, plugin deleted): demoting `write=True`→`False` failed exactly the 3 share tests,
  removing tenant resolution failed the 3 foreign-case + 3 share tests, and dropping `case=case` from the page
  lookup failed exactly the 2 foreign-page tests.
- [x] **12.6 — Taxonomy: three cards break the monospace convention for their values — FIXED 2026-10-17.** `ui/templates/ui/taxonomy.html`
  renders the four code-valued cards — **Case Scope**, **Case Phase**, **Case Type** and **Data
  Classification** — with `<td class="mono">` for the code column, but the three list cards rendered
  theirs plain: **Case Statuses** ("Value"), **Alert Statuses** ("Value") and **Observable Types**
  ("Name"). All seven cards now use `class="mono"` on the code-valued cell (display-only; no
  behaviour, model or API change). Regression guard:
  `tests/conformance/test_ui_p2_wiring.py::test_taxonomy_page_renders_code_valued_cells_in_mono`
  (asserts the seeded status values and observable-type names render with the mono class).
  Source: user request 2026-10-17.
- [x] **12.7 — Cases list: make the case title clickable.** In `ui/templates/ui/case_list.html` (the item originally cited `ui/cases.html`), the table row for
  each case currently links the **case number** to the Case Details view (`ui-case-detail` / `case_id`).
  Make the **case title** (the human-readable title column) a link to the same destination, reusing the
  same URL and link text logic, preserving truncation and an accessible link name (e.g. "Open case
  <number> <title>" if present). Source: user request 2026-10-17. (Done 2026-10-17)
- [x] **12.8 — Case Details (General): inline edits, Tags, Verdict, and enforced close flow — CLOSED 2026-10-19** (code shipped 2026-10-17; independently verified 18/18 ACs; security pass CONDITIONAL PASS 2026-10-18; hardening conditions §12.8-fu4 landed 2026-10-19).**
  (1) **Inline metadata edits** on the General tab (`ui-case-detail`): add edit icons next to **Severity**,
      **Scope**, **Type**, **Classification** and **Phase**; each opens a dedicated `<dialog>` modal populated
      with allowed values from Taxonomy (no free text). Mutations must be org-scoped and respect role rules
      (`@not_viewer`, `require_POST`), emit `AuditLog` entries and relevant WebSocket updates. 
  (2) **Tags row** above the description: display assigned tags (with removal if applicable) and an **Add Tag**
      button opening a `<dialog>` to select from existing tags to assign to the case (create-on-assign only if
      already supported; otherwise select from existing). 
  (3) **Verdict model + taxonomy**: add `verdict` to **Case** with choices `Undefined`, `True Positive`,
      `False Positive` and default `Undefined`; expose **Verdict** as a card on the **Taxonomy** page. Add
      `verdict` to **Alert** (same choices/default) so linked alerts can carry the case’s verdict. 
  (4) **Close requires Verdict + comment**: when setting a case status to `Closed`, intercept the transition
      in the UI and show a `<dialog>` modal requiring the **Case Verdict** (dropdown, non-`Undefined`) and a
      mandatory **closing comment** (Markdown allowed, stored in case timeline/ledger or as a closing note per
      existing conventions). Block closure until valid. 
  (5) **Close propagates to linked alerts**: on a successful case closure, close all linked **Alerts** and set
      each alert’s `verdict` to the case’s `verdict`; record these actions in `AuditLog` and emit events so
      the UI updates. Use `<dialog>` for all modals (no native `confirm/alert/prompt`), preserve keyboard
      accessibility and org scoping. Source: user request 2026-10-17.
  **Code shipped 2026-10-17; independently re-verified at 18/18 ACs (§12.8-fu1); security pass returned
  CONDITIONAL PASS on 2026-10-18 (§12.8-fu2); the §12.8-fu4 hardening conditions landed 2026-10-19 — so this
  closed `[x]` on 2026-10-19 and `COMPLETED.md` may now list it.**
  Report: `docs/verification/VERIFY-2026-10-17-case-verdict-close-flow.md`. Per
  `docs/planning/PLAN-2026-10-17-case-verdict-close-flow.md`: `Case.verdict` + `Alert.verdict`
  (snake_case tokens `undefined`/`true_positive`/`false_positive`, migration `cases/0018`, `alerts/0015`) with DB
  `in_values` constraints; a seeded `Closed` `AlertStatus` (`alerts/0016`); the close rule enforced once in
  `cases/update.py::enforce_close_rule` (called from `apply_case_update` **and** `case_bulk_update`) with
  close-propagation to linked alerts (`propagate_close_to_alerts`) and the `closed_date`/`end_date` and
  `AuditLog.target_type` bugs fixed; Verdict on the Taxonomy page; General-tab edit icons, Tags row and
  `<dialog>` close/field/tag modals (`ui-case-close`, `ui-case-field-edit`, `ui-case-tag-add`,
  `ui-case-tag-remove`). Deviations recorded: **V-1** (token spelling), **V-2** (alert verdict + `Closed`
  status), **V-3** (UI timeline kind is now `status-changed`). Tests:
  `tests/conformance/test_case_verdict_close.py`, `test_ui_case_verdict.py` (27) plus updated
  `test_ui_loop.py` / `test_realtime.py` / `test_viewer_readonly_ui.py` / `test_tenant_scoping.py`.
- [x] **12.8-fu1 — Independent verification re-run of §12.8 — DISCHARGED 2026-10-17.** Re-run at `79793f5`
  by the `qa-tester` subagent standing in for the `verifier` (still 503 then, so the substitution is
  recorded in the report's provenance): **18/18 ACs Met**, 0 Not Met, 0 Deviated; report re-issued in
  `554bb0d`. The re-run added one low deviation (**D-N1** — a refused close flashes + redirects but does
  not re-open the dialog, contrary to plan §9.3, tracked under §12.8-fu3 below), corrected two citation
  drifts in the superseded report, and **withdrew** its non-reproducible "pre-existing failure" claim
  (old O5). **§12.8 now awaits only the §12.8-fu4 conditions before it can flip to `[x]`.**
- [x] **12.8-fu3 — Deviation D-N1 RESOLVED 2026-10-18: amended, not implemented.** Plan §9.3 promised the close
  dialog be "re-rendered open" on refusal; the shipped behaviour is `messages.error` + redirect — the same
  affordance every other inline-edit failure uses. Decision (2026-10-18): **amend the plan prose to match what
  shipped** (the independent re-run and the security pass both recommended this, for consistency), rather than
  re-opening only the close dialog. No code change; no test asserted the re-open. Plan §9.3 and its §15 deviation
  list now read *amended*, so D-N1 is closed. Source: independent re-run 2026-10-17; decision 2026-10-18.
- [x] **12.8-fu2 — Security review of the §12.8 surface — DISCHARGED 2026-10-18 (verdict CONDITIONAL PASS).**
  Report: [`SECURITY-2026-10-18-case-verdict-close-flow.md`](./docs/reviews/SECURITY-2026-10-18-case-verdict-close-flow.md).
  0 Critical / 0 High / 2 Medium / 2 Low / 2 Info. Live review of `2476edb` at HEAD `d596db6`: org scoping + uniform
  404 on all four routes, `@not_viewer`/`@require_POST`, CSRF, DB `in_values` verdict constraints, service-layer
  verdict whitelist, complete `AuditLog` coverage and clean `details` hygiene, `enforce_close_rule` reached from all
  three choke points — all PASS. Two sufficient negatives: the close rule is **not** reachable via `compat/`, and
  `not_viewer` logs `match.url_name` not `request.path` (CWE-117 clear). The three conditions are tracked in
  §12.8-fu4 below. Source: planned security review; user request 2026-10-17.
- [x] **12.8-fu4 — Clear the §12.8 security-review conditions — DONE 2026-10-19 (M1 and L2 were real; M2 re-graded Medium → Info).**
  This is the last blocker before §12.8 can flip to `[x]`:
  - **M1 — fix by simplification; do NOT add the auditor's "refuse the close" cap.** `cases/update.py:266-271`
    materialises **every** linked alert id into a Python list solely to build an `id__in=` UPDATE. Two defects:
    (i) an unbounded list, and (ii) an `IN (...)` clause that **fails outright past Postgres' 65 535
    bind-parameter limit** — so a case with more than 65k linked alerts could not be closed at all, which is a
    correctness bug, not just a perf one. Fix: `closed = Alert.objects.filter(case=case).update(status=..., verdict=verdict)`
    and use the returned rowcount in place of `len(alert_ids)` (it already serves the `if not …: return 0` check).
    One statement, no materialised ids, no `IN` limit. The proposed `MAX_PROPAGATE_ALERTS` refusal is **rejected**:
    it converts a scale problem into a permanently unclosable case and contradicts the house precedent
    (`MAX_BULK_PROCEDURES` / `MAX_CORRELATION_OBS` bound *intermediate* work, never the analyst's primary action).
    Lock duration across N rows is inherent to any single-statement approach. Keep the existing §12.8 conformance
    tests green.
  - **M2 — re-graded Medium → Info; accepted architectural boundary.** "The close rule is bypassable via direct ORM"
    is tautological for any service-layer invariant, and no user-reachable path does it: `enforce_close_rule` guards
    `apply_case_update`, `case_bulk_update` and `create_case`, and `compat/` exposes no case-close verb. The one
    narrow real fact is `Case.stamp_closed_date()` (`cases/models.py:209`), which stamps `closed_date` on any save
    that lands on a Closed-stage status — so a hypothetical direct write would persist a closed case with
    `verdict="undefined"` and no comment. Note the auditor's option 3 (a DB `CHECK` requiring a verdict when
    `stage='Closed'`) is **not implementable**: a `CHECK` cannot reference another table and `stage` lives on
    `CaseStatus`, and the suite runs on SQLite. Remedy: document the boundary in `docs/spec/` (data-model + api) and
    add a conformance test that **asserts the bypass exists**, so the next reviewer does not re-raise it as a defect.
  - **L2 — real; fix by reusing the constant already in the tree.** `ui/views.py:1095` builds `available_tags` with no
    cap and `_case_tag_add_form.html` renders every tag as a checkbox. `Tag` is global by design (so there is no
    cross-org leak), but the list grows with every tag ever created. Apply `ui/pagination.py::PICKER_PAGE_SIZE = 200`
    (introduced by §12.3) with a "showing first N" note rather than inventing a second limit.
  - **L1** is the D-N1 decision and is tracked separately in §12.8-fu3.
  Source: security review 2026-10-18.
  **DONE 2026-10-19.** All three conditions are cleared. **M1** is now one statement —
  `Alert.objects.filter(case=case).update(status=closed_status, verdict=verdict)` — with the returned rowcount
  driving the timeline event, the `case.alerts_closed` audit row and the return value, so nothing is
  materialised into Python and there is no `IN (...)` ceiling. **L2** caps the Add-tag picker at the existing
  `PICKER_PAGE_SIZE`, fetching one row *past* the cap so truncation is detected without a second `COUNT` on
  the hot case-detail path (and without evaluating the queryset twice). **M2** is documented in
  `docs/spec/data-model.md` + `docs/spec/api.md` and pinned by two bypass tests. Tests:
  `tests/conformance/test_case_verdict_close.py` (+3), `tests/conformance/test_ui_case_verdict.py` (+2).
  **Verification caveat — why this closed on a manual run.** The first independent verification of this wave,
  `docs/verification/VERIFY-2026-10-18-case-verdict-close-flow-fu4.md`, is **invalidated**: the five tests it
  cites as passing had been **deleted from the tree during that very run** (they survived only as stale
  bytecode), its evidence is internally inconsistent (it claims 52 passes *and* "24 + 7 = 31" for the same two
  files; the tree holds 49), and it left its own ad-hoc test file behind. The five tests were reconstructed
  from that bytecode plus the file conventions and run by the **user**:
  `pytest tests/conformance/test_case_verdict_close.py test_ui_case_verdict.py` → **54 passed** (2026-10-19),
  alongside a planner line-by-line review of the diff. An independent re-run was offered and the user elected
  to run the suite manually instead — so this wave's evidence is a manual run, **not** a fresh independent
  pass; re-verify it if a later review needs that. (The reconstruction also closed the two coverage gaps the
  invalidated report had itself identified: the L2 exact-cap boundary, and the `save()`-path bypass.)
  **Left open by decision:** no load test for `propagate_close_to_alerts` at large N — the single `UPDATE`
  still holds row locks for the transaction, which is inherent to the approach and was deliberately not
  capped.
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

- [x] **12.10 — `sev_class` emits CSS classes that do not exist, so severity renders unstyled wherever that
  filter is used.** `ui/templatetags/ui_tags.py::sev_class` maps `1`–`4` to
  `low`/`medium`/`high`/`critical`, i.e. it emits `sev-high` / `sev-critical` — but `ui/static/ui/app.css`
  defines only `.sev-1`…`.sev-4` and `.sev-unknown`. So a template using the filter
  (`ui/templates/ui/_tab_alerts.html:25`) renders severity **without its semantic colour**, while a template
  that interpolates raw (`sev-{{ severity }}`, e.g. `ui/templates/ui/observable_detail.html:201-202` and the
  new `_tab_similar.html`) works. Found 2026-10-18 while reviewing §12.3; **pre-existing**, unrelated to that
  work, and deliberately not fixed there. Fix one of two ways and add a regression test: (a) add
  `.sev-low`/`.sev-medium`/`.sev-high`/`.sev-critical` to `app.css`, or (b) make `sev_class` return the
  numeric suffix so both idioms agree. Either way severity must keep its text label — never colour alone
  (AGENTS.md §6). Source: §12.3 planner review 2026-10-18.
  **FIXED 2026-10-19 — option (b).** `sev_class` now returns the numeric suffix (`"1"`…`"4"`, else `"unknown"`)
  derived from the pinned severity range, so the filter and the raw-interpolation templates emit the same class
  and the shipped CSS needs no change — deliberately avoiding option (a), because `app.css` is compiled *in
  place* (`tailwindcss -i app.css -o app.css`) and is mid-drift (§7.4), so hand-adding selectors is the riskier
  of the two. Guarded by `test_css_defines_all_emitted_severity_classes` (fails if the filter and `app.css`
  drift apart again — the exact shape of the original bug), five `sev_class` unit tests, and the pre-existing
  severity test moved from `sev-high` to `sev-3` (it had been **pinning the bug**). Pending: pytest run
  (user-run).
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
  Current: **659 lines** combining playbook-config parsing with list/detail/create/edit/delete/run views.
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
- [x] **14.5 — Show and remove the alert's current tags.** The Tags row is now rendered in the Alert
  detail card immediately after Assignee (`ui/templates/ui/alert_detail.html`, `ui/views/alerts.py`).
  Tags appear as read-only neutral pills in alphabetical order; untagged alerts show `No tags yet.`
  (`tests/conformance/test_ui_alert_detail_tags.py`). Removing assigned tags is now supported through
  the UI for mutating users (`@not_viewer` gated `alert_tag_remove` view at
  `alerts/<uuid:alert_id>/tag/remove`). A viewer POST to the remove endpoint receives a 404 and
  the link remains unchanged. Audit trail and ledger/broadcast parity for tag removals is tracked
  separately under §14.6.
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
- [ ] **14.12 — Commit the `.gitignore` fix and purge editor swap files.** `*.swp` is currently only
  an uncommitted `.gitignore` change; a committed `.swp` blob remains in history. Commit the ignore
  rule and confirm no swap files are tracked.

---

## 15. Alert observables from source mappings (2026-11-06)

Plan: [`PLAN-2026-11-06-alert-observables.md`](docs/planning/PLAN-2026-11-06-alert-observables.md)
(APPROVED; decisions §14; 28 ACs). This is the P6 candidate from
`PLAN-2026-10-16-source-field-mapping.md`. **Do not commit unless explicitly requested.**

- [x] **15.1 — P1: pure schema + extraction.** `ingest/observable_rules.py` and
  `ingest.mapping.extract_all_by_jsonpath`; observable validation in `validate_config`. AC1–AC4.
- [x] **15.2 — P2: ingest persistence.** `observables/alert_sync.py::sync_alert_observables` called
  from `store_ingested_alert` for create and replay. AC5–AC9.
- [x] **15.3 — P3: remap.** Observables re-extracted in `remap_alerts`; `observables_added` in
  `RemapResult` and the flash message. AC10–AC12.
- [x] **15.4 — P4: alert detail tab.** Observables tab, card moved out of General, honest empty
  state. Two provenance UI assertions moved to `?tab=observables`. AC13–AC16.
- [x] **15.5 — P5: case copy.** Copy on merge/import, reconcile proposed suggestions, dispatch once
  per new link, timeline counts. AC17–AC21.
- [x] **15.6 — P6: source editor.** Observable-rules card and save path preserving unrelated config;
  audit/no-op/error handling. AC22–AC25.
- [ ] **15.7 — Deferred API parity (Q6).** A future `GET /api/v1/alert/{alertId}/observable` read
  endpoint is intentionally omitted until a client needs it.
- [ ] **15.8 — Deferred provenance decision (Q7).** Explain and decide the trade-off for a per-link
  `AlertObservable.source_path` column before a migration-backed provenance wave.
- [x] **15.9 — P7: verification.** 56 focused tests + relevant suites + `make check` green; independent
  verifier passes AC1–AC28 (`docs/verification/VERIFY-2026-11-06-alert-observables.md`); security
  review PASS with one pre-existing Medium residual risk
  (`docs/reviews/SECURITY-2026-11-06-alert-observables.md`). AC26–AC28.
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
**Do not commit unless explicitly requested.**

- [x] **16.1 — View.** `settings_source_detail` reads `?tab=` (`mapping` default; invalid → `mapping`),
  computes `latest_alert` (`-date, -created_at` for the source) and pretty-printed `latest_raw_payload`;
  `_source_detail_context` forwards `active_tab`, `latest_alert`, `latest_raw_payload`.
- [x] **16.2 — Template.** Reuses `.tabs`/`.tab`/`.tab-on` under the messages block; Mapping content
  wrapped in `{% if active_tab == "mapping" %}`, Raw data card with identity `<dl>` and auto-escaped
  `<pre class="json">`, plus "No raw data yet." empty state (no alert link, per §4).
- [x] **16.3 — Tests.** `tests/conformance/test_source_detail_raw_data.py` (7): default tab, latest
  payload/identity, latest-wins ordering, auto-escaped payload, empty state, invalid-tab fallback,
  analyst 404. `test_settings_source_mapping.py` (53) still green — 60 total.
- [x] **16.4 — Gates.** `make check` green.
- [x] **16.5 — Independent verification.** PASS WITH NOTES, 8/8 ACs met
  (`docs/verification/VERIFY-2026-11-06-source-detail-raw-data.md`).
- [ ] **16.6 — Decide the empty-payload edge.** An alert whose `raw_payload` is the default `{}` renders
  `{}` in `<pre>` rather than the "No raw data yet." empty state (the plan never specified this case).
  Low priority; either show `{}` intentionally or treat a falsy payload as empty. Verifier observation (a).

---

## 17. Raw-data click-to-map (IMPLEMENTED, 2026-11-07)

Status: **IMPLEMENTED & VERIFIED (2026-11-07).** Verifier: PASS WITH NOTES, 10/10 ACs
(`docs/verification/VERIFY-2026-11-07-raw-click-map.md`); security: PASS WITH NOTES, no Critical/High
(`docs/security/SECURITY-2026-11-07-raw-click-map.md` — M1 GET-without-CSRF and M2 key-names-in-URL are
accepted). Decisions in §17.6 are locked; §17.9 has files, deviations, and test evidence. Not committed
yet. A full `docs/planning/PLAN-<date>-raw-click-map.md` may be written when scheduled for release.

### 17.1 Goal
On the source detail **Raw data** tab, make JSON scalar values clickable. Clicking a value opens a modal
("Map this field to…") offering the seven general targets (Title, Description, Summary, Severity, Date,
Type, Source reference) and, last, **Observable extraction**. Choosing one adds that value's JSONPath to
the matching mapping row, which the operator then reviews and saves.

### 17.2 Why "append in place" does not work as-is
- CSP is `script-src 'self'`, no inline/nonce (ADR-003): interaction must live in an external
  `ui/static/ui/*.js` file; templates must not build HTML from payload data.
- The source tabs are **server-rendered and mutually exclusive**: on `?tab=raw-data` the Mapping form is
  not in the DOM (`{% if active_tab == "mapping" %}`), and SRC-RAW-2 asserts mapping content is absent
  there — so a click cannot append to a textarea that does not exist.
- A click that wrote the stored config directly would bypass the reviewed "Save mapping" flow.

### 17.3 Recommended model (prefill, then Save)
1. Render the raw payload server-side as a structured, pretty JSON tree; each scalar carries
   `data-json-path="<jsonpath>"`. Keep a collapsible "Raw JSON" `<pre>` for copy/paste and fallback.
2. External `json-map.js` enhances scalar nodes (`role=button`, `tabindex=0`, Enter/Space); on activation
   it fills a hidden `map_path` in a server-rendered `<dialog>` and opens it.
3. The dialog offers the seven targets as radios, plus "Observable extraction" (reveals a server-rendered
   type `<select>`), and submits a **GET** to the same page:
   `?tab=mapping&map_target=<t>&map_path=<p>[&map_observable_type=<type>]`.
4. The view validates the target and path (starts with `$`, `jsonpath-ng` parses it, ≤ `MAX_PATH_LEN`=200)
   and renders the Mapping tab with the path appended to that row's textarea (deduped, **not saved**) plus
   a hint/highlight: "Added from raw data — review, then Save mapping."
5. Nothing persists until the existing "Save mapping" / "Save observable rules" POST (CSRF unchanged).

This keeps one writer per form, no silent writes, no new endpoint, and preserves SRC-RAW-2.

### 17.4 Components
- `ui/json_tree.py` (new, pure): `jsonpath_for(parts)` (dot for identifier keys, `[n]` for indices,
  `["…"]` with `"`/`\` escaping for everything else; round-trip tested with `jsonpath-ng`); a bounded
  tree builder (`MAX_NODES`≈1000, `MAX_DEPTH`≈12, truncation flag).
- `ui/templatetags/ui_tags.py`: a `json_tree` inclusion tag rendering `_json_tree.html` recursively.
- `ui/templates/ui/source_detail.html`: raw panel uses the tree + `<details>` raw `<pre>`; include
  `_json_map_modal.html` on the raw tab; Mapping tab shows the prefill hint/highlight.
- `ui/templates/ui/_json_tree.html`, `_json_map_modal.html` (new partials).
- `ui/static/ui/json-map.js` (new, external, CSP-safe; DOM text/value only, no `innerHTML`).
- `ui/templates/ui/base.html`: add a `{% block scripts %}` before `</body>` so page-specific JS loads once.
- `ui/views/ingestion_sources.py`: parse/validate the GET prefill; append to `editor_rows` /
  `observable_rows`; pass `map_targets`, `observable_types`, prefill state.
- `ui/static/ui/app.src.css` (+ regenerate `app.css` via `make css`): `.json-tree`, `.json-key`,
  `.json-val`, hover/focus treatment, truncation note.

### 17.5 Security
- All output auto-escaped; no `|safe` from payload data; no `innerHTML`.
- `map_path`/`map_target`/`map_observable_type` validated against allow-lists and length caps; invalid
  input silently ignored (normal Mapping render). GET prefill is non-persistent; the only writer remains
  the CSRF-protected Save POST.
- Node/depth caps prevent a huge payload from bloating the page.
- Note: the prefill URL exposes payload **key names** (not values) in browser history/server logs. Keys
  only; accepted unless the user prefers a POST/session alternative.

### 17.6 Open questions (answered 2026-11-07)
1. **Interaction model:** prefill + navigate to Mapping (recommended) vs. true in-place edit (needs both
   panels in the DOM, breaks SRC-RAW-2) vs. direct save (new write endpoint). Which?
2. **Path order on an existing target:** append (lower priority; first match wins) or prepend (higher
   priority)? Recommend append.
3. **Observable type:** choose from existing `ObservableType`s via a dropdown (recommended); also
   auto-suggest a type from the value (IP/hash/mail)? Allow free-text types, or known types only?
4. **Arrays:** clicking an element gives a concrete `[n]`; also offer an "all items" `[*]` option?
5. **Clickable nodes:** scalars only (string/number/bool/null), not objects/arrays? Recommend scalars.
6. **Raw fallback:** keep the collapsible "Raw JSON" `<pre>` for copy/paste/accessibility? Recommend yes.
7. **Caps:** ~1,000 nodes / depth 12 with a truncation notice — acceptable?
8. **Prefill transport:** GET query (simple; key names in history/logs) vs. sessionStorage/HTMX POST?
9. **After add:** scroll/focus the target textarea and show an "unsaved" hint — confirm.
10. **Scope:** admin/superAdmin only (inherits the source-page gate) — confirm.

### 17.7 Phases and acceptance criteria
- **P1 — pure helpers.** `jsonpath_for` + bounded tree builder + tests. `RCM-1` paths round-trip through
  `jsonpath-ng` and select exactly that value (incl. dots, spaces, quotes, unicode, indices); `RCM-2`
  node/depth truncation is flagged.
- **P2 — tree UI.** Template tag + partials + CSS + raw fallback. `RCM-3` scalars expose valid
  `data-json-path`; raw `<pre>` preserved.
- **P3 — modal + JS.** Dialog, radios (7 targets + Observable extraction), type select, external JS.
  `RCM-4` activation opens the dialog with the path; `RCM-5` keyboard operable (focus trap, Esc,
  Enter/Space, labels, no color-only).
- **P4 — prefill view.** Validation + append/dedup + hint. `RCM-6` target/path/type accepted → Mapping
  textarea holds the path (unsaved); `RCM-7` invalid/missing → normal Mapping render, no error page;
  `RCM-8` stored config unchanged until Save.
- **P5 — verification.** Focused tests + `make check`; independent verifier + security review.
  `RCM-9` autoescape/XSS-safe; `RCM-10` no-JS raw data still readable, CSRF/writes unchanged.

### 17.8 Testing
- Pure: `jsonpath_for` round-trip matrix; tree cap/truncation; escaping.
- View: prefill general target, dedup, invalid target/path, observable type prefill, no persistence,
  raw tab still hides mapping (SRC-RAW-2), analyst 404, reflected-value escaping.
- Template: raw tree `data-json-path` present; modal lists all seven labels + Observable extraction.
- JS: no harness in-repo — add a template-presence conformance test and document manual keyboard checks.

**Answers (locked):** (1) prefill + navigate; (2) append; (3) dropdown of existing `ObservableType`
names only, no auto-suggest, no free text; (4) concrete `[n]` **and** an "all items" `[*]` option;
(5) scalars only; (6) keep the collapsible Raw JSON `<pre>`; (7) 1,000 nodes / depth 12 with a
truncation notice; (8) GET query; (9) scroll/focus the textarea and show the unsaved hint; (10) admin/
superAdmin only (inherits the source-page gate).

### 17.9 Implementation result (2026-11-07)
- **Backend:** `ui/json_tree.py` (pure `jsonpath_for`, `all_items_path`, `is_valid_jsonpath`,
  `build_json_tree`, `JsonNode`/`JsonTree`; caps `MAX_TREE_NODES=1000`, `MAX_TREE_DEPTH=12`,
  `MAX_VALUE_CHARS=200`); `json_tree` inclusion tag in `ui/templatetags/ui_tags.py` +
  `ui/templates/ui/_json_tree.html`; `ui/views/ingestion_sources.py` validates the GET prefill and
  appends (deduped) to the unsaved editor rows, exposing `latest_raw_data`, `map_targets`,
  `observable_types`, `prefilled`.
- **Frontend:** dedicated `ui/templates/ui/_json_map_modal.html` (GET, no CSRF); `ui/static/ui/json-map.js`
  (external, no `innerHTML`/`eval`; role/tabindex + Enter/Space; path choice; observable select toggle);
  `{% block scripts %}` in `base.html`; raw panel renders `{% json_tree latest_raw_data %}` with the
  `<details>` raw fallback; Mapping tab renders the prefill hint and `data-prefilled` marker; `.json-tree`
  styles in `app.src.css`, `app.css` regenerated (`make css`).
- **Deviations:** (a) `_json_map_modal.html` is a dedicated `<dialog>`, not `_modal.html`, because the
  shared footer form injects `{% csrf_token %}` and defaults to POST — a GET form must not put the token
  in the URL; (b) `is_valid_jsonpath` lives in `ui/json_tree.py` (as planned) and is imported by the view,
  not duplicated in `ingest/mapping.py`.
- **Tests:** `tests/conformance/test_raw_click_map.py` (helpers + view prefill/dedup/invalid/observable/
  escaping/404) and `test_raw_click_map_template.py` (tree paths incl. `[*]`, modal labels + observable
  options, external script/no inline script, payload escaping, prefill hint + `data-prefilled`). Focused
  run: **88 passed**; `make css-check` green; ruff clean. Two test bugs fixed during integration: a missing
  `ObservableType` fixture and a dedup assertion that counted the new prefill hint as a duplicate.
- **Verification:** independent verifier PASS WITH NOTES (10/10 ACs) and security PASS WITH NOTES
  (no Critical/High; M1/M2 accepted). **Still needed:** a manual browser pass (keyboard, focus trap,
  scroll-to-textarea) and a dark-theme contrast check, then a commit once approved.

---

## 18. Case JSON import (PLAN, 2026-11-07)

Status: **PLAN — no implementation.** Builds on the existing case export (`ui-case-export` →
`cases.views._case_export_document`) and the **New case** modal in `ui/templates/ui/case_list.html`.

### 18.1 Goal
Let an analyst create a case from a previously exported JSON file, from the case list's **New case**
flow, and make the export complete enough to round-trip a case (scalars, children, provenance).

### 18.2 Current export gaps (`CaseExport` v1)
`_case_export_document` emits `{_type, version, exportedAt, case: case_json(detail=True), alerts:
[id,title,source,sourceRef,severity,rawPayload], procedures}`. Missing or partial:
- **case:** tags (not in `case_json`), timeline (detail path caps at 50), tasks (title/status/assignee
  only; no group/flag/order/dates/description/mandatory/notes), comments, pages, shares, attachments,
  `sourceTemplate` name.
- **alerts:** five fields + raw payload only — no status/date/tlp/pap/flag/assignee/summary/verdict/
  observables/tags/custom fields/comments/procedures.
- `automationRuns` is exported (read-only) but import policy is undecided.

### 18.3 Export v2 schema (recommended)
Keep `_type: "CaseExport"`, bump to `version: 2`; include:
- `case`: all `Case` scalars; `ownerOrg` name; `assignee` login; `status` value; `sourceTemplate` name;
  `tags: [{name}]`; `customFields: [{name, value}]`; `ingestionWarnings`.
- `tasks: [{title, description, group, status, flag, assignee, order, dueDate, startedAt, endedAt,
  mandatory, notes: [{message, createdAt, createdBy}]}]`.
- `timeline: [all events]` (no 50 cap; bounded by the export cap), `comments`, `pages`, `procedures`.
- `shares: [{organisation, permissions}]`, `attachments: [{name, contentType, size, sha256, external}]`
  (metadata only — no blobs).
- `alerts: [full alert export docs]` (same shape as §19), `automationRuns` (export only).
- Never export secrets, tokens, CSRF material, or `payload_ssdeep` (recompute on import).

### 18.4 Import behaviour
- Entry: `GET/POST /ui/cases/import` → `ui-case-import`; multipart `file`; admin/superAdmin
  (`@not_viewer` + `can_mutate`); CSRF. The GET renders the standalone import page. **Route ordering:**
  register `cases/import` *before* `cases/<str:case_id>` (the `str` converter would swallow it).
- Parse limits: ≤ 5 MiB; top-level object; `_type == "CaseExport"`; supported `version`; required
  `case.title`; reject `NaN`/`Infinity`; cap children (e.g. 1k alerts / 5k timeline / 2k observables)
  and nesting depth.
- Create in **one `transaction.atomic`**: fresh `number`; `owner_org = actor.org`; resolve `assignee`
  by login in-org; resolve `status` by value with fallback; tags by name; custom fields by name;
  tasks/notes, timeline, comments, pages, procedures; links for alerts.
- Shares are **skipped by default** (cross-org grant is a privilege-escalation surface); attachments
  are metadata-only with a warning (the JSON has no bytes).
- Preserve dates where present; a closed case must carry `verdict` (the export does) and a closing
  timeline entry is synthesised if absent.
- Provenance: `ingestion_warnings["imported"] = {at, by, sourceFile, originalNumber, originalId}` +
  a "Case imported from JSON" timeline event.
- `sourceTemplate` is **provenance only** — do not re-apply its defaults; the file already carries
  concrete tasks/custom fields/tags (`CaseTemplate.tasks/custom_fields/tags` are JSON blueprints).
- Task notes mirror into `TimelineEvent(kind="task-note")`; skip exported timeline rows of that kind
  (or import notes without re-mirroring) so the ledger is not doubled.
- Any validation/conflict error → no partial import; re-render with field-level errors.

### 18.5 UI
- `_case_create_fields.html`: add a secondary "Import from JSON…" link that navigates to the standalone
  import page (no nested modal — keeps the modal's single footer form intact). Keep "New case" primary.
- `ui/import_case.html` (new, rendered by `GET ui-case-import`): file input, a short "what is imported
  / not imported" note (shares skipped, attachments metadata only), "Import case" submit.
- Success → redirect to the new case detail with a flash "Case #N imported from JSON".

### 18.6 Security
- Upload-only; sanitised display filename; no server path from user input; `|safe` never used.
- Size/child caps + guarded JSON parse; atomic; CSRF; admin/superAdmin only.
- Force owner org to the actor's org; shares off by default; no secret material in export
  (regression test).

### 18.7 Open questions (defaults noted; not yet approved)
1. Import target org: always the actor's org (recommended) vs a superAdmin org picker?
   **Default:** actor's org.
2. Tags: create missing tags vs skip unknown. **Default:** create missing on attach (matches
   `_apply_case_tags`, which `get_or_create`s by name).
3. Shares: skip by default vs superAdmin opt-in. **Default:** skip (privilege-escalation surface).
4. Attachments: metadata-only vs optional base64/zip payload. **Default:** metadata-only + warning.
5. `automationRuns`: skip on import vs import as read-only history. **Default:** skip.
6. v1 files: accept with warnings vs reject. **Default:** accept v1 with warnings.
7. Linked-alert conflict on `(org, source, type, sourceRef)`: atomic fail vs skip with a report.
   **Default:** atomic fail (no partial import).
8. Duplicate import: always create a new case vs warn when `originalId` was imported before.
   **Default:** always create a new case.

### 18.8 Phases + ACs
- **P1 — export v2.** `IMP-1` export includes every Case scalar + tags + full timeline/tasks(+notes)/
  comments/pages/procedures/custom fields/shares/attachment metadata + full alert docs; no secrets;
  `version: 2`.
- **P2 — import core.** `IMP-2` a v2 file creates a case with scalars/tags/custom fields + provenance;
  `IMP-3` children (tasks/notes, timeline, comments, pages, procedures) imported; `IMP-4` invalid/
  oversized/wrong-type/unsupported-version files are rejected atomically with field errors.
- **P3 — linked alerts.** `IMP-5` exported alerts are recreated and linked, with observables/tags/
  custom fields/comments/procedures; a duplicate `(source, type, sourceRef)` yields a clear atomic
  failure.
- **P4 — UI + security.** `IMP-6` New case modal exposes Import from JSON; success redirects to the
  case; `IMP-7` shares/attachments handling documented and safe; CSRF/auth/size caps tested.
- **P5 — verification.** Focused tests + independent verifier + security review.

### 18.9 Testing
- Export: field-completeness snapshot; no token/secret keys; links resolve.
- Import: export→import→export equality on the stable subset (ignore ids/timestamps); child counts;
  status/assignee/tag resolution; missing/extra fields; caps; malformed JSON; wrong `_type`/version;
  duplicate alert conflict; non-admin 404/403; CSRF.
- UI: modal link present; upload enctype; success redirect; error re-render.

---

## 19. Alert JSON export/import (PLAN, 2026-11-07)

Status: **PLAN — no implementation.** Naming note: the API already has `alert/<id>/import` meaning
"import this alert into a case" (merge), so the new UI routes are `ui-alert-export` and
`ui-alert-import-json` (`alerts/import`).

### 19.1 Goal
- Alert detail: **Export JSON** button in `{% block actions %}`, same style/location as case detail.
- Alert list: **Import JSON** button in the filter card, outer right (`btn btn-primary btn-end`), same
  placement as "New case", opening an upload modal.

### 19.2 Export schema (`AlertExport` v1)
`GET /ui/alerts/<uuid>/export` → attachment `alert-<shortid>-export.json`:
```
{"_type":"AlertExport","version":1,"exportedAt":…,
 "alert": alert_json(alert) + {"rawPayload": …},
 "observables":[…],"tags":[{"name":…}],"customFields":[{"name":…,"value":…}],
 "comments":[…],"procedures":[…]}
```
- `raw_payload` is the deliberate exception to "never inlined" (same recorded deviation as the case
  export). Do **not** export `payload_ssdeep` (recompute on import) or treat ids as authoritative.
- `Comment` has both `case` and `alert` FKs, so alert comments export/import 1:1. `Share`, `Attachment`
  and `TimelineEvent` are case-only and are correctly absent from an alert export.
- Audit superAdmin cross-org read, mirroring case export.

### 19.3 Import behaviour
- `POST /ui/alerts/import` → `ui-alert-import-json`; multipart `file`; same gate as alert mutation
  (admin/superAdmin; CSRF).
- Validate `_type == "AlertExport"`, `version`, size ≤ 5 MiB, required `title/type/source/sourceRef`;
  child/depth caps.
- Create a **new** alert in the actor's org: fresh UUID; preserve scalars/enums/date/raw_payload/
  verdict/correlation key; resolve `status` by value with fallback, `assignee` by login; recompute
  `payload_ssdeep`.
- Do **not** reuse the DRF `alerts.views._create_alert` as-is: it is API-only (`request.data`) and
  currently never persists `raw_payload`. Set `raw_payload`/`payload_ssdeep` explicitly, or extract a
  shared `alerts/create.py` service (preferred) mirroring `cases/create.py`.
- Import observables (`resolve_observable` + `AlertObservable`), tags by name, custom fields by name,
  comments, procedures.
- Conflict on the `(owner_org, source, type, source_ref)` unique constraint → atomic failure with
  "An alert with this source/type/sourceRef already exists" and a link to the existing alert.
- Provenance: `ingestion_warnings["imported"] = {at, by, sourceFile, originalId}`.
- The exported `caseId` is not portable: export `caseNumber` and import **unlinked** by default
  (optional `?case=<number>` link is an open question).
- Success → redirect to the new alert detail with a flash.

### 19.4 UI
- `alert_detail.html` `{% block actions %}`: `<a class="btn" href="{% url 'ui-alert-export' alert.id %}">
  Export JSON</a>`.
- `alert_list.html` filter card — inside the existing `<form method="get" class="inline-form filter-bar">`,
  exactly mirroring "New case" in `case_list.html`:
  `{% if can_mutate %}<a class="btn btn-primary btn-end" href="{% url 'ui-alert-import' %}"
  data-modal-open="import-alert-modal">Import JSON</a>{% endif %}`. Without JS the link opens the
  standalone `GET ui-alert-import` page (progressive enhancement); with JS `modal.js` opens the modal.
- Modal: reuse `_modal.html` (add optional `enctype="multipart/form-data"` support) with
  `_alert_import_fields.html` — `<input type="file" name="file" accept="application/json,.json"
  required>` + hint — posting to `ui-alert-import-json`.

### 19.5 Security
- Upload-only; size/child caps; atomic; no `|safe`; sanitised filename; CSRF; admin/superAdmin.
- Never trust exported ids/org/assignee outside the target org; recompute hashes; force `owner_org`.
- No secrets in export (regression test); XSS via payload/filename covered.

### 19.6 Open questions (defaults noted; not yet approved)
1. Preserve exported `status` vs force `New`. **Default:** preserve by value, fallback + warning.
2. Preserve the case link (export `caseNumber`, resolve if visible) vs import unlinked.
   **Default:** import unlinked.
3. Duplicate conflict: hard fail vs skip/overwrite. **Default:** hard fail (atomic).
4. Tag/custom-field/observable-type resolution. **Default:** create missing tags; skip unknown custom
   fields/types with a warning.
5. Import an alert directly into a case from the case detail. **Default:** defer.

### 19.7 Phases + ACs
- **P1 — export.** `AIMP-1` alert export includes scalars + raw payload + observables/tags/custom
  fields/comments/procedures; no secrets/internal hash; `Content-Disposition` filename.
- **P2 — import core.** `AIMP-2` a valid file creates a new alert in the actor's org with the right
  scalars and provenance; `AIMP-3` status/assignee/date/enums resolved safely; `AIMP-4` malformed/
  oversized/wrong-type/unsupported-version/duplicate rejected atomically with clear errors.
- **P3 — children.** `AIMP-5` observables/tags/custom fields/comments/procedures imported and deduped.
- **P4 — UI.** `AIMP-6` Export JSON on alert detail (same style/location); `AIMP-7` Import JSON
  outer-right in the alert filter card + upload modal; redirect/flash; CSRF/auth tested.
- **P5 — verification.** Focused tests + independent verifier + security review.

### 19.8 Testing
- Round-trip export→import→export on the stable subset; child counts; conflict; caps; malformed;
  wrong `_type`/version; non-admin; CSRF; XSS via payload/filename; no secrets in export; UI placement
  (`btn-end`, actions block).

---

## 20. Raw-data JSON tree on the source mapping detail page (PLAN, 2026-11-07)

Status: **IMPLEMENTED 2026-11-07 — verification pending.** Corrects a defect in the shipped §17
click-to-map tree.

### 20.1 Problem (confirmed)
`source_detail.html` (`?tab=raw-data`) already calls `{% json_tree latest_raw_data %}`, but
`ui/json_tree.py::_build()` appends **every** node to the top-level `nodes` list, always with
`key=""` and `children=()`. The template's recursive `node.children` branch therefore never runs.
Result: the tab renders a **flat list of clickable values**, with no keys and no object/array
structure — exactly the reported symptom. Paths, `[*]` variants, caps, and the click-to-map modal
are otherwise correct.

### 20.2 Goal
Render the real JSON structure (objects, arrays, keys, indices, expandable branches) and keep the
existing scalar-click-to-map behaviour, accessibility, and the `[*]` alternative.

### 20.3 Fix — builder (`ui/json_tree.py`)
- `_build()` returns a `JsonNode`; children are attached to the parent (`children=(...)`) instead of
  being appended to the root list. `JsonTree.nodes` remains the **top-level roots**.
- Carry the dict key / list index on the node (`key`; add `is_index: bool = False`,
  `count: int = 0` for containers). Root `key=""`.
- Keep `MAX_TREE_NODES=1000`, `MAX_TREE_DEPTH=12`, `MAX_VALUE_CHARS=200`, per-branch `truncated`,
  and `path`/`all_path` generation exactly as-is (`[*]` for array positions).
- Containers are **not** clickable (expand/collapse only); scalars keep `.json-val` +
  `data-json-path` / `data-json-path-all`.

### 20.4 Fix — template + CSS (`django-frontend`)
Rewrite `_json_tree.html` as valid, nested markup (no inline JS — CSP):
- `<ul class="json-tree" role="tree"><li class="json-node" role="treeitem">` per node.
- scalar: `<span class="json-key">key</span><span class="json-punct">:</span>
  <span class="json-val" data-json-path=… data-json-path-all=…>value</span>`.
- object/array: `<details class="json-branch" open><summary><span class="json-key">key</span>
  <span class="json-brace">{…}</span> <span class="json-count">N keys</span></summary>
  <ul class="json-tree children">…</ul></details>`; root shows a `$` label.
- array items render `[0]:`; truncation hint stays; `<details>` gives native keyboard collapse.
- CSS in `ui/static/ui/app.src.css`, regenerate `ui/static/ui/app.css` with `make css` (verify
  `make css-check`): indentation/guides, summary marker, dim keys/braces/counts, keep
  `.json-val-map` hover/focus.
- a11y: `role="tree"`/`treeitem`; JS keeps adding `role="button"`, `tabindex`, `aria-label` to values.

### 20.5 Decisions (locked 2026-11-07)
1. Branch state: **all expanded**; `<details>` per branch.
2. Root row: **show `$` root**.
3. Type hints: **counts** (`{…} 3 keys` / `[…] 2 items`).
4. Container click: **expand only** (not clickable).
5. Key display: **plain dim `key:`**; array `[0]:`.
6. Caps: **keep** 1000 nodes / 12 depth / 200 chars + truncation hint.

### 20.6 Implementation Brief (ready on approval)
- **django-backend** — `ui/json_tree.py`: nest children, carry key/index/count, preserve caps and
  paths; update `tests/conformance/test_raw_click_map.py` (nesting/keys/index/count + existing caps).
- **django-frontend** — `ui/templates/ui/_json_tree.html` (valid nested `<ul>/<li>/<details>` keep
  `.json-val[data-json-path]`), `ui/static/ui/app.src.css` + `make css`, update
  `tests/conformance/test_raw_click_map_template.py` (keys, branch nesting, leaf paths + `[*]`,
  containers not clickable, escaping, modal still opens).
- **Constraints:** no inline JS (`script-src 'self'`); Django autoescape (never `|safe` on
  keys/values); keep `ui/static/ui/json-map.js` contract unchanged; `.venv/bin/pytest` on the two
  conformance files; `make check-fast`; `make css-check`.
- **ACs:** `TREE-1` nested DOM with keys/indices and >1 level of children; `TREE-2` clicking a scalar
  still opens the map modal with the right path; `TREE-3` `data-json-path-all` (`[*]`) still offered;
  `TREE-4` node/depth/value caps still truncate with a hint; `TREE-5` keyboard/a11y works and no CSP
  violation; `TREE-6` hostile keys/values are escaped.
- **Note:** record the §17 flat-builder correction as a deviation in the plan/verification docs.

### 20.7 Implementation result (2026-11-07)
- `ui/json_tree.py`: `_build()` returns a `JsonNode`; children nest under the parent; `key` /
  `is_index` / `count` (real container size) / per-branch `truncated` carried; caps and
  `jsonpath_for`/`all_items_path` output unchanged. 6 new tests added.
- `ui/templates/ui/_json_tree.html`: valid `<ul role="tree|group"><li role="treeitem">` markup with
  `<details open>` branches, `$ {…} N keys` / `[0]:` display, container counts, truncation hints;
  scalars keep `.json-val[data-json-path]` (+ `data-json-path-all`) so `json-map.js` is untouched.
- `ui/static/ui/app.src.css` → `app.css`: list reset, indentation guide, summary/brace/count styling;
  `.json-val-map` hover/focus preserved.
- Tests: `tests/conformance/test_raw_click_map.py` **28 passed**; `test_raw_click_map_template.py`
  **7 passed** (nesting, keys/index, containers not clickable, counts, escaping); `test_ui_urls_smoke`
  **6 passed**; `make css-check` + `make check-fast` green.
- Deviation note: a frontend sub-agent reported a non-existent `TemplateSyntaxError` for the
  `<li>`/`data-json-path-all` markup and reverted the template. The valid markup compiles and the
  rendered response passes the template tests — the revert was discarded.
- Fixed a visible template comment: `_json_map_modal.html` used a **multi-line** `{# … #}`, which
  Django does not treat as a comment (only single-line `{# #}` is), so it rendered as text on the
  raw-data tab. Replaced with `{% comment %}…{% endcomment %}`; the modal test now asserts `{#` and
  the comment text never appear in the response.

---

## 🎯 Next Recommended Items

The canonical **NEXT HIGHEST-IMPACT ITEMS** table at the top of this file is the single source of truth.
**Refresh 2026-10-19 (b):** **§10.12 is closed** — the §10a–§10c ingestion-token wave got both the passes it
was missing: an independent re-verification (22/22 ACs) and its **first** security review (PASS, 0 Critical /
0 High). But its four follow-ups are **newly open**, and `§10.12-fu1` is a *real* Postgres-only race (Medium —
a concurrently created token can be cascade-deleted by the reap), so this is not a "nothing to see" closure.
**Refresh 2026-10-19:** §12.8-fu4 landed, so **§12.8 is closed** and the case-verdict/close-flow feature is
fully done; the two entries that topped the 2026-10-18 ranking are now history (§12.8-fu3 was resolved too).
Everything else holds. Ranked as of 2026-10-19:

1. **§11.2 (M3)** — playbook action secrets are global and rendered in cleartext to any cross-org analyst. The
   highest-severity *open* finding in the repo, and it needs a policy call (scope `Playbook` by org vs. stop
   storing plaintext secrets).

### Tests — new items (to be added by user review)

- [ ] **12.14 — Alert tag management tests (Phase F, still open).**
  P1 regression coverage added 2026-11-05: `tests/conformance/test_alert_tag_add_regressions.py` (5 tests).
  Broader Phase F coverage still open — alert-detail available/current tag filtering, the modal's 400/404
  paths, tenant scoping and idempotency. See §14.7 for the missing plan/doc reference and the wider
  test list; §14 records the remaining P2/P3 review findings.
2. **§6.16 P6** — global search over cases/alerts/observables. The biggest remaining *feature* gap; §12.1 made the
   lists truthful, so it no longer has to search a truncated corpus.
3. **§12.4 / §6.16 P7** — bulk triage, realtime breadth.
4. **Security, unchanged** — **S3** (per-account lockout on the API login; wire `identity/ratelimit.py` into
   `compat/views.py::api_login`, return **429** + `Retry-After`, derive the client IP from the proxy chain instead
   of the client-supplied `X-Forwarded-For`) and **S5** (attachment aggregate caps + extension-from-content-type).
   These are deployment-hardening, not analyst ergonomics.
5. **§2.6 / §2.7** — coverage back to ≥ 80%, and the 10 still-NOT-MET Phase-12/T2 ACs.
6. **§12.11 — CI is red on every run, and the full suite is never run locally.** §12.3-fu2 shows the
   consequence: a broken test can sit on `main` indefinitely. Two small fixes (the `secrets` job's 10-minute
   timeout — which means the full-history secret scan is *not running at all* — and either making the
   "…migrations, suite)" step run the suite or renaming it and `make check`'s help text), plus a Postgres pass
   over the conformance suite. Highest *leverage* in this list, because every other claim here is currently
   unbacked by CI.
7. **Small open follow-ups** — `§12.3-fu1` (auto-bundling, deferred by Q1b), `§10.12-fu1…fu4`, `§12.1-fu1`,
   `§12.8-fu1/2/3`, `§6.13/§6.16 P4` (audit-log viewer). Newly closed 2026-10-19: `§12.10`, `§12.5-fu1`,
   `§12.5-fu2`, `§12.3-fu2`.

**Phase 12 / T2 is not fully shipped**: independent verification (2026-10-10) found 3 ACs MET / 3 DEVIATED /
10 NOT MET (`VERIFY-2026-10-10-t2-and-phase12.md`). Wave A closed the three unambiguous bugs; the
tenant-isolation wave closed the scope-guard / `run_now` / SSRF / merge-widening security items (ADR-004).
The remaining AC gaps are **§2.7**, and S3/S5 are security row 12. Nothing is now genuinely *deferred* —
the last such item (**§6.4 — Tenant isolation**) shipped 2026-10-10.
