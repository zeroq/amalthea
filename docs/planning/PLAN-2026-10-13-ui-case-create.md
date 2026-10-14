# PLAN — Create a case manually from the UI (`TODO` §10.2)

- **Date:** 2026-10-13
- **Status:** Implemented (P1–P5) · independently verified (**Partial**) · security-reviewed (**0 Critical / 0 High**) · post-review **M1** regression + **L1** gating fixed (2026-10-13); owner answered §10 with "go with your recommendations"; deviations recorded in §0 and `docs/spec/deviations.md` (**W102-1…W102-7**)
- **Owner:** planner
- **Related:** `AGENTS.md` §2 Module B, §3, §6; `ADR-002` §D11 (status tolerance),
  `ADR-004` (tenant model), `ADR-006` (viewer role); `docs/reviews/REVIEW-2026-10-11-p2-ui-mutations.md`
  (the P2 mutation pattern this follows); `TODO.md` §10.2, §12.1; `docs/verification/VERIFY-2026-10-12-viewer-role-enforcement.md`

---

## 0. Implementation outcome and deviations (2026-10-13)

Owner answers to §12: **all recommendations accepted** — Q1 extraction default on, Q2 add the `n`
shortcut + rail entry, Q3 extend the create payload, Q4 default status `InProgress`, Q5 audit action
`case.created`, Q6 no default flag. Three deviations emerged during implementation and are recorded
here (canonical table: `docs/spec/deviations.md` → **Wave 10.2**, ids **W102-1…W102-5**):

1. **W102-2 — the extraction checkbox was dropped; extraction is unconditional.** The plan assumed the
   service would take an `extract: bool` (§4, §7 Q1, T1.1). Reading the code showed the *existing*
   `_create_case` already ran `extract_into_case` over title + description on **every** create, and a
   payload `extract`/`observables` pair only *adds* explicit strings to the corpus. Keeping the API's
   recorded behaviour meant `create_case` has **no `extract` parameter** and the UI shows a hint line
   rather than a checkbox. This supersedes plan §4's Extraction row, §7's checkbox row, T1.1's
   `extract=False` signature, **P1-f**, and **P2-i**.
2. **W102-3 — stricter enum validation on create.** `severity`/`tlp`/`pap` that are non-integer or
   out-of-range are now a clean 400 (previously `severity: 0`/empty silently defaulted to 2, and an
   out-of-range value could reach a DB `CHECK` as a 500). Empty/null still means "model default"
   (ADR-002 §D11 tolerance). This is a deliberate, documented behaviour change on `POST /api/v1/case`.
3. **W102-1 — one shared service.** `cases.create.create_case` is called by both `cases/views.py::_create_case`
   and `ui/views.py::case_create`; recorded as the create-side twin of `W616P2-3`.

Also recorded: **W102-4** no `case.created` automation trigger was added (plan §3 non-goal — indexed
creation still drives Module D for free because extraction fires `observable.created`), and
**W102-5** `CaseUpdateError` was not renamed to `CaseWriteError` (churn without benefit).

Delivered artefacts: `cases/create.py`; `cases/update.py::_set_case_fields(warnings=…)`;
`cases/views.py::_create_case`; `ui/views.py::case_create` + `_render_case_create_form`; `ui/urls.py`
(`cases/new`); `ui/templates/ui/case_create.html`; `ui/templates/ui/case_list.html` affordances;
`ui/static/ui/keys.js` + `ui/templates/ui/base.html` (`n` shortcut + rail + cheat-sheet);
`tests/conformance/test_ui_case_create.py` (41 cases) plus the `test_tenant_scoping.py` /
`test_viewer_readonly_ui.py` route classifications; `docs/spec/api.md`; `docs/spec/deviations.md`.

### Post-verification (2026-10-13): defects found and closed

Independent verification (`docs/verification/VERIFY-2026-10-13-ui-case-create.md`, verdict **Partial**)
and the security review (`docs/reviews/REVIEW-2026-10-13-ui-case-create.md`, **0 Critical / 0 High**) ran
after P5. Every "not met" AC and both review findings were then closed:

- **M1 (Medium, fixed — a regression from this refactor).** `POST /api/v1/case` with `status: ""` or
  `null` answered **500** where the pre-refactor wrapper (`str(payload.get("status") or "InProgress")`)
  defaulted. `_create_scalars` now omits a falsy `status`, so the empty value never reaches
  `resolve_case_status` (which `get_or_create`d a `CaseStatus` whose `stage=""` violates the
  `case_status_stage_valid` `CHECK`). Re-pinned by 3 regression tests; `docs/spec/api.md` updated.
- **L1 (Low, fixed — a Layer-3 miss).** The `New case` rail link and its cheat-sheet row rendered for
  viewers (they 404); both are now `can_mutate`-gated. The raw `n` shortcut cannot be role-gated in a
  static CSP-allowed JS file and stays, matching the existing `,` → Settings precedent (**V7**).
  Recorded as **W102-6**.
- **W102-7 (Info, accepted).** `POST /api/v1/case` writes no `AuditLog` row while the new UI path writes
  `case.created` — a pre-existing asymmetry, recorded not fixed.
- **Verifier not-met items closed.** T4.5b (the security review) and T5.2–T5.4 (TODO §10.2 marked DONE,
  the priority row removed and the table renumbered, `COMPLETED.md` 2026-10-13 entry) are now done, and the
  *unasserted edge ACs* are now **pinned by tests** (P2-f viewer affordance-absence, P2-g anonymous→login,
  T1.2/P2-d malformed + out-of-range enums, P2-a labels/`maxlength="500"`, P2-c scalar echo, T1.3b/T2.5
  auto-created-status warnings, §9 mass-assignment on the UI **and** API paths, §13.2 full-field UI
  success) — 13 new nodes, taking `test_ui_case_create.py` to 41.
- Gate after fixes: `make check` **1662 passed / 3 skipped** (41 nodes in `test_ui_case_create.py`).

---

## 1. Summary

Today a `Case` only comes into existence by promoting an alert:
`ui/views.py::alert_escalate` → `alerts/escalation.py::import_alert_to_case`. The **API** can already
create one directly (`POST /api/v1/case` → `cases/views.py::_create_case`, serving TheHive's
`case` collection), but the analyst UI has **no `ui-case-create` route** — an incident that arrives by
phone, email or ticket cannot be documented in Amalthea at all.

This plan adds `/cases/new` (GET form + POST create) **and** extracts the case-construction logic into a
single shared service so the API and the UI cannot drift — the same decision the P2 review forced for
*updates* (`cases/update.py::apply_case_update`, deviation `W616P2-3`).

No schema change, no migration: `Case.number` is already allocated by `Case.save()`, and
`summary`/`flag`/`tlp`/`pap`/`tags` already exist.

## 2. Goals

- G1 An analyst with the `analyst` role or above can open a case from the UI in one screen, with the
  fields the case model already supports.
- G2 The created case is indistinguishable from an escalated one: same `owner_org` rule, same
  `Case.number` allocation, same `case-created` ledger entry, same audit row, same WS publish.
- G3 One definition of "how a case is constructed", used by both `POST /api/v1/case` and `/cases/new`.
- G4 The viewer guarantee holds without a new mechanism: a viewer never sees the affordance and gets a
  uniform 404 on POST (ADR-006).
- G5 The Module D loop keeps working for free: if extraction is on, linked observables fire
  `observable.created`, playbooks run, and their output lands on the new case's timeline.

## 3. Non-goals

- **No `case.created` automation trigger.** `automation/dispatcher.py::TRIGGER_EVENTS` is
  `observable.created`, `alert.ingested`, `case.status_changed`, `task.completed`. Adding a fifth trigger
  would touch the dispatcher, the playbook authoring UI, `validate_config`, the docs and its own tests —
  a separate plan. (Extraction firing `observable.created` *is* in scope, and is the loop an analyst
  actually sees.)
- **No case-detail tabs, no classifier fields** (`scope`/`type`/`data-classification`/`phase`) — those
  are §10.3–10.7 with open questions Q-D1…Q-D5.
- **No cross-org create.** `owner_org` is the actor's org (ADR-004); superAdmin cross-org *writes* are
  not a supported shape today and stay unsupported.
- **No bulk create**, no template-driven prefill beyond what already exists (`ui-case-apply-template`
  stays a case-detail action).
- **No live list update** (§6.16 P7 realtime breadth) and **no pagination** (§12.1) — a create redirects
  to the case detail page, where the ledger is live.
- **No HTMX requirement.** The form is a plain POST (degrade-first); `hx-*` may be added with P7.

## 4. Architecture

**No new component, no new dependency, no new ADR.** This is:
1. a new request-free service module `cases/create.py` (mirrors `cases/update.py`), and
2. a new server-rendered view + template + route, and
3. two delegations (the API's `_create_case` and the new UI view call the same service).

| Decision | Choice | Why |
|---|---|---|
| Where the construction logic lives | `cases/create.py::create_case()` | `cases/update.py` already proves the pattern: one applier, two surfaces, no drift (deviation `W616P2-3`). |
| Error type | reuse `cases.update.CaseUpdateError` | It already carries the exact `{type, message, fields}` payload the API returns and the UI turns into a form error. Its name is now historical — recorded as a deviation rather than churning three modules; a rename to `CaseWriteError` is a §2.5-style cleanup. |
| Field-set definition | reuse `CASE_SCALAR_FIELDS` + `_set_case_fields` + `_apply_case_tags` | Adding a field later then lands on create, update, bulk and UI at once. |
| Status validation | tolerant in the service (`resolve_case_status`, ADR-002 §D11), **bounded in the UI** before the call | The P2 decision verbatim: the API stays permissive, the form cannot mint a status. |
| Assignee resolution | `_resolve_login(value, org=...)` (login, then UUID, org-scoped) | Same call as `case_edit`/`apply_case_update`; a cross-org login is refused, never silently bound (SEC-2026-10-11 M1). |
| Transaction | `@transaction.atomic` on `create_case` | Today `_create_case` is not atomic: a failure between the row and the ledger entry leaves a caseless ledger (or a warning-less case). |
| Extraction | ~~explicit `extract: bool`; UI checkbox default on~~ **SUPERSEDED — W102-2**: extraction is unconditional; no `extract` parameter, no UI checkbox | TheHive's `POST /case` does not auto-extract, so the API keeps its current contract; the UI is the ergonomic surface and extraction is idempotent + org-scoped. |

## 5. Data model

**No changes, no migration.** Fields used by the form, all pre-existing:

| Field | Notes |
|---|---|
| `number` | `AllocatedNumberField`; allocated in `Case.save()` (`cases/numbering.py`). Never supplied by the caller. |
| `title` (≤500) | Required, trimmed, 500-truncated post-trim (the API's existing rule: `title[:500]`). |
| `description` / `summary` | Markdown / list summary. |
| `severity` / `tlp` / `pap` | `SmallIntegerField` with CHECK constraints (`core/enums.py`); range-validated in the service so out-of-range is a 400/form error, not an `IntegrityError` 500. |
| `status` | FK → `CaseStatus`, tolerant resolve; seeded rows `New`, `InProgress`, `Contained`, `Closed`. Default `InProgress` (matches the API today). |
| `assignee` | FK → `User`, org-scoped; `""` ⇒ unassigned. |
| `tags` | M2M through `CaseTagLink`; comma-separated in the form, created on attach (existing `_apply_case_tags`). |
| `flag` | `BooleanField`; checkbox. |
| `start_date` | `timezone.now()` (the escalation path uses the alert's date; a manual case has no source event). |
| `owner_org` | **Mandatory** = `request.user.org`. No org ⇒ the form renders an explanatory state and POST refuses (mirrors the API's 403). |
| `closed_date` | Stamped by `Case.stamp_closed_date()` if the chosen status is in the `Closed` stage — no special handling. |

## 6. API contract

`POST /api/v1/case` (`cases/views.py::case_collection`) keeps its route, response shape and status codes,
and gains the fields the UI needs:

| Input | Today | After |
|---|---|---|
| `title` | required, 400 `{type,message,fields}` if blank | unchanged |
| `description` | accepted | unchanged |
| `severity` | accepted, `int(... or 2)` | unchanged default, now range-validated |
| `status` | accepted, tolerant, default `InProgress` | unchanged |
| `extract` / `observables` | opt-in extraction | unchanged |
| `summary`, `tlp`, `pap`, `flag`, `tags`/`addTags`/`removeTags`, `assignee` | **ignored** | accepted via the shared service |
| actor with `org = None` | 403 | unchanged (403) |
| response | `201` `case_json(detail=True)` | unchanged |

**Also unchanged:** unknown keys stay ignored (`test_unknown_fields.py` tolerance), `case_json` is not
touched, and `GET /api/v1/case` is untouched.

`docs/spec/api.md` gets the accepted-keys row updated (one table).

## 7. UI / UX

**Route:** `path("cases/new", views.case_create, name="ui-case-create")` — registered **above**
`path("cases/<str:case_id>", …)` (`ui/urls.py:21`), exactly as `case-templates/new` and `tags/new` are.
Without that ordering `/cases/new` resolves to `case_detail` with `case_id="new"` → 404.

**Entry points:**
- `case_list.html` `{% block actions %}` — a `New case` button, inside `{% if can_mutate %}`.
- `case_list.html` empty state — a call to action instead of the bare "No cases" line.
- Keyboard (Q2): `n` → `/cases/new`, added to **all three** shortcut artefacts — `ROUTES` +
  `CHEATSHEET` in `ui/static/ui/keys.js`, the rail link in `base.html`, and the server-rendered
  cheat-sheet table — because `tests/conformance/test_ui_nav.py::test_rail_and_routes_cover_the_same_keys`
  asserts rail keys ≡ `ROUTES` keys, and `test_cheatsheet_documents_exactly_the_bound_keys` asserts every
  bound key has a row.

**Form (`ui/case_create.html`, mirroring `ui/case_edit.html`):**

| Control | Default |
|---|---|
| Title (text, `required`, `maxlength=500`) | empty |
| Description (textarea, Markdown) | empty |
| Summary (textarea) | empty |
| Severity / TLP / PAP (selects, `SEVERITY_CHOICES` / `TLP_CHOICES` / `PAP_CHOICES`) | 2 / 2 / 2 |
| Status (select over the `CaseStatus` vocabulary) | `InProgress` |
| Assignee (select, users in the actor's org, `""` = Unassigned) | Unassigned |
| Flag as important (checkbox) | off |
| Tags (text, comma-separated) | empty |
| Extract observables from the title and description | **always on** — no checkbox (SUPERSEDED — W102-2) |
| Create case (submit) · Cancel (`ui-case-list`) | |

**Behaviour:**
- `GET` → 200. `can_mutate` guards the button; a viewer never reaches the view (`@not_viewer` → 404).
- POST success → `messages.success(f"Case {case.number} created.")` → **302 to `ui-case-detail`**
  (the ledger is where the analyst wants to be, and it is the page with the live WS room). Warnings from
  the service (e.g. an auto-created status) are flashed as `messages.warning`.
- POST invalid → **200 re-render** with `messages.error` and the submitted values echoed (read via
  `QueryDict.get()` scalars, never `dict(...)` — the P2 `N1` lesson), no case created.
- `owner_org` missing (superAdmin without an org): GET renders 200 with the form replaced by an
  explanation; POST flashes an error and redirects to `ui-case-list`. Never a 500.
- Accessibility: visible `<label for>` on every control, no inline `style=`/`<script>`/`on*=`
  (ADR-003 / CSP `'self'`), error text through the existing `messages` region, and no native dialogs
  (nothing destructive here).
- Viewer: the button is hidden by `can_mutate` **and** POST is 404 — Layer 1/2/3 of ADR-006, unchanged.

## 8. Phases, tasks and acceptance criteria

### P1 — Shared creation service (`cases/create.py`)

- [ ] T1.1 Add `create_case(payload, *, actor, org) -> tuple[Case, list[str]]` (**no `extract` param — W102-2**),
      `@transaction.atomic`, reusing `_set_case_fields`, `_apply_case_tags`, `resolve_case_status`,
      `append_timeline_event` (kind `case-created`).
  - **AC1.1.1** A blank/whitespace title raises `CaseUpdateError` and writes nothing (no `Case` row).
- [ ] T1.2 Range-validate `severity`/`tlp`/`pap`; a non-integer or out-of-range value raises before any
      write.
- [ ] T1.3 Resolve the assignee through `_resolve_login(value, org=org)`; `""`/`None` ⇒ unassigned.
- [ ] T1.3b Store `resolve_case_status` warnings on `case.ingestion_warnings` (as
      `import_alert_to_case` does) and return them.
- [ ] T1.2 AC: `severity=9`, `severity="x"`, `tlp=99` ⇒ error, **no** `Case` row and no partial ledger row.
- [ ] T1.4 Point `cases/views.py::_create_case` at the service; keep the 201/400/403 shapes.

**AC (P1)**
- **AC1.1** `create_case` returns the case plus warnings; `Case.number` is allocated; `owner_org is org`.
- **P1-a** `POST /api/v1/case {"title": "X", "tlp": 3, "pap": 1, "severity": 4, "summary": "s",
  "flag": True, "tags": ["a", "b"], "assignee": "<own-org login>"}` → 201, and the row has those values,
  the two tags linked and the assignee bound. (Today all of them are silently dropped.)
- **P1-b** `POST` with a blank title → 400 with the exact existing body `{type, message, fields}`.
- **P1-c** `POST` as a user with `org = None` → 403, unchanged.
- **P1-d** `POST` with `severity: 9` → 400 (not a 500) and no case row.
- **P1-e** `POST` with an unknown field (e.g. `"nonsense": 1`) → still 201 (`test_unknown_fields.py`
  stays green).
- **P1-f** ~~The API default is unchanged~~ **SUPERSEDED — W102-2/W102-3**: a bare `{"title": "X"}` creates `InProgress`, severity 2,
  tlp/pap 2, no tags/assignee, `ingestion_warnings == {}`, and does **not** extract observables.

### P2 — UI view, route, template, entry points

- [ ] T2.1 `path("cases/new", …)` above `cases/<str:case_id>`; view `case_create` with
      `@not_viewer @login_required @require_http_methods(["GET","POST"])`.
- [ ] T2.2 `ui/case_create.html` (form table in §7) + `form_values` echo helper reusing the
      `case_edit` shape (`_render_case_create_form`).
- [ ] T2.3 `New case` button + empty-state CTA in `case_list.html`, both inside `can_mutate`.
- [ ] T2.4 Audit: `AuditLog.objects.create(..., action="case.created", target_type="Case",
      target_id=str(case.id), target_org=org)` **inside** the same transaction as the mutation
      (the P2 L2 rule: a failed audit insert rolls the change back).
- [ ] T2.5 Flash the service's warnings (`messages.warning`) after success.

**Acceptance criteria:**
- **P2-a** An analyst GETs `/cases/new` → 200, all eleven controls present with labels, title marked
  `required`, and the CSRF token present.
- **P2-b** POST with a valid body → 302 to `ui-case-detail` for the new number; the case exists with
  `owner_org = request.user.org`, a timeline entry titled "Case created" (kind `case-created`, actor =
  the analyst) and an `AuditLog` row `action="case.created"` with the right `target_org`/`target_id`.
- **P2-c** POST with a blank title → 200 (re-render), an error message, **no** case row, and the typed
  values echoed (not `['x']` list rendering).
- **P2-d** POST with an unknown status, or `severity=99`, or `tlp="abc"` → 200 + error, no case row,
  **no** new `CaseStatus` row auto-created (the UI bounds the vocabulary), no 500.
- **P2-e** POST with an assignee who exists in **another** org (login or UUID) → 200 + error, no case,
  no cross-org binding (SEC-2026-10-11 M1 regression guard).
- **P2-f** A **viewer** POSTs the same URL → 404; the `New case` button is absent from the case list for
  a viewer ([ADR-006]; the enumeration sweeps in `test_viewer_readonly_ui.py` also pick the route up
  automatically).
- **P2-g** Unauthenticated GET → redirect to login.
- **P2-h** A user with `org = None` GETs `/cases/new` → 200 with an explanatory state and no form; POSTs
  → redirect with an error and no case row.
- **P2-i** **SUPERSEDED — W102-2** (no checkbox; extraction is always on): a description containing `evil.example` and a hash links an
  `Observable` to the new case **and** dispatches `observable.created` (assert the `AutomationRun`, or the
  observable link, from the MVP-loop pattern in `test_mvp_loop_automation.py`); with it off, nothing is
  extracted.
- **P2-j** The route does not shadow the case detail: after creating case `#7`,
  `reverse("ui-case-detail", args=[7])` and `/cases/7` resolve to the case, while `/cases/new` resolves to
  `ui-case-create`.
- **P2-k** `tests/conformance/test_ui_urls_smoke.py` stays green (the new `{% url %}` references resolve
  and the page does not 500).

### P3 — Keyboard + navigation (only if Q2 = yes)

- [ ] T3.1 `{ key: "n", label: "New case" }` in `CHEATSHEET`, `n: "/cases/new"` in `ROUTES`, a rail link
      `<a href="{% url 'ui-case-create' %}">… <span class="k">n</span> New case</a>`, and the matching
      server-rendered cheat-sheet row.
- **P3-a** `tests/conformance/test_ui_nav.py` passes unmodified (rail ≡ `ROUTES`, cheatsheet ≡ bound
  keys, labels match character-for-character, the rendered dashboard contains `<kbd>n</kbd>`).
- **P3-b** `n` is not bound while a text field/textarea/select has focus (the existing `isTyping` rule;
  assert by inspection of `keys.js` + the existing test's coverage).

### P4 — Conformance test updates (forced by the enumeration traps)

- [ ] T4.1 `tests/conformance/test_tenant_scoping.py`: add `"ui-case-create"` to the read-sweep
      `list_routes` set. It is discovered as a GET-capable non-global UI route, so
  `test_cross_org_ui_read_returns_404` and `test_superadmin_cross_org_ui_read_succeeds` will exercise it:
  cross-org GET must be 200 (a create form is org-independent), own-org 200, superAdmin 200.
  Do **not** add it to `_UI_WRITE_ONLY_ROUTES` (it serves GET) and do not hide it in
  `_UI_GLOBAL_READ_ROUTES` (that would drop it from conformance).
- **P3-b/P4-a** `test_tenant_scoping.py` is extended, not loosened: the route is asserted to answer 200
  for own-org, cross-org and superAdmin GETs.
- [ ] T4.2 Add `"ui-case-create"` to `_MUTATION_ROUTE_SAMPLES` in `test_viewer_readonly_ui.py`.
- [ ] T4.3 New `tests/conformance/test_ui_case_create.py` covering P1-a…f, P2-a…k, plus:
  - **P4-b** POST without a CSRF token (an `enforce_csrf_checks=True` client) → 403 and no case row.
  - **P4-c** Two POSTs with the same title create two cases with **different** `number`s (allocation is
    not title-keyed).
  - **P4-d** `title` of 600 chars is truncated to 500 on the created row and does not raise.
- [ ] T4.4 `docs/spec/api.md`: add the accepted create keys row.

### P4b — Independent verification + security review (mandatory, per project convention)

- [ ] T4.5 `verifier` subagent: `docs/verification/VERIFY-2026-10-13-ui-case-create.md`, every AC
      met/not-met/deviated.
- [ ] T4.5b `security-auditor`: `docs/reviews/REVIEW-2026-10-13-ui-case-create.md` — focus: viewer
      bypass, cross-org assignee/tags, org=None path, audit-in-transaction, CSRF, mass-assignment via
      the newly accepted API keys (`tlp`/`pap`/`flag`/`tags` are attacker-controlled in the API — confirm
      nothing reaches a field a caller must not set, e.g. `number`, `owner_org`, `closed_date`).

### P5 — Docs

- [ ] T5.1 `docs/spec/deviations.md`: a `Wave 10.2` entry (shared service; API gains fields;
  `CaseUpdateError` name retained; no `case.created` trigger; UI extraction default).
- [ ] T5.2 `TODO.md`: close §10.2 (with a pointer to the plan + verification), point §12.1 (pagination) at
      the case list as the first target.
- [ ] T5.3 `COMPLETED.md`: a dated entry with the `make check` counts.
- [ ] T5.4 `docs/spec/deviations.md`/`TODO` cross-references updated if the shortcut (P3) lands.

## 9. Security

| Risk | Control |
|---|---|
| Viewer writes a case | `@not_viewer` → uniform 404; `can_mutate` hides the affordance; two enumeration sweeps. |
| Cross-tenant data injection (assignee/tags) | Assignee resolved via `_resolve_login(..., org=...)`; `owner_org` is the actor's org, never caller-supplied. Tags are a global vocabulary (intended, §10.7/`ui-tag-*`). |
| Field injection via the API | The service never accepts `number`, `owner_org`, `closed_date`, `end_date` — only `CASE_SCALAR_FIELDS` + `assignee` + tags + `status`. Pinned by a test that posts those keys and asserts they are ignored. |
| `IntegrityError` 500 from out-of-range enums | Range validation in the shared service (the `SEC-2026-10-11 L1` fix, reused). |
| Unaudited mutation | `AuditLog` row in the same transaction (`SEC-2026-10-11 L2` rule). |
| Partial write (case without ledger entry, or vice versa) | `@transaction.atomic` on the service. |
| Log injection / XSS | No user text is logged; Markdown stays sanitized (`ui/markdown.py` + `nh3`, ADR-005); no inline assets (ADR-003). |
| CSRF | The form carries `{% csrf_token %}`; an `enforce_csrf_checks` test asserts 403 without it. |
| Brute-force / rate limiting | Out of scope, unchanged (`TODO` §11 priority 12, S3). |

## 10. Testing

- **New:** `tests/conformance/test_ui_case_create.py` (~20 cases: P1-a…f, P2-a…k, P4-b…d).
- **Updated:** `tests/conformance/test_tenant_scoping.py` (read-sweep classification),
  `tests/conformance/test_viewer_readonly_ui.py` (mutation sample), `docs/spec/api.md`.
- **Regression watch (must stay green untouched):** `test_unknown_fields.py`, `test_mvp_loop_automation.py`,
  `test_ui_urls_smoke.py`, `test_ui_nav.py`, `test_enum_contracts.py`, `test_ui_p1_wiring.py`,
  `test_ui_p2_wiring.py`, `test_viewer_reads.py`.
- **Gate:** `make check` (ruff + format + mypy strict + migration check + css-check + full suite).
  Expected delta: ~+20 passed, no skips added.

## 11. Risks

| Risk | Mitigation |
|---|---|
| `/cases/new` shadowed by `cases/<str:case_id>` | Literal route declared first (house pattern); P2-j pins it. |
| A drifted third construction path in future | The service is the only place `Case(...)` is built for a create; the API and UI both delegate. Code review + this plan record it. |
| Route-enumeration failures discovered late | P4 classifies the route **before** the view lands (test-first), so the gate is green at every step. |
| UI defaults (extraction on) surprising an analyst | Q1; the checkbox is visible and submitted explicitly, and the behaviour is documented in the plan + deviations. |
| `tests/conformance/test_ui_nav.py` coupling if the shortcut lands | P3 updates all four artefacts together; the test is the acceptance criterion. |
| Scope creep into §10.3–10.7 classifier fields | Explicit non-goal; the form carries only existing model fields. |

## 12. Open questions (needed before implementation)

- **Q1 — Extraction default in the UI form.** On (my recommendation: on, because it makes Module D's
  feedback loop fire from a manual case, it is idempotent, and org-scoped) or off (a smaller blast radius,
  strictly mirroring the API default)?
- **Q2 — Keyboard shortcut `n` + rail entry.** Add (yes = consistent with "every primary action is
  reachable without a mouse", costs a rail item + cheat-sheet row), or button-only?
- **Q3 — API scope.** Confirm extending `POST /api/v1/case` to accept
  `summary`/`tlp`/`pap`/`flag`/`tags`/`assignee` (recommended: yes — TheHive parity, and it is the only
  way to keep one construction path), or keep the API frozen and let the UI build the case itself
  (rejected: that is exactly the drift P2 eliminated)?
- **Q4 — Default status.** `InProgress` (parity with the API + escalation) or `New` (a manual case starts
  in triage)? Recommendation: `InProgress`, matching what already exists.
- **Q5 — Audit action name.** `case.created` (matches `user.created`/`organisation.created`) — confirm.
- **Q6 — Should the created case be starred/flagged by default?** No (recommendation); `flag` stays an
  explicit choice.

## 13. Acceptance criteria — implementation-level summary

The work is **done** when, on a clean tree:

1. `make check` is green (ruff, format, mypy strict, migrations in sync, css-check, full suite).
2. An analyst can create a case from `/cases/new` with title, description, summary, severity, TLP, PAP,
   status, assignee, flag, tags and extraction, and land on the new case's detail page with a
   "Case created" ledger entry.
3. A viewer cannot reach the route or see the affordance (404 + hidden button).
4. The API accepts the same field set through the same service, with no regression in its existing
   response shapes, tolerance or 403/400 semantics.
5. Cross-tenant assignee input is refused; `owner_org` is always the actor's org; the audit row exists,
   in-transaction, as `case.created`.
6. `test_tenant_scoping.py`, `test_viewer_readonly_ui.py`, `test_ui_nav.py`, `test_ui_urls_smoke.py`
   are extended (never loosened) and green.
7. Verification (`docs/verification/…`) and security review (`docs/reviews/…`) are written, and every
   deviation is recorded in `docs/spec/deviations.md`.

## 14. Implementation Brief (for `django-backend` + `django-frontend`)

**Backend (`django-backend`)**
- `cases/create.py` (new): `create_case(payload, *, actor, org) -> tuple[Case, list[str]]` (no `extract` param — W102-2);
  `@transaction.atomic`; reuses `cases.update._set_case_fields`, `_apply_case_tags`,
  `CaseUpdateError`, `alerts.escalation.resolve_case_status`,
  `cases.ledger.append_timeline_event` (kind `case-created`, title `"Case created"`,
  `description=case.description[:500]`), `observables.extractor.extract_into_case`.
- `cases/views.py::_create_case`: delegate; keep 201/400/403 and the org-less 403; drop the duplicated
  construction.
- `ui/views.py`: `case_create` (`@not_viewer @login_required @require_http_methods(["GET","POST"])`) +
  `_render_case_create_form`; reuse `_as_uuid`, `SEVERITY_CHOICES`, `TLP_CHOICES`, `PAP_CHOICES`,
  `_base_context` (`can_mutate`), the `AuditLog`-in-transaction pattern from `case_edit`.
- `ui/urls.py`: `cases/new` **above** `cases/<str:case_id>`.
- `tests/conformance/test_ui_case_create.py` (new), the two test-file classifications (P4).

**Frontend (`django-frontend`)**
- `ui/templates/ui/case_create.html` (new; mirror `case_edit.html`, same class vocabulary, labels, and
  no inline assets).
- `ui/templates/ui/case_list.html`: `New case` button + empty-state CTA under `can_mutate`.
- If Q2 = yes: `ui/static/ui/keys.js` (`CHEATSHEET` + `ROUTES`), `ui/templates/ui/base.html` (rail link +
  cheat-sheet row).

**Deviations to record:** the reused `CaseUpdateError` name; the API gaining the field set; `case.created`
audit action; no `case.created` automation trigger; the UI extraction default; and (if skipped) no
keyboard shortcut.
