# PLAN — Wave 6.1: TheHive T2 API surface

- **Date:** 2026-10-09
- **Status:** Draft for approval
- **Owner:** planner (tech lead)
- **Source:** `TODO.md` §6.1 · master plan `PLAN-2026-10-03-thehive-compatible-mvp.md` §7.3 · `docs/decisions/ADR-002`
- **Related:** `docs/spec/api.md` (implemented T1 surface), `docs/spec/data-model.md`, `docs/spec/deviations.md`
- **Companion:** `tests/conformance/test_wire_boundary.py` (TODO 2.3 — the one-way rule this wave must not break)

> Implementation agents: see **§13 Implementation Brief**. Deviations from this plan must be recorded in
> the plan and in `docs/spec/deviations.md`.

---

## 1. Summary

The MVP loop and the **T1** wire surface are shipped and gated (Phases 1–11). `TODO` §6.1 is the
large remaining bucket: the **T2** endpoints named in master plan §7.3. T2 is not one task; it is
roughly a dozen feature areas. This plan turns the bucket into **five independently shippable
phases**, ordered so that each phase stands on already-built infrastructure and needs no schema
change in a later phase.

Each phase is closed only when `make check` is green, its new endpoints have conformance tests with
demonstrated failure modes (R11), and `docs/spec/api.md` + `data-model.md` + `deviations.md` are
updated in the same change.

The wave is deliberately **breadth-first per phase and lean per endpoint**: reuse the existing
renderer/`compat`/view patterns, ground contracts in the pinned TheHive golden fixtures
(`test_thehive_fixtures.py`, thehive4py 2.1.0), and add no new infrastructure that a later phase
would have to undo. New module/service files must keep TheHive field-name literals out (enforced by
the TODO 2.3 guard) — new `views.py`/`serializers.py` are inside the boundary by construction.

## 2. Goals

1. Close the T2 surface named in §7.3, in five phases (P1–P5), each a reviewable unit.
2. Keep the "compatible on the wire, clean inside" contract: no TheHive literal escapes the wire
   boundary; internal models stay Django-clean.
3. Reuse existing patterns (`core/serializers.py` renderers, `compat/` errors/auth, twin-spelled
   URL routes, the `_resolve_case` identifier rule, the org-scope guard `_case_org_scope`).
4. Keep the wave honest by evidence: every new endpoint has a conformance test whose mutation is
   demonstrated (R11), plus a golden-fixture check where thehive4py has a matching shape.

## 3. Non-goals

- **TheHive UI surface** (`ADR-002 §D12`) — GraphQL/angular UI parity is out.
- **Multi-tenancy enforcement** — tenant isolation is deferred (`TODO §6.4`, decision §3.2: single
  tenant + nullable `Organisation` FK). T2 endpoints reuse the existing org-scope guard; they do not
  introduce a tenant predicate.
- **Postgres-only indexes** (`TODO §6.2`) — GIN/INCLUDE work stays separate.
- **`task.completed` trigger emission** (`P12-1`) — a small Module D fix, folded into P2 only if it
  falls out cheaply; otherwise it keeps its own TODO entry.
- **Observable PATCH/DELETE policy** (`TODO §6.5`) — T2 adds no new destructive verb on the global
  observable; it stays behind the existing recorded decision.
- Rewriting shipped T1 endpoints or their contracts.

## 4. Current state (grounding)

**Shipped (T1):** login/logout; alert CRUD + merge/import/observable; case CRUD + task/observable/
timeline/customEvent/alert-unlink; observable/task/customEvent detail; `customField` list;
`POST /api/v1/query`; webhook ingest; `/healthz`, `/readyz`; the `ui` app.
**Renderers already present** (`core/serializers.py`): `alert_json`, `observable_json`,
`timeline_event_json`, `task_json`, `custom_event_json`, `custom_field_json`, `user_json`,
`automation_run_json`, `case_json`. **Error/auth hooks** (`compat/`) are complete.

**Models that already exist** (so these T2 areas are *endpoints + renderers + tests*, no schema
work): `AlertStatus`, `Alert`, `ObservableType`, `Observable`, `CaseStatus`, `Tag`, `CustomField`,
`Case`, `Task`, `TimelineEvent`, `CaseObservable`/`AlertObservable`, `CaseTagLink`/`AlertTagLink`/
`ObservableTagLink`, `Organisation`, `User`, `ApiKey`, `Playbook`, `AutomationRun`,
`IngestionSource`.

**Models that do _not_ exist** (schema work required): **Attachment, Page, Comment, Share,
CaseTemplate, TTP/Procedure**.

**Gaps vs §7.3:** `query` `_bulk` + `case/_merge/{ids}`; `page`, `comment`, `tag` endpoints
(model exists, endpoints don't), `shares`, `observable/type` CRUD, `case`/`alert` status CRUD,
`user`/`user/current`, `describe`, `export`, `flow`, `case template`, `taxonomy`,
`procedures`/TTP, `organisation`.

## 5. Architecture & decisions

- **Routing.** Continue the **twin-spelled** pattern (slash + no-slash) for every route that carries
  a body, and keep per-app `urls.py` mounted **before** `compat.urls`' catch-all in
  `amalthea/urls.py`. Ordering stays load-bearing (specific sub-resources above bare `<str:id>`).
- **Wire boundary.** New renderers go in `core/serializers.py` (or a per-app `serializers.py`), new
  controllers in per-app `views.py`. Both are inside the 2.3 boundary. Any new **service** module
  (`*/services.py`) must not contain TheHive literals — the guard enforces it.
- **New models** get `UUIDModel` (+ `TimeStampedModel` unless append-only; append-only link rows
  carry only `created_at` — the M7 rule). Every FK is indexed by default; do not hand-write a
  redundant index (plan §6 redundancy rule).
- **Comment model (decided 2026-10-09).** `comment` gets **its own `Comment` model** — the wire needs
  a stable comment `_id` with its own list endpoint, which `TimelineEvent` (a rendered ledger
  projection) does not provide. Record the decision in `deviations.md`: comments are a first-class
  entity, not a timeline projection.
- **Page mapping.** TheHive `page` is a rich, tabbed case document. Introduce a minimal `Page` model
  (`case`, `title`, `content` Mk, `order`, `category`) if the wire needs a stable `_id`; otherwise
  map onto a `TimelineEvent` kind. **Spike first** (see R2).
- **Org scope.** All new case/alert/observable-scoped reads and writes call the existing
  `_case_org_scope` (or its alert/observable sibling) exactly as T1 does; no new tenant predicate.
- **Storage (attachments).** Store under `MEDIA_ROOT` with generated opaque names, never the client
  filename; record the original name/content-type/size/hash on the row. Enforce a size cap before
  streaming to disk and reject path traversal.
- **Query integration.** New first-class entities (Page, Attachment, Share, CaseTemplate, TTP) must
  be reachable from `POST /api/v1/query` only if §7.3/ADR D6 requires it; default is *not* to extend
  the DSL in this wave.

## 6. Phased plan

### P1 — Identity, vocabularies & tags (no schema change)

**Scope:** `user`, `user/current`, `organisation` (read + scoped update), `observable/type` CRUD,
`case`/`alert` status CRUD, `tag` CRUD + link/unlink on case/alert/observable, `describe`.
**Why first:** all models exist; this is pure endpoints/renderers/tests and unblocks P2/P4.

**Tasks**
1. `identity/urls.py` + views: `GET user/{idOrName}`, `GET user/current`, `GET/PATCH organisation`;
   reuse `user_json`.
2. `observables/urls.py` (currently empty) + views + `observable_type_json`: full CRUD on
   `ObservableType`, with delete guarded when observables reference the type.
3. `cases/` + `alerts/` status CRUD (list/create/update/delete `CaseStatus`/`AlertStatus`), guarded
   against deleting a status in use.
4. Tag endpoints: `GET/POST tag`, `PATCH/DELETE tag/{id}`, and link/unlink routes on
   `case/{id}/tag`, `alert/{id}/tag`, `observable/{id}/tag` (link tables already exist).
5. `describe` endpoint (`compat` or `core`) returning the entity/type catalogue in TheHive's shape.
6. Mount all new URLconfs in `amalthea/urls.py` **above** `compat.urls`.

**ACs**
- **AC6.1-P1-a** Every route answers both spellings where a body is sent; `POST` with a trailing
  slash is not 301'd into a body-loss.
- **AC6.1-P1-b** A status/type/tag in use cannot be deleted (409/400 with a TheHive error envelope),
  proven by a **mutation guard** that deletes the reference first and asserts the guard then allows it.
- **AC6.1-P1-c** `describe` and `user/current` match a pinned TheHive fixture subset.
- **AC6.1-P1-d** `make check` green; `tests/conformance/test_t2_p1_surface.py` added; no new file
  outside the wire boundary contains a T2 wire literal (2.3 guard green).

### P2 — Collaboration: pages, comments, shares, flow

**Scope:** `page` (case pages), `comment` (case/alert/task), `shares` (case/alert × organisation
with read/write perms), `flow` (read-only case-flow view).
**Why second:** the collaborative ledger is Module B; pages/comments/shares are what analysts touch.

**Tasks**
1. Implement the `Comment` model (own model — decided §5) + its CRUD/list endpoints. Spike and decide
   the `Page` shape, then implement `Page` (`case`, `title`, `content` Mk, `order`, `category`).
2. `cases/urls.py` + views for `page` CRUD and `comment` create/list; emit realtime events via the
   existing `realtime.publisher` choke point so the UI stays synced (Module B requirement).
3. New `Share` model (`case|alert`, `organisation`, `permissions`) + CRUD endpoints; **default deny**
   for shares management unless the caller owns/administers the case.
4. `flow` read-only endpoint returning the case's linked entities in TheHive's flow shape.
5. Migrations for `Page` (+ `Share`); update `data-model.md`.

**ACs**
- **AC6.1-P2-a** Page/comment create produces a WebSocket event; a conformance test proves an
  unauthenticated subscriber cannot join the case group.
- **AC6.1-P2-b** Share permissions are enforced: a read-only share cannot write — failure mode
  demonstrated.
- **AC6.1-P2-c** `page`/`comment`/`flow` shapes match pinned fixtures; `make check` green with
  `tests/conformance/test_t2_p2_surface.py`.

### P3 — Attachments

**Scope:** `Attachment` model + `POST /case/{caseId}/attachments` (multipart) + download + delete.

**Tasks**
1. `Attachment` model (case FK, original name, content type, size, sha256, `created_by`, storage
   path); migration.
2. Upload view: size cap **before** disk write, content-type allow-list + sniff check, opaque
   stored filename, hashed content for dedupe/idempotency; download streams with the original name.
3. Authorization: only org-scoped case members may upload/download; `@csrf_exempt` only where the
   TheHive contract requires it, otherwise session/CSRF-safe.
4. Update `deviation`/`data-model` docs; add `test_t2_p3_attachments.py`.

**ACs**
- **AC6.1-P3-a** Oversize body ⇒ 413; wrong content type ⇒ 415; traversal filename ⇒ stored sanitised
  (never escapes `MEDIA_ROOT`) — three failure modes demonstrated.
- **AC6.1-P3-b** Download returns the original filename + content type and the recorded sha256
  matches the bytes.
- **AC6.1-P3-c** Cross-org upload/download is rejected (401/403) — demonstrated.

### P4 — Bulk, merge, templates & taxonomy

**Scope:** `query` `_bulk` patches; `POST /case/_merge/{ids}`; `case/template` CRUD; `taxonomy`.

**Tasks**
1. `_bulk` support in `query/engine.py` (+ `query/views.py`), reusing the existing patch semantics;
   cover both create and update bulk operations per ADR D6.
2. `case/_merge/{ids}` — merge N cases into one target with an audit ledger entry per merged case and
   a deterministic observable/task re-parenting rule.
3. `CaseTemplate` model (fields + task templates + custom-field defaults) + CRUD; migration.
4. `taxonomy` endpoint aggregating observable types + statuses + templates (read model).
5. Extend `test_thehive_fixtures.py` coverage for the bulk shapes.

**ACs**
- **AC6.1-P4-a** `_bulk` is transactional per item: one failing item does not roll back the batch; the
  response reports per-item status — failure mode demonstrated.
- **AC6.1-P4-b** `case/_merge` is idempotent on replay and writes provenance to the timeline.
- **AC6.1-P4-c** Creating a case from a template applies its task/custom-field defaults — demonstrated.

### P5 — Reporting & TTP

**Scope:** `export` (case/alert export in TheHive's shape), `procedures`/TTP entities.

**Tasks**
1. TTP/Procedure model (name, TTP code, tactic/technique, case link) + CRUD; migration.
2. `export` view(s) producing the TheHive export envelope, **including `raw_payload`** (full
   fidelity; the endpoint is authenticated and org-scoped). This is a deliberate exception to the
   "`raw_payload` is never inlined" rule — record it in `deviations.md`.
3. Any remaining §7.3 stragglers discovered during P1–P4 (tracked as they appear).

**ACs**
- **AC6.1-P5-a** Export round-trips through the pinned fixture schema; a case with a `raw_payload`
  exports it verbatim, and an unauthorised caller cannot export the case at all — demonstrated.
- **AC6.1-P5-b** TTP CRUD guarded by the same in-use rule as P1 vocabularies.

## 7. Cross-cutting work (applies to every phase)

- **Realtime:** every T2 mutation that changes case/alert state emits an event through
  `realtime.publisher` (Module B: "every comment, task assignment, and state transition must emit an
  event").
- **Errors:** all failures use `compat/errors.py` envelopes; 400 (never 401) for login-shaped
  failures; unknown fields ignored (ADR D11).
- **Unknown-field tolerance & fixtures:** extend `test_unknown_fields.py` and
  `test_thehive_fixtures.py` per phase.
- **Docs in the same change:** `docs/spec/api.md` (new tables), `data-model.md` (new models),
  `deviations.md` (any divergence), `TODO.md` (mark phase done), `COMPLETED.md` (dated entry).

## 8. Security

- Reuse `compat/auth.py` (Bearer/Basic/session) and the existing throttles; new write endpoints are
  authenticated and org-scoped like T1.
- Attachments (P3) are the highest-risk surface: size cap before write, content sniff, opaque stored
  names, no traversal, authorization on both upload and download.
- Shares (P2) are default-deny; a share never widens access beyond the org scope of the caller.
- No secrets in fixtures/tests (the secret-scan hook runs on every commit; generated tokens only).
- No new eval/exec; TTP/automation inputs stay data, not code.

## 9. Testing & Definition of Done

- **Per phase:** a new `tests/conformance/test_t2_pN_*.py` (or per-area names) with **demonstrated
  failure modes** (R11): mutate the guard's assumption and assert the failure.
- **Fixtures:** ground each shape in the pinned thehive4py 2.1.0 golden fixtures where one exists;
  otherwise record the shape decision in `deviations.md`.
- **Gate:** `make check` (ruff · ruff-format · mypy strict · `manage.py check` · migration-sync ·
  pytest) green on SQLite; Postgres suite for anything touching constraints/indexes.
- **Standards:** `make check` stays the single DoD (TODO 8.3); mutation guards follow
  `tests/conformance/_mutation.py` (TODO 8.1); the wire-boundary guard (TODO 2.3) stays green.

## 10. Token-efficiency strategy (explicit)

1. **Ground in fixtures, not upstream source.** Implement shapes from `test_thehive_fixtures.py`
   and `docs/spec/api.md`; do not read TheHive's Java/scala source.
2. **Reuse, don't re-architect.** New endpoints copy the nearest T1 view + renderer; no new
   framework, no generic CRUD abstraction, no new app unless a model/app boundary demands it.
3. **One phase per commit series; one focused test file per phase.** Avoid whole-suite runs during
   development — run the phase's test file, then a single `make check` before the phase close.
4. **No subagent swarm.** Use a subagent only for a genuinely independent, expensive task (e.g., the
   P2 page spike), with a tight brief that demands the report in its final message
   (TODO 7.3).
5. **Spike only where the wire is ambiguous** (P2 page shape). Everywhere else, the T1
   pattern already answers the question.
6. **Batch the docs.** Update `api.md`/`data-model.md`/`deviations.md`/`TODO`/`COMPLETED` once per
   phase, at close, not per endpoint.

## 11. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Scope creep — T2 is a bucket; "one more endpoint" compounds | High | High | Five phases, each closable independently; anything new becomes its own phase/TODO. |
| R2 | Page wire shape guessed wrong | Medium | Medium | Time-boxed P2 spike against fixtures + thehive4py; record the decision. (Comment decided: own model.) |
| R3 | Attachments introduce a real security hole | Medium | High | Content/size/path controls + authz tests with demonstrated failures (P3). |
| R4 | New services leak TheHive literals | Low | Medium | TODO 2.3 guard fails the build; keep literals in renderers/views only. |
| R5 | Migration churn across phases | Medium | Low | Design each model to stand alone; no later-phase dependency on an earlier schema. |
| R6 | Fixture coverage thinner than assumed | Medium | Medium | Where no fixture exists, record the shape decision in `deviations.md` and test it. |

## 12. Resolved decisions & open questions

**Resolved 2026-10-09 (product owner):**
1. **Q1 — `comment`: own model.** A first-class `Comment` with a stable `_id` and its own list
   endpoint; not a `TimelineEvent` projection. Folded into P2.
2. **Q2 — no query-DSL extension** for the new entities in this wave. `POST /api/v1/query` keeps its
   current scope.
3. **Q3 — `organisation`: read + scoped update** of the caller's own org (single-tenant decision
   §3.2 stands; no tenant predicate).
4. **Q4 — `export` includes `raw_payload`** (full fidelity; authenticated + org-scoped). Recorded as a
   deliberate exception to the "`raw_payload` is never inlined" rule.

**Still open:**
5. **Q5** Ordering — is P1→P5 right, or does attachments/export have an external deadline? (Confirm
   with product.)

## 13. Implementation Brief

**Handoff to `django-backend` / `django-frontend` (per phase).**

- **Start with P1.** It has no schema change and exercises every pattern the later phases reuse.
- **Files to touch (P1):** `identity/urls.py` (new) + `identity/views.py`; `observables/urls.py`
  (fill) + `observables/views.py`; `cases/views.py` + `cases/urls.py`; `alerts/views.py` +
  `alerts/urls.py`; `core/serializers.py` (new `observable_type_json`, status/tag renderers);
  `amalthea/urls.py` (mount new URLconfs before `compat.urls`); `tests/conformance/test_t2_p1_surface.py`.
- **Acceptance criteria:** AC6.1-P1-a … -d above. Each AC must be backed by a test whose failure
  mode is demonstrated.
- **Constraints:** twin-spelled routes; org-scope like T1; no TheHive literals outside the wire
  boundary (2.3 guard); reuse `compat/errors.py`; emit realtime events for state changes.
- **Definition of done:** `make check` green; docs updated in the same change; `TODO` §6.1 annotated
  with the shipped phase.
- **Deviation rule:** any divergence from this plan is recorded in this plan **and**
  `docs/spec/deviations.md` before the phase is marked done.

## 14. Sequencing recommendation

1. Land P1 (fast, no schema).
2. Land P2 (collaboration — highest product value; needs the P2 page spike).
3. Land P3 (attachments — highest security risk; do it while the authz patterns are fresh).
4. Land P4 (bulk/merge/templates — builds on the query engine).
5. Land P5 (export/TTP — reporting close-out).

Each phase is independently valuable and independently shippable. `TODO` §6.1 is marked `[x]` only
when all five phases (or an explicitly agreed subset) are shipped, with the remainder split into
their own TODO entries.

## 15. References

- `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §7.3 (T2 scope), §6 (index rules)
- `docs/decisions/ADR-002` (D6 query, D9 namespace, D11 tolerance, D12 non-goals)
- `docs/spec/api.md` (shipped T1 surface), `docs/spec/data-model.md`, `docs/spec/deviations.md`
- `tests/conformance/test_thehive_fixtures.py` (pinned thehive4py 2.1.0 shapes)
- `tests/conformance/test_wire_boundary.py` (TODO 2.3 boundary this wave must respect)
- `tests/conformance/_mutation.py` + `test_mutation_standard.py` (TODO 8.1/8.2)
