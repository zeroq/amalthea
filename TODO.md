# TODO — Amalthea

Open work only. Completed items live in [`COMPLETED.md`](./COMPLETED.md).
Last updated: 2026-10-11 (**§6.12 org/user Settings shipped + verified** (24/24 ACs) + **§6.14 Wave 6.3A Settings UI hardening done** — F3/F5/F6/F7/F8/F9/F10 fixed, security re-audit clear, verifier 17/17)

**Conventions** — every item carries a `Source` (plan AC, review finding ID, or decision ID) so it can
be traced, and `Blocks` when it gates other work. Review IDs (`C1`, `H2`, `M5`…) refer to
`docs/reviews/REVIEW-2026-10-03-phase3-schema-gate.md` (round 1) and
`REVIEW-2026-10-05-phase3-schema-gate-round3.md` (round 3).

**Status legend** — `[ ]` open · `[~]` in progress · `[!]` blocked

---

## 🎯 NEXT HIGHEST-IMPACT ITEMS (Priority Order)

| Priority | Item | Description | Impact |
|----------|------|-------------|--------|
| **1** | **Security remediation (Wave B)** | From `VERIFY-2026-10-10-t2-and-phase12.md` / SEC-AUDIT. **DONE 2026-10-10 via the tenant-isolation wave (ADR-004):** F2/S1/S2 scope guards (incl. the WebSocket consumer) and the Wave-A UI delete route, S4 object-level authz on `run_now`, S6 SSRF DNS-rebinding TOCTOU + timeout clamp, S7 merge share-copy widening, F2 org-blind case/alert/observable resolution + global observable mutation. **Remaining:** S3 per-account lockout on the **API** login + stop trusting client `XFF`; S5 attachment aggregate caps + extension-from-content-type. | **High** — remaining S3/S5 for a hardened multi-user deployment. |
| **2** | **6.4 — Tenant isolation** | ✅ **SHIPPED 2026-10-10** — strict multi-tenant model (mandatory `owner_org` on Case/Alert/Observable, per-org webhook tokens, roles, superAdmin cross-org audit, org-scoped query API/WebSocket/UI, per-org observable dedupe). Plan `PLAN-2026-10-10-tenant-isolation.md`; ADR-004; `make check` 1110 passed. | **Done.** |
| **3** | **Test hardening (Phase 12 / T2)** | QA list from `VERIFY-2026-10-10`: delete-with-runs row survival, merge children matrix, attachment boundaries (exact cap/empty/batch partial), timeout cap 30/31, run-now `Success`+`output_log`, `SESSION_COOKIE_HTTPONLY`, UI login-throttle; §8.5 mutation guards for blast-radius/share/TTP/delete-with-runs/attachment. Extend `test_ui_urls_smoke.py` to Python-only `redirect` names (verifier N3). **Also restore coverage ≥ 80%** (§2.6 — dropped to 79% after the tenant-isolation + org-management waves). | **Medium** — regression prevention. |
| **4** | **7.4 — Tailwind source/artifact split** | `ui/static/ui/app.css` is both the Tailwind input and the committed output; the pipeline is not idempotent so `css-check` cannot detect drift. Split `app.src.css` (source) → `app.css` (artifact). See ADR-003 "Known limitation". | **Low** — build hygiene. |
| **5** | **7.5 — Orphaned routes / `ui-case-merge`** | **`ui-case-merge` FIXED 2026-10-10** (tenant-isolation P7): correct `cases.merge.merge_cases` import + signature, added `ui/case_merge.html`, removed the dead `@require_GET` block, org-scoped. Remaining: six routes (`ui-case-export/bulk/taxonomy/procedure(s)/attachment-detail`) still have no entry point anywhere. Either build the missing UI or delete the dead routes/views. | **Low** — dead routes. |
| **6** | **6.12 — Settings: organisations + users (superAdmin)** | New `superAdmin`-only **Settings** area with two pages — **Organisations** and **Users** (list / add / edit / delete) + matching Amalthea API. **DONE 2026-10-11** (O1–O5; verifier 24/24 ACs; security review clear; §6.14 hardening landed on top). Plan: `docs/planning/PLAN-2026-10-10-org-management.md`. | **Done.** |

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
- [ ] **6.15 — Wave 6.3B: repo-wide ADR-003 compliance (externalise all inline scripts/styles)** Scope **(B)**, follow-up to §6.14: audit the whole `ui/templates/` tree and externalise **every** remaining inline `<script>`/`<style>`/`style="…"` (case detail `live.js` bootstrap, playbook forms, cheatsheet modal, etc.) so `script-src 'self'`/`style-src 'self'` hold without `unsafe-inline`. Pre-existing shipped surfaces, so track separately from the Settings fix. Plan §13B. **Not scheduled.**

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

## 🎯 Next Recommended Items

See the canonical **NEXT HIGHEST-IMPACT ITEMS** table at the top of this file (it is the single source of
truth and is kept current). As of **2026-10-11** the open items are: **S3** — per-account login lockout on
the API (`POST /api/v1/login`) + stop trusting the client `X-Forwarded-For`, and **S5** — attachment
aggregate caps + extension-from-content-type (§ priority #1); **Phase 12 / T2 test hardening** (priority
#3) and the two low follow-ups (#4–#5). **§6.4 tenant isolation** (strict multi-tenant, ADR-004),
**§6.12 org/user Settings** and **§6.14 Wave 6.3A Settings UI hardening** all shipped 2026-10-11.

Phase 12 code landed (P1–P3, P5–P6), but it is **not** fully shipped: independent verification on
2026-10-10 found 3 ACs MET / 3 DEVIATED / 10 NOT MET (see `VERIFY-2026-10-10-t2-and-phase12.md`). Wave A
closed the three unambiguous bugs (B1/B2/B3) + a mypy gate regression; the tenant-isolation wave closed
the scope-guard/`run_now`/SSRF/merge-widening security items (ADR-004). The remaining AC gaps and the S3/S5
security items are tracked as priorities #1 and #3 above. Nothing is now genuinely *deferred* — the last
such item (**6.4 — Tenant isolation**) shipped on 2026-10-10.

**Recommended next (big impact): S3 — close the API login brute-force gap.** `identity/ratelimit.py`
(5 attempts / 15 min, 5 min lockout) is wired **only** into the UI login (`ui/views.py`); the TheHive-compat
`POST /api/v1/login` (`compat/views.py::api_login`) is protected only by the generic DRF per-IP 100/min
throttle and returns **200**, not **429**. Wire `check_login_rate_limit`/`record_failed_login`/
`record_successful_login` into `api_login`, return **429** with `Retry-After` on lockout (and 429 for the UI
too), and derive the client IP from the proxy chain rather than a client-supplied header. Small, well-scoped,
and closes the last unauthenticated brute-force surface on a platform that is otherwise multi-tenant-hardened.
Runner-up: **§6.15 / ADR-003 repo-wide** — 6 templates + `404.html` still carry inline scripts/`style=` that
the shipped `script-src 'self'` CSP silently blocks (same class as the wave just closed).