# PLAN — UI completeness wave (2026-10-11)

**Source:** the UI gap table produced after Wave 6.3B (§6.15); user decision **"everything"** (2026-10-11).
**Scope owner:** planner. **Implementers:** `django-frontend` (templates/HTMX/JS), `django-backend` (views/services), `db-postgres` (only if an index is measured as needed). **Verifiers:** `verifier`, `security-auditor`, `qa-tester`.
**Parent specs:** `AGENTS.md` §2 (modules), §6 (design & style); `docs/decisions/ADR-003` (frontend assets/CSP), ADR-004 (tenant isolation).
**TODO anchor:** new section **§6.16** (created by this plan).

---

## 1. Summary

Twenty-four verified gaps from the post-§6.15 UI audit. They fall into four classes:

1. **Built but unreachable** — attachments, case merge, export, apply-template, taxonomy, procedures (feature works, no entry point).
2. **Missing navigation** — Observables (the headline differentiator), Tags, Case templates, Taxonomy have no rail entry; no search; no tenant indicator.
3. **Missing flows** — alert triage, case edit, observable edit/delete, case-template edit, ingestion-token management, audit viewer, self-service password.
4. **Broken / degraded** — playbook edit drops its action config, no `500`/`403` pages, Markdown never rendered, realtime only on case detail, HTMX in one template, cheat-sheet built via `innerHTML`.

The wave is delivered in **seven phases**, ordered so that every phase is independently shippable and verifiable. P1–P2 restore the analyst loop; P3–P5 complete the entity surface; P6–P7 are the two genuine mini-projects (search, realtime/HTMX) and are the ones most likely to slip.

**Guiding constraint:** everything ships under the shipped CSP (`script-src 'self'`, `style-src 'self'`, no `unsafe-inline`). No phase may reintroduce an inline `<script>`, `style=`, or `on*=` — `tests/conformance/test_ui_inline_assets.py` is the enforcement.

## 2. Goals

- **G1.** Every shipped feature is reachable from the rail or a primary screen — no URL-typing, no orphan routes.
- **G2.** The MVP triage loop (ingest → triage alert → escalate → case work → close) is fully operable mouse-free **and** without JS.
- **G3.** Every entity with a detail page has an edit path (case, observable, case template, playbook) — or an explicit, documented reason it does not.
- **G4.** Every page is reachable, and every page is theme-consistent, including error pages.
- **G5.** CSP holds repo-wide; no new inline asset.

## 3. Non-goals

- **N1.** Re-designing the visual language; this is completion, not a facelift.
- **N2.** New backend *domains* (case scoring, SLA timers, saved views DB, reporting).
- **N3.** Postgres RLS, DB-level enforcement, or a second realtime transport (the WS + HTTP fallback stays).
- **N4.** TheHive `compat/` parity for any new UI action (decision Q4, reaffirmed).
- **N5.** S3/S5 security items and coverage ≥80 % (§2.6) — tracked separately; not this wave.

## 4. Architecture (no change; restatement + rationale)

The stack is fixed by ADR-001/003 and is deliberately *not* revisited here: Django templates + HTMX + Tailwind + Channels, dark-only, CSP-strict, progressive enhancement (every HTMX action must also work as a plain form POST).

- **Where new behaviour goes.** Server-rendered views in `ui/views.py` (or the owning app's `services.py` for mutations) → template partial in `ui/templates/ui/` → optional thin JS in `ui/static/ui/`. Business rules do **not** live in JS.
- **Scoping.** Every new view resolves its target through `identity/scoping.py` (`resolve_case` / `resolve_alert` / `resolve_observable` / `resolve_organisation`) and renders the **uniform 404** on miss — never 403. This is the single mechanism; no view may hand-roll an org filter.
- **Modals.** Destructive/confirm actions use the existing `<dialog>` pattern (`_modal.html` + `modal.js` + `modal.css`), which is already the prescribed pattern in `AGENTS.md` §6. The `?` cheat-sheet is converted to that same pattern (D6) rather than its own `innerHTML` construction.
- **HTMX.** Mutations that can swap a partial should; the non-HTMX path is the same URL as a full POST. `HX-Request` decides partial vs full render (existing precedent: `case_comment`).
- **ADRs:** **ADR-005** (`markdown-it-py` + `nh3`, server-side sanitized CommonMark) is required by the Q1 decision and covers D3. ADR-003 gains a note that the cheat-sheet is server-rendered (§4 note) if D6 lands as designed.

## 5. Data model / schema

**No migrations are expected in P1–P6.** Explicitly:

| Item | Schema impact |
|---|---|
| Attachments, merge, export, apply-template, taxonomy, procedures | none — reuse existing rows/routes |
| Alert triage (C1), case edit (C2), observable edit/delete (C3) | none — existing columns; observable edit/delete must respect the §6.5 blast-radius guards |
| Case-template edit (C4) | none |
| Ingestion tokens (C5), audit viewer (C6) | none — `IngestionToken` / `AuditLog` already exist |
| Playbook round-trip (D1), Markdown (D3) | none (D3 is presentation; if it stores rendered HTML, **do not** — render on read) |
| Search (B3) | none required; **only if** `EXPLAIN` shows a seq scan on real data, add a `pg_trgm` GIN index in a separate migration (`db-postgres` owns that decision) |
| Realtime per-org channel (D4) | none — channel name derived from `org_id` |

The plan's AC includes `makemigrations --check --dry-run` staying green; if any phase *does* need a migration, it must say so in its outcome note.

## 6. API surface

No new **wire** endpoints are required — P2/P3 mutation views call the same services the API already uses (`_set_alert_fields`/`_update_alert`, observable update/delete path, `_apply_template`). The only additions are **UI routes** (named `ui-*`), and one new page view for search:

| New UI route | Purpose | Backed by |
|---|---|---|
| `ui-case-edit` | C2 | `Case` fields directly + audit |
| `ui-alert-status` | C1 | `_set_alert_fields` / `_update_alert` |
| `ui-observable-edit`, `ui-observable-delete` | C3 | observable update/delete service (blast-radius guards) |
| `ui-case-template-edit` | C4 | existing template form view |
| `ui-search` | B3 | new org-scoped query across cases/alerts/observables |
| `ui-settings-tokens`, `ui-settings-tokens-create`, `ui-settings-tokens-revoke` | C5 | `IngestionToken.create_token` / `revoked_at` |
| `ui-settings-audit` | C6 | `AuditLog` read-only |
| `ui-password-change` | C7 | Django `PasswordChangeView` |

All new routes are `ui-`-prefixed (per `ui/urls.py` docstring) and all non-GET routes are CSRF-protected POSTs.

## 7. UI work

### 7.1 Navigation / IA (B1, B2, B4, D8)

The rail grows from 8 to 12 entries; ordering by frequency, keyboard key in the keycap:

| Key | Entry | Notes |
|---|---|---|
| `g` | Dashboard | existing |
| `a` | Alerts | existing |
| `c` | Cases | existing |
| `o` | **Observables** | new (B1) — the differentiator gets a home |
| `p` | Playbooks | existing (keys: check collision with `p`=playbooks vs procedures — procedures live inside a case, no rail entry) |
| `r` | Automation runs | existing |
| `t` | **Tags** | new (B2) |
| `m` | **Case templates** | new (B2) (`m` for "templates/models"; `c`/`t` taken) |
| `x` | **Taxonomy** | new (B2) |
| `s` | Sources | existing |
| `,` | Settings | existing |
| — | tenant indicator (B4) | rail header/foot: `{{ org.name }}` for members, "All organisations" for superAdmin, linking to Settings for superAdmin |

- `keys.js` `ROUTES` + `CHEATSHEET` updated in lock-step; the cheat-sheet is **data-driven from one place** so a rail entry cannot exist without a shortcut (add a conformance test that the two lists agree).
- `aria-current="page"` on every new entry; each entry carries a Font Awesome icon **plus** its keycap (D7) — the icon supplements the visible label, never replaces it (`aria-hidden="true"`).

### 7.2 Case detail becomes the case workspace (A1, A2, A3, A5)

New "cards" on `case_detail.html`, each a self-contained section with a plain-POST fallback:

- **Observables** — list extracted observables with type/value/links (closes the loop with `case_extract`).
- **Attachments** (A1) — upload form + file list (size, uploader, download/delete). Reuses `case_attachment_upload/list/download/delete`; no new backend.
- **Actions** — Merge into case… (A2, `<dialog>` + target number), Export JSON (A3, GET link), Apply template… (A3, link to the existing page), Tags (link, existing).
- **Procedures / TTP** (A5) — attach an ATT&CK procedure/TTP to the case; reuses `case_procedure_create`/`case_procedures_create`, needs a new partial.
- **Edit case** (C2) — link to `ui-case-edit`.

### 7.2a Accessibility / style rules for all new UI

Severity/TLP/PAP always carry a text label (never colour alone, WCAG 1.4.1); `:focus-visible` preserved; every new modal traps focus and closes on Esc; icon-only controls carry `aria-label`; new component CSS goes into `ui/static/ui/app.css` as **component classes** (not Tailwind utilities — see §7.3 constraint).

### 7.3 Styling constraint (carried from §6.15)

`ui/static/ui/app.css` is its own Tailwind input, so **new utilities are never generated**. All new styles are plain component CSS appended to `app.css`, then `make css`. Removing this constraint is TODO §7.4 and is a prerequisite for any future "just use a Tailwind class" work — noted, not fixed here.

## 8. Phases

Each phase has its own AC checklist; a phase is "done" when `make check` is green **and** its ACs are verified (verifier for behavioural ACs, security-auditor for any new mutation/route, qa-tester for the regression net).

### P1 — Wire up what exists + navigation (A1–A4, B1, B2, B4, C4, C7, D2, D6, D7, D8)

- [ ] **A1** Case detail gains an Attachments section (upload form + list; download/delete) with no URL-typing; works with HTMX **and** as plain POST.
- [ ] **A2** Case detail gains "Merge into case…" (`<dialog>`, target number, CSRF) posting to `ui-case-merge`; result redirects to the surviving case.
- [ ] **A3a** Export JSON is a visible action on case detail (GET, downloads the file).
- [ ] **A3b** Apply-template is reachable from case detail (link to the existing page).
- [ ] **A3c** Bulk operations: the stub view is either **implemented** (multi-select on case list → status/assignee/tags) or **removed** — see Open Question **Q2**. No dead route survives P1.
- [ ] **A4** Taxonomy is reachable (rail entry `x` + a link from case-list filters).
- [ ] **B1** Rail has Observables (`o`); list + detail reachable and keyboard-navigable.
- [ ] **B2** Rail has Tags (`t`), Case templates (`m`), Taxonomy (`x`).
- [ ] **B4** Rail shows the tenant indicator (org name, or "All organisations" for superAdmin) linking to Settings for superAdmin.
- [ ] **C4** Case-template edit: `ui-case-template-edit` route + form pre-populated + link from detail; create still works.
- [ ] **C7** Self-service password change: `ui-password-change` (Django view + themed template), linked from the rail foot next to Sign out.
- [ ] **D2** `500.html` and `403.html` exist, extend the base layout, and are exercised by a test that forces each status.
- [ ] **D6** Cheat-sheet is a server-rendered `<dialog>` in `base.html` (shortcut table built from one server-side source), opened by `?` and by a visible affordance; `keys.js` no longer builds HTML (`innerHTML` removed from the cheat-sheet path).
- [ ] **D7** Font Awesome icons on rail entries and primary actions, `aria-hidden`, icon-only controls labelled.
- [ ] **D8** Keyboard shortcuts cover every rail entry; `keys.js` `ROUTES` ↔ cheat-sheet table cannot drift (conformance test).
- [ ] Guard: `test_ui_inline_assets.py` still green; `test_ui_table_markup.py` extended to any new tables.

### P2 — Triage and edit flows (C1, C2, C3)

- [ ] **C1** Alert detail gains a status control (New / Triaged / Dismissed) writing through the existing alert service, emitting the existing timeline/audit event; illegal transitions rejected with a message, not a 500.
- [ ] **C2** `ui-case-edit`: title, description, severity, TLP/PAP, assignee, flag, summary; org-scoped; audited; `Write`-share required; uniform 404 otherwise.
- [ ] **C3** `ui-observable-edit` + `ui-observable-delete`: edit value/enrichment; delete honours the §6.5 cross-case blast-radius guard (refuse or confirm, never silently unlink other cases); org-scoped; audited.
- [ ] Conformance: each new mutation view has an authz test (other-org caller → uniform 404) and a CSRF test.

### P3 — Procedures / TTP UI (A5)

- [x] Case detail can attach a procedure/TTP (new partial + form); list shows attached procedures with ATT&CK ID and description.
- [x] Works without JS; invalid ATT&CK id → message, not 500.

**P3 status (2026-10-12): DONE — verified + security-reviewed.** Gate `make check` = **1538 passed / 3 skipped** (ruff + format + mypy 105 files + migrations + css clean + full pytest). New conformance test files: `tests/conformance/test_ui_procedures.py` (22 cases: rendering, valid/invalid create, bulk create, per-item DB-failure isolation, bulk cap, authz, CSRF, no-JS, XSS) + `tests/conformance/test_ui_css_tokens.py` (2 cases). Independent verification: `docs/verification/VERIFY-2026-10-12-ui-completeness-p3.md` (**5/5 ACs met**). Security: `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (**0 Critical / 0 High**; 1 Medium = the systemic `read-only` gap tracked from P5, now at two more surfaces). Follow-ups recorded in `TODO §11.4`.

On the AC "invalid ATT&CK id → message, not 500": the plan meant a bad `ttpId` (which is an FK). Both that and a malformed `occurDate` now produce a flash message and a redirect, never a 500 — and `patternId` is *also* enforced as required by the form (see deviations W616P3-1). A syntactically odd but non-empty `patternId` is still accepted, deliberately: `pattern_id` is a plain string in the model and in `compat.procedures.parse_procedure` (TheHive parity), so the UI does not invent a stricter validation the API does not have.

Defects found and fixed during the phase (details in the verification record once it lands):
- **CSS token bug** — the new `.procedures`/`.form-grid` rules referenced 7 tokens that do not exist in `app.css` (`--color-border`, `--color-fg`, `--color-primary`, …). An undefined `var()` makes the whole declaration invalid at computed-value time, so every border/badge colour was silently dropped. Now mapped to the real theme tokens, `outline:none` removed from the focus rule (WCAG 2.4.7), and guarded by the new `test_ui_css_tokens.py`.
- **Bulk-create transaction bug** — items were created inside one atomic block with no per-item savepoint, so a DB-level failure in one item poisoned the batch and every *later* item failed with `TransactionManagementError` (one bad row silently became N). Each item now runs in its own savepoint; regression test added and confirmed to fail without the fix.
- **Duplicated `_resolve_ttp_value`** — the UI had copied the resolver twice instead of importing the canonical one from `cases.views` (same class of drift as the CSS bug).
- **Message hygiene** — `except Exception as exc: messages.error(f"...{exc}")` leaked the exception text to the analyst; the detail now goes to the logger and the flash carries a generic message.
- **Test hardening** — the ttp-code-precedence test was tautological (both values were the same string) and the XSS test asserted nothing meaningful; both rewritten.

### P4 — Settings expansions (C5, C6) — pulls TODO §6.13 forward

- [ ] **C5** Settings → Ingestion tokens: list (prefix, org, scope, created, last used, revoked), create (secret **shown once**), revoke. superAdmin-only; uniform 404 for others; audited.
- [ ] **C6** Settings → Audit log: read-only, keyset-paginated table (actor, actor_org, target_org, action, target_type/id, ip, time) with filters. superAdmin-only. **No export** in this phase.
- [ ] Both pages pass the CSP/escaping conformance net (`test_settings_ui.py` pattern).

### P5 — Playbook round-trip + Markdown (D1, D3)

- [x] **D1** Editing a playbook pre-selects the stored action and round-trips url/method/timeout/body/action_path/headers; saving without changes is a no-op; a test asserts the action config survives a GET→POST cycle.
- [x] **D3** Case description and comment/ledger bodies render as **sanitized CommonMark** (decision 3.3), server-side, with a strict allow-list (no raw HTML, no `javascript:`); plain-text fallback preserved; escaping test with `<script>`/`<img onerror>` payloads. Runtime dependency decision = **Q1**.

**P5 status (2026-10-12): DONE — verified + security-reviewed.** Independent verification: `docs/verification/VERIFY-2026-10-12-ui-completeness-p5.md` (18/18 ACs met). Security: `docs/reviews/REVIEW-2026-10-12-ui-completeness-p5.md` (0 Critical, 0 High). Fixed during the batch: textarea stored-XSS in `playbook_form.html` (`|safe` → autoescape, + regression test), unbounded `header_count` → `MAX_HEADERS = 64` reject (M2), missing `@require_http_methods` (L2), undefined CSS var + `.md` `white-space` (D3). **Open, deferred to decisions below:** M1 (does `read-only` mutate? — role policy, pre-existing, matches API), M3 (playbook headers are global + rendered in cleartext → secret exposure), L1 (protocol-relative `href` survives the sanitizer). See `TODO` §10.9.

### P6 — Global search (B3)

- [ ] Header search input (`/` focuses it, as the cheat-sheet already promises) → `ui-search` results page: matches across cases (number/title/description), alerts (title), observables (value), **org-scoped**, paginated.
- [ ] Empty query renders the page; no result renders an empty state; a cross-tenant match never leaks (authz test).
- [ ] If (and only if) `EXPLAIN` shows a seq scan on seeded volume, add the trigram index in its own migration.

### P7 — Realtime breadth + HTMX adoption (D4, D5)

- [ ] **D4a** Alert list, case list and dashboard live-update via a per-org channel (`org_{org_id}`) published alongside the existing `case_{id}` room; HTTP fallback unchanged.
- [ ] **D4b** Case detail shows a presence indicator ("N analysts viewing") — counts only, no identities, no new personal data.
- [ ] **D5** Convert the list filters and case-detail mutations to HTMX partial swaps where it improves the interaction, keeping the full-POST fallback; the conversion is scoped to those screens, not "every form in the app".
- [ ] Realtime tests: publisher/consumer unit tests + an authz test that a foreign-org subscriber cannot join `org_{other}`.

## 9. Security

- **Authz:** every new view resolves through `identity/scoping.py`; uniform 404 (no existence oracle). New mutation views get object-level authz tests including the write-share vs read-share distinction.
- **CSP:** no inline asset; `test_ui_inline_assets.py` must stay green (it runs over all templates).
- **XSS:** the only new rich content is Markdown (D3) — sanitize server-side with an allow-list; test `javascript:`, `<script>`, `onerror=`, and raw HTML. Everything else stays `{{ }}`-escaped.
- **Secrets:** the ingestion-token secret (C5) is displayed **once** on create and never re-rendered from storage (mirrors the F9 `secret_hash` exclusion); no token value in logs, URLs, or the audit body.
- **CSRF:** all new POST routes use Django CSRF; HTMX requests carry the CSRF token (existing pattern).
- **Audit:** case edit, observable delete, token create/revoke, and superAdmin cross-org reads write `AuditLog` entries (the viewer in C6 surfaces them).
- **SuperAdmin surfaces:** tokens + audit are superAdmin-only; non-superAdmin gets uniform 404.

## 10. Testing

- **Conformance:** extend `test_ui_urls_smoke.py` to render **every** named `ui-` route (closing verifier N3 — no "Python-only redirect" escape hatch); add `test_ui_nav.py` (rail ⇄ keys.js ⇄ cheat-sheet consistency), `test_ui_attachments.py`, `test_ui_search.py`, `test_ui_markdown.py` (XSS payloads), `test_ui_tokens.py`, `test_ui_audit_viewer.py`, realtime `org_` channel tests.
- **Authz matrix:** each new view gets a same-org / cross-org / no-org / superAdmin table test.
- **No-JS:** at least one test per P1/P2 flow issues a plain POST (no `HX-Request`) and asserts the redirect/HTML path.
- **Regression:** `make check` is the Definition of Done; the new routes must not reduce coverage below the current 79 % baseline (ideally push it toward 80 % — §2.6).
- **Self-verification vs independent:** implementers self-test; `verifier` re-checks ACs from the diff (implementers have over-claimed before), `security-auditor` reviews C1/C2/C3/C5/C6/D3, `qa-tester` owns the regression net.

## 11. Risks

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | **Scope**: 24 items is a multi-day wave; P6/P7 (search, realtime/HTMX) are each as large as P1–P5 combined | High | Phase-by-phase ship + verify; P6/P7 can land separately without blocking the rest |
| R2 | **Markdown dependency** (D3) requires new hashed runtime deps in a public repo | Med | **Resolved (Q1 → A, 2026-10-12):** `markdown-it-py` + `nh3` added to hashed `base.txt` via `scripts/lock.sh`; sanitizer allow-list + XSS conformance test (ADR-005) |
| R3 | **C3 observable delete** touches cross-case sharing | Med | Reuse the §6.5 blast-radius guard; refuse with an explicit message; test the multi-case case |
| R4 | **C1 alert triage** duplicates API logic in the UI | Med | Call the existing `_set_alert_fields`/`_update_alert` service, never re-implement |
| R5 | **D5 HTMX adoption** regresses the no-JS fallback | Med | Every conversion ships with a plain-POST test; HX-Request branch in the view |
| R6 | **P4 audit viewer** could leak cross-org history to a non-superAdmin | Low | superAdmin-only + uniform 404 + authz test |
| R7 | **D4 per-org channel** widens the realtime blast radius | Low | Reuse the existing visibility check; add a foreign-org join test |
| R8 | Tailwind constraint (§7.3) leaks into "why isn't my class working" confusion | Med | Document in the plan + component-class convention; §7.4 remains open |

## 12. Open Questions

- **Q1 (blocks P5/D3) — DECIDED 2026-10-12 → Option A.** Markdown stack = **`markdown-it-py`** (CommonMark parser) **+ `nh3`** (Ammonia bindings, strict allow-list sanitizer), added to the **hashed runtime requirements** (`requirements/base.txt`), regenerated with `scripts/lock.sh`, and governed by **ADR-005**. Rendering is **server-side** in a template filter (`ui_tags.markdown`); case descriptions and comment/ledger bodies lose raw HTML, `javascript:` URLs and event-handler attributes. Rejected alternatives: (b) hand-rolled subset — its own XSS surface, larger review burden; (c) defer D3 — drops the wave's core "rich ledger" requirement.
- **Q2 (blocks P1/A3c).** Bulk operations: implement (multi-select → status/assignee/tag) or delete the stub route? Recommendation: **delete the stub now** and file bulk as its own item — it is a UI+API+authz design in its own right and would otherwise hold up P1.
- **Q3 (blocks P7/D5).** How far does HTMX go? Recommendation: **list filters + case-detail mutations only**; a full-app conversion is a separate architectural wave.
- **Q4 (blocks P4/C6).** Audit-log retention/export policy — the viewer is read-only in this wave; if retention is required it is a separate concern.
- **Q5 (blocks P6/B3).** Search scope: cases+alerts+observables only (recommended) or also comments/ledger text (heavier, needs full-text search)?

## 13. Item → phase index (traceability)

| Item | Gap | Phase |
|---|---|---|
| A1 | attachments unreachable | P1 |
| A2 | merge unreachable | P1 |
| A3a/b | export / apply-template unreachable | P1 |
| A3c | bulk stub | P1 (decide) → own item |
| A4 | taxonomy unreachable | P1 |
| A5 | procedures UI missing | P3 |
| B1 | Observables rail entry | P1 |
| B2 | Tags / Case templates / Taxonomy rail | P1 |
| B3 | global search | P6 |
| B4 | tenant indicator | P1 |
| C1 | alert triage | P2 |
| C2 | case edit | P2 |
| C3 | observable edit/delete | P2 |
| C4 | case-template edit | P1 |
| C5 | ingestion tokens | P4 |
| C6 | audit viewer | P4 |
| C7 | password change | P1 |
| D1 | playbook round-trip | P5 |
| D2 | 500/403 pages | P1 |
| D3 | Markdown | P5 |
| D4 | realtime breadth + presence | P7 |
| D5 | HTMX adoption | P7 |
| D6 | cheat-sheet modal | P1 |
| D7 | Font Awesome usage | P1 |
| D8 | keyboard coverage | P1 |

## 13a. Implementation batching (handoff order)

1. **Batch 1 → `django-frontend`** — P1 templates/JS/CSS (rail, sections, dialogs, error pages, cheat-sheet) + `test_ui_nav.py`; **`django-backend`** in parallel for `ui-case-template-edit` (C4), `ui-password-change` (C7), and the A3c decision.
2. **Batch 2 → `django-backend`** — P2 views/services (C1, C2, C3) with authz/CSRF tests; then P3.
3. **Batch 3 → `django-backend` + `django-frontend`** — P4 (tokens, audit viewer), P5 (D1, D3).
4. **Batch 4 → `django-backend` + `django-frontend`** — P6, P7.
5. After each batch: `verifier` (ACs), `security-auditor` (new mutations), `qa-tester` (regression), then update `TODO.md` §6.16 / `COMPLETED.md` / `deviations.md` and re-run `make check`.

Any deviation from this plan is recorded in `docs/spec/deviations.md` under "UI completeness wave".

## 13b. Outcome — P1 (2026-10-11)

**Status: DONE.** Gate `make check` = **1430 passed / 3 skipped** (ruff + format + mypy 104 files + migrations + css clean). Independent verification: `docs/verification/VERIFY-2026-10-11-p1-ui-completeness.md` — **11 met / 3 partial / 0 not met**; every partial and the single MEDIUM security finding were closed in-wave (see deviations `W616P1-*`).

| Plan item | Outcome |
|---|---|
| A1 attachments | ✅ `case_detail` hosts upload/list/download/delete (field `attachments`, `created_by`); redirects back to the case |
| A2 merge | ✅ `<dialog>` posts `target_case`; POST integration tested |
| A3a/b export / apply-template | ✅ plain GET link + link to the existing route |
| A3c / **Q2** bulk | ✅ stub deleted (decision applied); bulk deferred to its own item |
| A4 taxonomy / B1/B2 rail entries | ✅ `o`/`t`/`m`/`x` in the rail with icons + shortcuts |
| B4 tenant indicator | ✅ org name → “All organisations” (superAdmin, no org) → “No organisation”; all three tested |
| C4 case-template edit | ✅ prefilled GET, audited POST, edit link; **plus** role gate (security fix) |
| C7 password change | ✅ `PasswordChangeView` at `ui-password-change`; themed template; rail link |
| D2 error pages | ✅ 404/403/500 wired; `500.html` standalone + zero-query proven |
| D6 cheat-sheet | ✅ server-rendered `<dialog>`; `?` **and** a visible control open it |
| D7 Font Awesome | ✅ every used icon has a subset rule (`test_ui_icons.py`) |
| D8 keyboard coverage | ✅ locked by `test_ui_nav.py` (rail ⇄ `ROUTES` ⇄ `CHEATSHEET` ⇄ table) |

**Still open:** **Q1** (runtime Markdown + sanitiser) — gates P5/D3; unchanged. **Q3/Q4/Q5** — later phases, unchanged.

## 13c. Outcome — P2 (2026-10-11)

**Status: DONE (code) — independent verification passed, security remediation applied.** Gate `make check` = **1464 passed / 3 skipped** (ruff + format + mypy 105 files + migrations + css clean). New guard `tests/conformance/test_ui_p2_wiring.py` (**30 cases**). Reports: verifier `docs/verification/VERIFY-2026-10-11-p2-ui-completeness.md` (C1/C2/C3 **Met**, re-verification Pass); security `docs/reviews/REVIEW-2026-10-11-p2-ui-mutations.md`. Deviations: `docs/spec/deviations.md` “Wave 6.16-P2” (`W616P2-1…5`).

| Plan item | Outcome |
|---|---|
| C1 alert triage | ✅ `ui-alert-status` POST; applies an **existing** `AlertStatus.value` only (no mint), audit + case-ledger entry; triage `<select>` fixed to post the value, not the pk |
| C2 case edit | ✅ `ui-case-edit` GET/POST over the new **shared** `cases/update.py` applier (API `_update_case`/bulk + UI are one definition); bounded status; assignee/tags/flag; audit |
| C3 observable edit | ✅ `ui-observable-edit`; applier wire shape (`dataType`, `ignoreSimilarity`) + validated `enrichment_data`; **`data` immutable** (per-org dedupe key) |
| C3 observable delete | ✅ `ui-observable-delete`; cross-case guard honoured; pk captured before `delete()` (audit no longer logs `"None"`) |
| Cross-case force (both) | ✅ in-form acknowledgement checkbox (`name="force"`) for edit **and** delete — matches the API's `?force=true` |
| Robustness (found) | ✅ `resolve_alert`/`resolve_observable` no longer 500 on a non-UUID id (uniform 404); missing `Response` import; TLP/PAP/severity select preselection |

### Security remediation (post-verification, same day)

`REVIEW-2026-10-11-p2-ui-mutations.md` found one MEDIUM + two LOW; all are fixed and independently re-audited (**M1/L1/L2/N1 → FIXED**, no regression):

| ID | Fix |
|---|---|
| **M1** (MEDIUM) | Cross-tenant assignee injection: `_resolve_login(value, *, org=None)` is now org-scoped (login **and** UUID); the applier threads `case.owner_org` and the task path `task.case.owner_org`, so the API PATCH, bulk and UI cannot bind a principal from another org. The UI additionally rejects a non-member with a clear error. |
| **L1** | Out-of-range `severity`/`tlp`/`pap` were an unhandled `IntegrityError` (500); `_set_case_fields` now range-checks against `core.enums` and raises `CaseUpdateError` → 400/form error on both surfaces. |
| **L2** | The `AuditLog` row now shares the mutation's transaction (a failed audit rolls the change back) — in `case_edit`, `observable_edit` and `alert_status`. |
| **NEW-1** | The case-edit assignee select prefilled the **UUID** while its options are **logins**, so an untouched save silently unassigned the case; now prefills the login. |
| **NEW-2** | `alert_status` audited outside the mutation transaction (same class as L2); now wrapped in one `atomic()`. |
| N1 | Re-render helpers read `QueryDict` scalars via `.get()` (no `['x']` echoes); `case_edit` now passes `is_cross_org`. |

Tests: `test_ui_p2_wiring.py` grew from 24 → **30** (cross-org assignee refused, applier login-scoping, out-of-range enum rerender, applier range error, assignee-login prefill, audit-failure rollback). Reports: `docs/reviews/REVIEW-2026-10-11-p2-ui-mutations.md` (incl. "Re-audit 2026-10-11"), `docs/verification/VERIFY-2026-10-11-p2-ui-completeness.md` (incl. "Re-verification 2026-10-11").

**Notes:** the seeded alert vocabulary (`New/Triaged/Dismissed` + `Imported`) matches the plan — no deviation. The observable form offers TLP `0..3` because the shared applier rejects `4/Unknown` (W616P2-5). **Q1** unchanged and still gates P5/D3.

## 14. References

- `AGENTS.md` §2 (modules B/C/D), §6 (design & style), §5 (secret hygiene)
- ADR-001 (stack), **ADR-003** (frontend assets/CSP, incl. §7.3 known limitation), ADR-004 (tenant isolation)
- `docs/planning/PLAN-2026-10-10-org-management.md` §13B (§6.15 outcome this wave builds on)
- `tests/conformance/test_ui_inline_assets.py`, `test_settings_ui.py`, `test_ui_table_markup.py`, `test_ui_urls_smoke.py`
- TODO §6.13 (tokens/audit → pulled into P4), §2.6 (coverage), §7.4 (Tailwind split), #5 (orphan routes → mostly closed by P1)
