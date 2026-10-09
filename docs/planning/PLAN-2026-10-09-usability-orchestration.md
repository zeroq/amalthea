# PLAN — Phase 12: Usability & Orchestration

- **Date:** 2026-10-09
- **Status:** Approved 2026-10-09 — ready for implementation (Q1–Q5 resolved, §12)
- **Owner:** planner (tech lead)
- **Source:** `TODO.md` §6.8–§6.11 (review 2026-10-09) · §6.2/§6.4/§6.5/§6.7 · `AGENTS.md` §1, §2 (Modules B/C/D), §6
- **Related:** `docs/decisions/ADR-001` (Django/DRF/Channels), `ADR-002` (TheHive-compatible wire), `docs/spec/{api,data-model,realtime,automation,deviations}.md`, `PLAN-2026-10-03-thehive-compatible-mvp.md`
- **Predecessors:** §1–§5 and Wave 6.1 (T1/T2 API surface) all **shipped**; `make check` 941/3 green, Postgres re-gate green.

---

## 1. Summary

The backend is functionally complete: ingestion, lifecycle, observables, orchestration, realtime,
query DSL, and the full T1/T2 TheHive wire surface all pass their gates. What is missing is the layer
that makes it a **platform an analyst can actually operate**, and it is not one of the open TODO items:

1. **Module D is unconfigurable.** `Playbook` is a first-class model (ADR-002 §D4) reached by
   `automation/dispatcher.py` on every domain event — but there is **no create/read path anywhere**:
   no API view, no URL, no UI, no seed fixture, and only `identity` registers admin models. Today a
   playbook only exists if someone writes the row by hand. Amalthea's stated differentiator cannot be
   exercised by a user.
2. **The UI is ~5 phases behind the API.** Every T2 P1–P5 endpoint (tags, comments/pages/shares,
   attachments, case templates, procedures/TTP, export, bulk) is API-only. Module C's headline —
   "this artifact appeared in N cases over 6 months" — is served by the `observable_detail` fan-out but
   has no page.
3. **The declared frontend stack is not installed.** `case_detail.html` ships `hx-*` attributes with
   no htmx on the page; styling is hand-written `app.css`, not Tailwind. AGENTS.md §1/§6 spec both.
4. **Login is not rate-limited** (webhooks are).

Phase 12 closes these and folds in the deferred platform-hardening items that a real deployment needs:
paged case timeline (§6.7), observable mutation safety (§6.5), Postgres-only indexes (§6.2), and a
decision on tenant isolation (§6.4).

## 2. Goals

1. **Module D is authorable end-to-end** from the UI and API: create/edit/enable a playbook, pick a
   trigger and an action, see it run, read the result in the case ledger.
2. **The analyst UI surfaces the whole T2 object model** — observables graph, tags, collaboration,
   attachments, templates, procedures/TTP — so no shipped endpoint is UI-less.
3. **The spec'd frontend stack is real**: HTMX loaded, Tailwind compiled, Font Awesome Free icons,
   all self-hosted and CSP-clean.
4. **A public deployment is defensible**: login throttling, paged timelines, an explicit observable
   mutation policy.

## 3. Non-goals

- Not a React SPA / TheHive-UI clone (BRIEF §0.3 still holds); server-rendered + HTMX only.
- No playbook *builder* (drag-and-drop DAG / Tines-style canvas). One playbook = one action, as
  `automation/executor.py` already defines.
- No new external integrations (VirusTotal/AD/firewall *clients*). The HTTP action already covers
  arbitrary outbound calls; a bundled connector library is a later wave.
- Multi-signal correlation, alert-ingest format plugins, and per-link observable tags (TODO 6.3) are
  out of scope unless the open questions below pull them in.
- Tenant **enforcement** is **deferred** (Q4, 2026-10-09): this plan records the deferral and its
  options in `docs/spec/deviations.md`; TODO §6.4 stays open. §P6 ships only the timeline and
  observable-mutation items.

## 4. Architecture

**No architectural pivot.** Everything below uses the shipped stack (Django 5.x + DRF + Channels +
Celery/Redis, server-rendered templates). Rationale and alternatives live in `ADR-001`; the wire
contract stays in `compat/` + `core/serializers.py` per the one-way boundary (`test_wire_boundary.py`).

### 4.1 Frontend stack (decision needed — §12 Q1/Q2)

- **HTMX**: self-host a pinned `htmx.min.js` under `ui/static/ui/vendor/` (like `live.js`). Loaded
  once in `base.html`. Progressive enhancement is preserved: every `hx-*` form already falls back to
  a normal POST.
- **Tailwind:** compile to a single `ui/static/ui/app.css`. Two options — **(a)** the standalone
  Tailwind CLI pinned by version + SHA in `scripts/` (no Node toolchain, fits the "dependency-light"
  posture), or (b) a Node build step. **Recommendation: (a).** The compiled artifact is committed; CI
  adds a `make css` currentness check mirroring `scripts/lock.sh --check`.
- **Font Awesome Free** (AGENTS.md §6): self-host the free webfont subset (or a trimmed CSS +
  webfonts) under `ui/static/vendor/fontawesome/`. Record the license (`CC BY 4.0` icons /
  `SIL OFL 1.1` fonts) in `THIRD-PARTY.md`. Icons are `aria-hidden="true"` when decorative, carry an
  accessible name when icon-only, and never replace a visible label on a primary action.
- **ADRs:** `ADR-003-frontend-assets.md` records the decisions above: self-hosted assets, **no CDN**
  at runtime (Q1), and a Tailwind **compile** pipeline with the compiled stylesheet committed (Q2) —
  keeps `SECURE_*`/CSP simple and the app offline-capable.

**Rendering stays server-side.** HTMX fragments are small Django template partials; the existing
`cases/ledger.py` + Channels path is the source of live truth and is unchanged.

## 5. Data model

Minimal, additive. All migrations forward-only.

### 5.1 New / changed entities

- **`Playbook`** (existing; `automation/models.py`) — no new columns required for authoring. Add:
  - `Meta.constraints`: a CHECK that `trigger_event` ∈ `TRIGGER_EVENTS` is **validated at the
    serializer**, not the DB (the vocabulary is code-owned and may grow; a DB CHECK would force a
    migration per trigger).
  - No `db_table`/index change. `playbook_trigger_idx` already covers dispatch.
- **`AutomationRun`** (existing): add a nullable `triggered_by` `CharField(max_length=20)` with values
  `event` / `manual` (default `"event"`) so the ledger can distinguish a human-run playbook from an
  event-driven one. Migration + a CHECK via `in_values`. Index no change.
- **`Case` / `Observable`/`CaseTemplate`/`Procedure`:** **unchanged.** The UI is a view over existing
  rows.
- No schema change is needed for Phase P3 (observables graph) or Phase P4 (UI catch-up): the fan-out query
  already exists (`Observable.case_observables`).

### 5.2 Deferred (decided 2026-10-09)

- **Tenant isolation (§6.4):** **deferred** (Q4, 2026-10-09). If/when enforced it is a follow-up
  migration adding `owner_org` population + per-query filters (or middleware org-scope); not built
  this wave. The deferral and its options are recorded in `docs/spec/deviations.md`.

## 6. API contracts

House rules apply: UUID/`number` `{idOrName}`, ISO-8601 timestamps in entity JSON, org/tenant notes
per §6.4, per-endpoint authz rows in `tests/conformance/test_authz.py`. Playbook routes are an
**Amalthea extension** (TheHive 5 / `thehive4py` 2.1.0 expose no playbook route) — recorded as
deviation **P12-2** (P12-1 is the `task.completed` fix, §P2).

### 6.1 Playbook authoring (Phase P2)

| Path | Methods | View |
|---|---|---|
| `playbook`, `playbook/` | GET (list), POST (create) | `playbook_collection` |
| `playbook/_meta` | GET | `playbook_meta` |
| `playbook/<idOrName>` | GET, PATCH, DELETE | `playbook_detail` |
| `playbook/<idOrName>/run` | POST | `playbook_run` |

- Body (create/update): `{"name","description","triggerEvent","isActive","config"}`. `triggerEvent`
  must be one of `automation.dispatcher.TRIGGER_EVENTS`. DELETE is refused (`409`/`400`) while the
  playbook has any `AutomationRun` (FK is `PROTECT`) — return a clear message rather than a 500.
- `config` is validated by action type (the executor's contract):
  - `{"action":"http","url","method","headers","body","timeoutSeconds"}` — `url` required; `method`
    from a fixed set; `headers` a string→string map; `timeoutSeconds` a positive number (capped).
  - `{"action":"python","action_path"}` — `action_path` **must** be a key returned by
    `automation/executor.registered_actions()` (validation, not invocation; nothing is imported).
  - unknown `action` → `400`. This is the authoring-time mirror of the executor's runtime refusal.
- `GET /playbook/_meta` → `{"triggerEvents":[…], "actions":[{"action":"http",…},{"action":"python",
  "registeredPaths":[…]}]}`, so the UI pickers are data-driven and cannot drift from the code.
- `POST /playbook/{idOrName}/run` body `{"case"?:idOrNumber,"observable"?:id}`: enqueues exactly one
  `AutomationRun` (Celery) with a **non-deduplicating** key (`manual:<uuid4>`), `trigger_event`
  recorded as the playbook's (or `"manual"`), `triggered_by="manual"`. Returns `202` + the run id;
  the result lands in `output_log` **and** the case timeline exactly as scheduled runs do. At least
  one of `case`/`observable` is required.
- Serialization: `core/serializers.py::playbook_json` + `automation_run_json`, house entity style
  (ISO-8601, `_`-prefixed only where the wire boundary already does — this is not a TheHive entity).

### 6.2 Deferred platform items (see §8 P6)

- `case/{id}?includeTimeline=false` (or a `timelinePage`/keyset envelope) for §6.7 — exact shape
  chosen at implementation; must stay compatible with thehive4py's `getCase` call (AC8.4).
- Postgres `GinIndex` on `raw_payload` / `Observable.tags` + `INCLUDE` covering indexes (§6.2),
  behind `django.contrib.postgres`, added to `INSTALLED_APPS` for Postgres only.

## 7. UI / UX

All pages: dark theme, semantic severity (labelled, never colour-only), Font Awesome Free icons,
keyboard reachable, HTMX-enhanced with a plain-POST fallback, Channels for live regions.

- **New nav destinations:** *Observables*, *Playbooks* (under Automation).
- **Observables index** — searchable/filterable by type; columns: type, value, # cases, first
  seen/last seen. **Observable detail** — the fan-out: every case the artifact appears in, with
  links, plus tags, enrichment data, and related AutomationRuns. This closes Module C in the UI.
- **Playbooks** — list (name, trigger, action, active, last run/status) and a detail/edit form:
  trigger `<select>` from `_meta`, action radio (http/python), dynamic config fields, activate
  toggle, **Run now** (case/observable picker), and the run history. Empty state links to a seeded
  example playbook.
- **Case detail** additions: add-observable form, attachment upload, tags editor, procedures/TTP
  capture, template apply, and (live) notes pages/shares. All via HTMX partials that reuse the same
  service functions the API calls (`ui/views.py` already does this).
- **Keyboard:** extend `ui/static/ui/keys.js` and add a discoverable `?` cheat-sheet per AGENTS.md §6.
- **A11y:** keep the existing conformance style — severity/status text+colour tests extended to the
  new pages.

## 8. Phases, tasks, acceptance criteria

Each phase lands green under `make check` (ruff, format, mypy strict, `manage.py check`,
`makemigrations --check`, pytest) plus secret scan. "AC met" requires a **non-vacuous** test
(mutation-proven where a guard is added, per the mutation standard in TODO §8).

### P1 — Frontend foundation (TODO 6.10)

- **T1.1** Add `ADR-003` recording self-hosted assets + Tailwind compile choice; add `THIRD-PARTY.md`
  with Font Awesome/Tailwind/HTMX licenses.
- **T1.2** Vendor HTMX (pinned version + integrity) under `ui/static/vendor/`; load it in
  `base.html` after the deferred-friendly `<body>`; confirm no `hx-*` regression.
- **T1.3** Port `app.css` to a Tailwind source + build pipeline (`make css` / `make css-check`);
  produce the compiled stylesheet; keep the dark palette + semantic severity tokens.
- **T1.4** Vendor Font Awesome Free subset + a small `icon` template tag rendering
  `<i class="fa-…" aria-hidden="true">`; document accessible-name rules in `docs/spec/`.
- **T1.5** Add CSP that allows only `'self'` for scripts/styles/fonts; extend
  `test_prod_settings.py`/UI tests.
- **AC6.12-P1-a** Loading the app in a browser (smoke) executes no external network request; the
  live-note form POSTs via HTMX (assert the fragment response, not a full page).
- **AC6.12-P1-b** `make css-check` fails when the compiled CSS is stale (mutation: hand-edit a class).
- **AC6.12-P1-c** Every icon is `aria-hidden` or labelled; an a11y test asserts no icon-only control
  lacks a name.

### P2 — Orchestration authoring (TODO 6.8) — *the differentiator*

- **T2.0 (closes deviation P12-1)** Emit `TaskCompleted`: add a `Task` receiver in
  `automation/registry.py` mirroring `case_saved` — capture the previous status, emit on the
  transition **into** `Completed` only — and route the UI task-toggle path through it. Update
  `docs/spec/automation.md`/`deviations.md` to retire P12-1.
- **T2.1** `Playbook` config validation (`automation/playbooks.py`): `validate_config()` mirroring
  `executor.execute`'s contract, tested against every branch incl. unknown action + unregistered path.
- **T2.2** `core/serializers.py::playbook_json`, `automation_run_json`.
- **T2.3** Views `playbook_collection`, `playbook_detail`, `playbook_meta`, `playbook_run`; routes;
  `__all__`; DELETE guard on existing runs.
- **T2.4** Manual-run service (`automation/dispatcher.py::run_now`) creating the `AutomationRun` +
  Celery enqueue with a non-dedup key; result feedback via the existing ledger path.
- **T2.5** Seed a working example playbook (migration or `make seed`) bound to `observable.created`,
  using the registered `_enrichment_probe` python action (no external call, deterministic).
- **T2.6** `test_playbook_authoring.py`: CRUD, `_meta` non-drift (trigger list == `TRIGGER_EVENTS`),
  config validation matrix, run-now → run row + timeline entry, DELETE-with-runs refused,
  authz rows in `test_authz.py`.
- **T2.7** UI: Playbooks list + edit form + Run-now + run history (reuses T2.3 endpoints/services).
- **AC6.12-P2-a** An analyst can create a playbook from the UI, trigger it by linking an observable,
  and see a Success `AutomationRun` + ledger note — **without touching the DB** (end-to-end test).
- **AC6.12-P2-b** A playbook naming an unknown action or unregistered `action_path` is rejected at
  write time (400), never enqueued.
- **AC6.12-P2-c** `_meta` cannot drift: a test fails if the advertised trigger list differs from
  `TRIGGER_EVENTS` or the registered-path list differs from `registered_actions()`.
- **AC6.12-P2-d** Completing a task emits exactly one `TaskCompleted` and fires a playbook bound to
  `task.completed`; re-saving an already-`Completed` task emits nothing (closes P12-1).

### P3 — Observables graph (TODO 6.9, Module C)

- **T3.1** UI `observable_list` (filter by type/query) + `observable_detail` (fan-out cases, tags,
  enrichment, runs); reuse `cases/views.py::observable_detail` service, not HTTP.
- **T3.2** Link cases both ways; show first/last seen.
- **T3.3** Tests: fan-out correctness across ≥3 cases; a11y + no-N+1 (select_related/prefetch).
- **AC6.12-P3-a** An artifact present in N cases shows exactly those N cases with working links.

### P4 — Case-surface catch-up (TODO 6.9)

- **T4.1** Tags editor (attach/detach) on case/alert/observable.
- **T4.2** Attachment upload/download (P3 API surface) with size-cap handling surfaced in the UI.
- **T4.3** Collaboration: comment/page/share UI (P2 API surface), Markdown rendering reuse.
- **T4.4** Case templates picker/apply + taxonomy-driven status/severity selects.
- **T4.5** Procedures/TTP capture on case/alert; case export button.
- **T4.6** Bulk action toolbar (select rows → `_bulk` endpoints).
- **AC6.12-P4-a** Each T2 endpoint family has at least one reachable UI path, covered by a UI test.
- **AC6.12-P4-b** Forms degrade to plain POST when HTMX is absent (progressive enhancement test).

### P5 — Login hardening (TODO 6.11)

- **T5.1** Throttle login (per-IP + per-username, DRF `AnonRateThrottle`/custom) with tests.
- **T5.2** Assert `SESSION_COOKIE_HTTPONLY` and other cookie flags in `test_prod_settings.py`.
- **AC6.12-P5-a** N failed logins from one IP/username are throttled (429) and the failure is
  logged; a successful login resets the counter (or the window is time-based — document which).

### P6 — Deferred platform items

- **T6.1 (6.7)** Paged/keyset case timeline without breaking `thehive4py.getCase` (AC8.4 retained).
  **AC6.12-P6-a** A case with 10k events responds with a bounded payload and the client can page.
- **T6.2 (6.5)** Observable mutation policy (Q3): `PATCH`/`DELETE` requires an explicit
  `?force=true`. Without it, mutating an observable linked to **other** cases is refused with `409`
  and a body listing the affected cases; with `?force=true` the mutation proceeds and the UI shows
  an impact warning naming those cases first. (Single-case observables mutate normally.)
  **AC6.12-P6-b** Mutating a shared observable without `force` is refused with the linking cases
  listed; with `force` it succeeds and cannot silently corrupt the other cases.
- **T6.3 (6.2)** Postgres `GinIndex` (raw_payload, `Observable.tags`) + `INCLUDE` covering indexes,
  asserted on Postgres only; `django.contrib.postgres` added for the Postgres settings only.
  **AC6.12-P6-c** `EXPLAIN` (Postgres) selects the GIN index for a JSONB containment query.
- **T6.4 (6.4)** Tenant isolation is **deferred** (Q4). Record the further deferral in
  `docs/spec/deviations.md` with the enforcement options (middleware org-scope vs per-view filters;
  superuser/analyst roles) so the next wave starts from a decision, not a blank page. No code; keep
  TODO §6.4 open.

### P7 — Docs & close

- **T7.1** Update `docs/spec/api.md` (playbook routes), `automation.md` (authoring model),
  `data-model.md` (`automation_run.triggered_by`), `deviations.md` (new P12-* + the ADR-003 asset
  choice), `realtime.md` if new live regions.
- **T7.2** Update `TODO.md`/`COMPLETED.md`; set plan status SHIPPED; record per-phase deviations.
- **AC6.12-P7-a** Every shipped route appears in `docs/spec/api.md`; deviations register has no
  undocumented divergence (drift policy in `docs/spec/README.md`).

## 9. Security

- **Authoring is a privileged surface.** Playbook create/update/delete/run require write scope;
  `test_authz.py` gets the full READ/MUTATING matrix. The SSRF guard remains the runtime boundary —
  authoring validation is *additional*, never a replacement.
- **No code execution from config.** `python` actions remain registry-only (unchanged); the UI/API
  can only name an already-registered path.
- **Self-hosted assets + strict CSP** (no inline/eval) reduce XSS surface; Markdown stays sanitized
  (CommonMark decision §3.3).
- **Login throttling** (Phase P5) closes the brute-force gap; secrets remain env-only (§5 AGENTS.md).
- **Tenant isolation is deferred (Q4).** No endpoint reads or filters by organisation this wave; the
  deferral is recorded in `docs/spec/deviations.md` rather than silently ignored. Any future
  cross-user endpoint is gated on that decision.

## 10. Testing

- Extend the conformance suite: `test_playbook_authoring.py`, additions to `test_authz.py`,
  `test_fk_audit.py` (any new FK), `test_wire_boundary.py` (ensure no new TheHive literal leaks from
  `automation/`), `test_enum_contracts.py` (new status/trigger vocab), `test_prod_settings.py` (CSP,
  cookies), plus UI/a11y tests.
- Mutation guards for any new invariant (config validation, `_meta` non-drift) per the mutation standard.
- Keep `make check` as the single DoD; Postgres job already covers vendor-specific items (P6).
- Non-goal: load/soak testing; the perf transcripts remain the reference.

## 11. Risks

- **R1 — Tailwind build adds a toolchain** the project has avoided (no Node today). Mitigation:
  ADR-003 + `make css-check`; keep it build-time only (no node in runtime/CI test path beyond the
  check). **Fallback:** keep hand-written CSS, drop Tailwind, keep Font Awesome + HTMX.
- **T2.6 uses the approved scope only — no DAG builder.** Scope creep is the top risk here; guard with
  the Non-goals.
- **P4 is wide.** It is deliberately many small UI tasks; split across commits per endpoint family so
  a regression is bisectable.
- **thehive4py compatibility** could be broken by the Phase P6 timeline changes — AC8.4 must stay green.
- **Font Awesome licensing** must be recorded; self-hosting does not remove attribution duties.

## 12. Decisions (resolved 2026-10-09)

- **Q1 — Assets:** self-hosted; **no CDN** at runtime. Recorded in `ADR-003`.
- **Q2 — Tailwind:** standalone **compile** pipeline (`make css` + `make css-check`); compiled
  stylesheet committed in-tree.
- **Q3 — Observable mutation:** `?force=true` with an **impact warning** that lists the other linked
  cases; without it, a multi-case mutation is refused `409`. (Phase P6, T6.2.)
- **Q4 — Tenant isolation:** **remains deferred.** Record a further deferral in
  `docs/spec/deviations.md` with the enforcement options; leave TODO §6.4 open. P6 ships only the
  timeline and observable-mutation items.
- **Q5 — `task.completed`:** confirmed *registered-but-unemitted* (deviation P12-1). **Fix it** by
  adding the `Task` receiver so the trigger is real (Phase P2, T2.0 + AC6.12-P2-d), retiring P12-1.

## 13. Implementation Brief (on approval)

Order: **P1 → P2 → P3 → P4 → P5 → P6 → P7**, each a separate commit.

- **P2 owner `django-backend` + `playbook-engineer`:** `automation/playbooks.py` (new),
  `automation/registry.py` (T2.0 `Task` receiver — closes P12-1), `automation/dispatcher.py::run_now`,
  new `automation/urls.py` (registered before `compat.urls`); `core/serializers.py`;
  `tests/conformance/test_playbook_authoring.py`; `test_authz.py`; seed migration.
  AC6.12-P2-a/b/c/d.
- **P1/P3/P4 owner `django-frontend`:** `ui/templates/ui/*`, `ui/static/vendor/*`,
  `docs/decisions/ADR-003-frontend-assets.md`, `THIRD-PARTY.md`, `ui/views.py`, `ui/urls.py`,
  `tests/conformance/test_ui_*.py`. AC6.12-P1/P3/P4.
- **P5 owner `django-backend` + `security-auditor`:** login throttle + cookie assertions.
- **P6 owner `db-postgres` (6.2/6.7) + `django-backend` (6.5):** with `security-auditor` on 6.5.
- **Verification:** `verifier` (per-phase AC), `qa-tester` (edge cases), `security-auditor` (P2/P5/P6).
  Deviations must be recorded in `docs/spec/deviations.md` and this plan updated in place.
