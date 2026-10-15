# TODO — Amalthea

Open work only. Completed items live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-10-14 (**case pages are authorable in the UI** — §12.5 code shipped
(`PLAN-2026-10-14-case-pages-ui.md`): `ui-case-page-create/-edit/-delete`, a `can_mutate`-gated Pages card,
modal create/edit/delete, Markdown-rendered content, `live.js` re-fetch; **verification pending**, and five
shared-layer defects found and fixed on the way through the modal-contract + tab stylesheet — see the plan
§11 and `docs/spec/deviations.md` W10.4). Earlier: **cases view fixes** — `GET /cases/` no longer 404s (new trailing-slash
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
surface, no search, every list silently capped at 200 with no pager, a decorative observables filter,
API-only case pages). Earlier: §6.16 P3 procedures/TTP UI + P5 D1/D3 shipped (P3 follow-ups §11.4, P5
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

| Priority | Item | Description | Impact on the analyst |
|----------|------|-------------|-----------------------|
| **1** | **§12.3 — No cross-case / correlation surface (Modules B + C)** | `correlation_key` is derived, indexed and queryable but **nothing consumes it**: no auto-bundling of correlated alerts into a Case, and no "cases sharing this observable" view. The observable page lists its linked cases, but nothing links *from* a case. Also settles §10.7 "Similar" + Q-D4. | **Highest** — the marquee differentiator ("instantly highlight an advanced persistent pattern") is invisible in the UI. |
| **2** | **§6.16 P6 — Global search (B3)** | No search surface anywhere in the UI; the only affordance is browsing lists, which §12.1/12.2 make lossy. Plan: `PLAN-2026-10-11-ui-completeness.md` B3/Q5 (cases + alerts + observables). | **Highest** — "have we seen this hash / IP / user before?" is the most asked triage question and has no answer. |
| **3** | **§12.1 — Lists are silently truncated at 200, with no pager** | Every list view slices `[:PAGE_SIZE]` (200); no `Paginator`/`?page=` exists in `ui/`. | **High** — on any real instance the analyst cannot reach last month's cases, and is not told rows were cut. |
| **4** | **§12.2 — The observables-list filter does nothing — SHIPPED 2026-10-15** | `observable_list` now reads `q`/`type` from `request.GET`, passes `observable_types` (the dropdown is no longer empty), stays org-scoped, and tolerates a malformed `type` id. The pager + honest empty state remain §12.1. | **Done** — the control no longer lies; the §12.1 truncation caveat still applies. |
| **5** | **§12.4 — No bulk actions in the UI** | `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk` (PATCH) exist in the API with per-item isolation; the UI stub was deleted (Q2) and not replaced. | **Medium-High** — a 200-alert morning is single-item work. |
| **6** | **§10.3–10.6 — Case scope / type / data-classification / phase — SHIPPED 2026-10-15** | `PLAN-2026-10-14-case-classification-fields.md`, commit `822d050`, verified 21/21 ACs. `data_classification` is admin-only and audited; `phase` is a separate field from `CaseStatus` (Q-D2). | **Done** — follow-up is §10.9 (status labels). |
| **7** | **§12.5 — Case pages: renderable, not authorable** | **Code shipped 2026-10-14, verification pending** (create/edit/delete modal on the General tab). | **Medium** — a documentation surface the analyst can see but never contribute to. |
| **8** | **§6.16 P7 — Per-org realtime breadth + presence** | Scoped HTMX adoption (D4/D5), broader live events, presence. | **Medium** — collaboration polish; the ledger already syncs on the case page. |
| **9** | **§10.1 — Role-scoped user management** | An `admin` manages own-org users (today `superAdmin`-only). | **Medium** — an ops/onboarding concern, not triage. |
| **10** | **§6.13 / §6.16 P4 — Settings: ingestion tokens + audit-log viewer** | Per-org webhook tokens (create/revoke, show-once) + read-only `AuditLog` viewer; also retires the stale webhook copy (§10.8). | **Medium** — self-serve ingestion; the audit viewer is the accountability half. |
| **11** | **Security: §11.2 (M3) + S3 + S5** | **M3** playbook secrets are global + cleartext to any authenticated user of any org. **S3** per-account lockout on the **API** login; stop trusting client `XFF`. **S5** attachment aggregate caps + extension-from-content-type. | **Medium (security)** — M3 blocks multi-tenant trust; S5's caps are the analyst-facing half (upload robustness). |
| **12** | **§2.6 + §2.7 (test hardening) + §7.1–7.4 + §8.5/§8.7 + §11.3/§11.4-L1/§11.5** | Coverage back ≥ 80% (79% since the tenant wave) + the Phase-12/T2 QA list; Tailwind source/artifact split; mutation coverage; timestamp wording; protocol-relative `href`; unaudited procedure attaches; Django admin outside the role model; `django.request` log forging. | **None** — engineering hygiene + accepted Low/Info follow-ups. |
| **13** | **Orphaned routes** | `ui-case-attachment-list` (+ its now-dead `ui/case_attachments.html`) and `ui-case-attachment-detail` (405) are reachable by URL only — the case page renders its own attachment table. `ui-case-export` / `ui-taxonomy` are now wired (P1), so this row supersedes the old claim. | **Low** — dead surface; delete or build an entry point. |


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

- [ ] **10.1 — Role-scoped user management.** §6.12 shipped Settings → Users but **superAdmin-only**
  (`ui/views.py::_require_superadmin_ui`). Extend it so an **`admin` manages every user of their own
  organisation** (list/create/edit/delete/deactivate, reset password, change role within the org) while
  `superAdmin` keeps cross-org reach. All the §6.12 guards must hold per-scope: uniform 404 for
  out-of-scope ids (never a 403 existence oracle), can't escalate to `superAdmin` or edit outside the org,
  last-superAdmin guard, audit every mutation. UI: same Settings pages, filtered by `request.user.org`.
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
- [ ] **10.7 — Case detail tabs.** Split the case page into tabs: **General, Alerts, Tasks, Observables,
  Attachments, Similar, Comments, Audit.** Server-rendered (§6/ADR-003: no inline JS; CSP `'self'`),
  keyboard-navigable, degradable to separate pages/sections without JS. Feeds: General = 10.3–10.6 +
  summary; Alerts = `case`↔`Alert` links; Tasks/Observables/Attachments/Comments = existing relations;
  **Similar** = the §2-Module-C cross-case observable linking — **now tracked as §12.3** (with the missing
  auto-bundle consumer; decide Q-D4 there, not here); **Audit** = the read-only `AuditLog` viewer
  (pull forward §6.13/P4).
- [ ] **10.8 — Ingestion-token surface + stale webhook copy (mostly absorbed by §6.16 P4).** The README
  Quickstart now documents the per-org token, but the rest of the surface is still thin: (a)
  `ui/templates/ui/sources_list.html` says *"POST `/api/v1/alerts/webhook/<slug>/` … become alerts"* with
  no mention of the required token — fold the token (and its show-once secret) into that page when **P4**
  builds Settings → Ingestion tokens (§6.13); (b) add a short **Ingestion tokens** section to
  `docs/spec/identity-auth.md` (create/verify/revoke, prefix lookup, Argon2, legacy plaintext fallback)
  and cross-reference it from the README. Mostly absorbed by §6.16 **P4** — recorded here so the stale
  copy is not forgotten if P4 slips.
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
- [ ] **11.2 — M3: playbook action secrets are global and rendered in cleartext.** `Playbook` has **no
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
- [ ] **11.3 — L1: protocol-relative `href` survives the sanitizer.** `[x](//evil.example)` renders as
  `<a href="//evil.example">` (`ui/markdown.py`; `_ALLOWED_URL_SCHEMES` only sees the inherited scheme).
  Not XSS — a same-scheme link to an arbitrary host (phishing aid). Accept as low, or post-filter `href`
  values whose `urlsplit()` yields an empty scheme **and** a `//` prefix.

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
they share the list-view plumbing and are cheap; **§12.3** needs a decision first (§10.7/Q-D4). §6.16 P6
(search) and P7 (realtime) stay in their own plan and should land after
§12.1/12.2 — searching a truncated list is a half-answer.

- [ ] **12.1 — Every UI list is silently truncated at `PAGE_SIZE = 200`, with no pager.** `alert_list`
  (`ui/views.py:353`), the alert-detail rail (`:382`), `case_list` (`:455`), the case-detail rail (`:491`),
  `automation_list` (`:650`), `observable_list` (`:1423`) and the Settings user list (`:1541`) all slice
  `[:PAGE_SIZE]`; there is **no** `Paginator` / `page_obj` / `?page=` anywhere in `ui/`. On a real instance
  an analyst cannot reach anything older than the newest 200 rows, and nothing on the page says rows were
  cut. Fix: server-rendered pagination (degrade to `?page=` links; HTMX optional), a visible
  "showing N of M" count, and an honest empty state that distinguishes *no rows* from *filtered to none* —
  note the API is *not* a workaround for the analyst: `GET /case` defaults to the same cap
  (`timelineLimit` default 50 / max 200 in `cases/views.py`). Source: audit 2026-10-12.
- [x] **12.2 — The observables-list filter is decorative — FIXED 2026-10-15.** `observable_list` now reads
  `request.GET`: `q` filters the artifact value (`data__icontains`) and `type` narrows to one `ObservableType`
  id, with `query` / `observable_types` / `type_filter` passed to the context so the dropdown is populated and
  the submitted type stays selected. The filter stays org-scoped (`org_q`) and a non-UUID `type` is dropped
  rather than 500-ing out of the UUID column. Regression tests in `tests/conformance/test_ui_p2_wiring.py`
  (value, type, malformed id, org scoping). **Not** closed by this: §12.1 — there is still no pager and the
  empty state still cannot tell "no rows" from "filtered to none", so filtering a truncated 200-row list
  remains a half-answer. Source: audit 2026-10-12.
- [ ] **12.3 — No cross-case / correlation surface (AGENTS.md §2 Modules B + C).** `Alert.correlation_key`
  is derived at ingest (`ingest/mapping.py::extract_correlation_key`), indexed (`(correlation_key, date)`)
  and exposed to the query engine — but **no code consumes it**: there is no automatic bundling of
  correlated alerts into a `Case` (Module B: "automatically bundled … identical destination IP within a
  10-minute window") and no "cases sharing this observable" view. The observable detail page *does* list its
  linked cases (`observable_detail.html:178`, "Linked Cases (N)"), so the pivot exists one hop away — but
  nothing links *from* a case to it and nothing ranks or heuristically scores the overlap. Decision first
  (§10.7 "Similar" tab semantics, **Q-D4**); then: an automated-bundle service that is **explicit and
  auditable** (silent auto-merges are unacceptable — every bundle must write a ledger entry + `AuditLog` row,
  per §9 of the plan and ADR-002), plus a case-side "related cases" panel reusing
  `case_observables__observable` (no new model). Source: audit 2026-10-12; AGENTS.md §2. **Q-D4 decided
  2026-10-14:** deferred — `PLAN-2026-10-14-case-classification-fields.md` §12 settled it, and the "Similar"
  tab remains the explicit "not yet implemented" placeholder in `ui/templates/ui/_tab_similar.html`. Nothing
  else depends on it; it is still unimplemented and unscheduled.
- [ ] **12.4 — No bulk actions in the UI.** `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk`
  (PATCH) exist in the API with per-item isolation + status reporting (`compat/bulk.py`,
  `test_t2_p4_surface.py`), but the UI stub was deleted under Q2 (P1) and nothing replaced it — triaging a
  200-alert morning is single-item work. Fix: selection-based bulk triage (status / assignee / dismiss) that
  calls the same service, keyboard-reachable, `<dialog>` confirmation (ADR-003: no native `confirm()`), and a
  per-item result summary so partial failures are visible. Source: audit 2026-10-12.
- [~] **12.5 — Case pages are renderable but not authorable in the UI.** **Code shipped 2026-10-14**
  (`PLAN-2026-10-14-case-pages-ui.md`): `ui-case-page-create/-edit/-delete` (all `@not_viewer`
  `@require_POST`), a `can_mutate`-gated Pages card with modal create/edit/delete, Markdown rendering, and
  `live.js` re-fetching `#case-pages` on a `page` frame. **Awaiting independent verification** — until that
  lands this stays open and `COMPLETED.md` must not list it. Two gaps recorded in the plan (§11 `W104-5`,
  `W104-8`): a page edit/delete writes **no `AuditLog`** row (invisible in the case Audit tab, like
  tags/tasks), and a **delete publishes no WebSocket frame** (API parity — another analyst sees it only on
  their next load). Source: audit 2026-10-12; plan 2026-10-14.

---

## 🎯 Next Recommended Items

The canonical **NEXT HIGHEST-IMPACT ITEMS** table at the top of this file is the single source of truth.
As of **2026-10-13** (**§10.2 manual case creation shipped**) the best next move **for the analyst** is
**§12.1 + §12.2** — a truncated list that never says it truncated, and a filter that never filters, are the
two cheapest fixes with the largest trust payoff; §12.3 (cross-case/correlation) and §6.16 P6 (global
search) are the two biggest *feature* gaps and both want a decision first. Ranked:

1. **§12.1 + §12.2** — pagination + a truthful observables filter (small, shared list-view work).
2. **§12.3** — decide the "Similar"/Q-D4 semantics, then an auditable auto-bundle consumer + a case-side
   related-cases panel (the Module B/C differentiator, currently business-card-only).
3. **§6.16 P6** — global search over cases/alerts/observables (after §12.1/12.2 so it does not search a
   truncated corpus).
4. **§12.4 / §6.16 P7** — bulk triage, realtime breadth (§12.5 shipped 2026-10-14, verification pending).
5. **Security, unchanged** — **§11.2 (M3)** playbook secrets, then **S3** (per-account lockout on the API
   login; wire `identity/ratelimit.py` into `compat/views.py::api_login`, return **429** + `Retry-After`,
   derive the client IP from the proxy chain instead of the client-supplied `X-Forwarded-For`) and **S5**
   (attachment aggregate caps + extension-from-content-type). These are deployment-hardening, not analyst
   ergonomics.

**Phase 12 / T2 is not fully shipped**: independent verification (2026-10-10) found 3 ACs MET / 3 DEVIATED /
10 NOT MET (`VERIFY-2026-10-10-t2-and-phase12.md`). Wave A closed the three unambiguous bugs; the
tenant-isolation wave closed the scope-guard / `run_now` / SSRF / merge-widening security items (ADR-004).
The remaining AC gaps are **§2.7**, and S3/S5 are security row 12. Nothing is now genuinely *deferred* —
the last such item (**§6.4 — Tenant isolation**) shipped 2026-10-10.
