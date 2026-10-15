# PLAN-2026-10-14 — Case classification fields: `scope`, `case_type`, `data_classification`, `phase`

**Status:** **APPROVED 2026-10-14** — all §12 decisions locked by the user ("D8 yes Unset/Unknown
value, rest as recommended"). P1 may start. Deviations during implementation must be recorded in
§14.
**Source:** `TODO.md` §10.3–10.6 (user request 2026-10-11), tracked as priority **6** in the TODO
table. `PLAN-2026-10-13-ui-case-create.md:105` deliberately excluded these fields from manual case
creation, so this is the first plan to cover them.
**Owner:** planner. **Proposed scope owners:** `db-postgres` + `django-backend` (P1–P3),
`django-frontend` (P4), `qa-tester` (tests), `security-auditor` (P5).
**Related:** `AGENTS.md` §3 (schema), §6 (design/style); `docs/spec/data-model.md`,
`docs/spec/api.md` (§`case_json`), `docs/spec/query-dsl.md`; ADR-002 (§D4/§D11),
ADR-004 (tenant), ADR-006 (viewer role); `TODO.md` §10.3–10.6, §12.3.

---

## 1. Summary

`Case` today carries `severity`, `tlp`, `pap`, `tags`, `status` (FK to a `CaseStatus` vocabulary
with a 3-bucket `stage`), `flag`, `summary`. Missing are the four fields an IR analyst uses to
*classify* an incident for reporting and for downstream routing:

| Field (TODO) | Purpose | Locked values (Q-D8 adds an explicit `unset`) |
|---|---|---|
| `scope` (10.3) | Blast radius | `unset` (Unset/Unknown), Single Asset/User, Multiple Assets/Users, Single Department, Multiple Departments, Complete Enterprise, Third Party/Supplier |
| `case_type` (10.4) | Incident taxonomy | `unset` (Unset/Unknown), Malware, Phishing, Password Leak, Ransomware, BEC, … |
| `data_classification` (10.5) | What data is implicated | `unset` (Unset/Unknown), No Data, Public Data, Personal Data (PII), Sensitive Data (Credentials, IP) |
| `phase` (10.6) | IR lifecycle position | `initial_triage`, `containment`, `analysis`, `remediation`, `cleanup`, `close` (+ `unset`) |

This plan adds them end-to-end: **model → migration → DB constraint → shared applier → API +
`case_json` → compat parity → query DSL → UI (create / edit / general tab) → conformance tests.**

## 2. Goals

- G1 — Four new first-class, queryable, enum-validated `Case` fields (per Q-D1/Q-D9 decisions).
- G2 — Every field is **enforced twice** (`choices=` on the field, `CheckConstraint` in the DB), the
  house rule from `core/enums.py` (REVIEW-2026-10-03 H5/L1) — so a raw write cannot store a typo.
- G3 — Create, edit (modal), and display all four on the case page, keyboard-reachable, no
  colour-only signalling (AGENTS §6).
- G4 — Round-trip through the API: accepted on `POST /api/v1/case` + `PATCH case/{id}` and returned
  by `case_json` (subject to Q-D3).
- G5 — Queryable through the Phase-8 query DSL + sortable, matching the existing `severity`/`tlp`/`pap`
  treatment (subject to Q-D7).
- G6 — The exhaustive enum audit (`test_enum_contracts.py::test_..._inventory`) covers the new
  constraints, and the graded-domain mutation guards prove each CHECK actually refuses a bad value.

## 3. Non-goals

- **Not** the correlation / "Similar" surface (§12.3, Q-D4) — different problem, tracked separately.
- **Not** bulk editing of classification fields (TODO §12.4).
- **Not** a reporting/export module; `case_json` is the wire surface only.
- **Not** a redesign of `CaseStatus` beyond what Q-D2 requires.
- **Not** a classification-driven sharing/permission model (e.g. "PII cases can't be shared") — that
  would be a new ADR. Only the *write* gate (Q-D5) is in scope.
- **Not** retro-defining TheHive wire semantics we cannot verify; compat exposure is a decision
  (Q-D3), and if it is "expose", the keys are Amalthea extensions (TheHive ignores unknown keys).

## 4. Architecture / approach

No new subsystem. The fields ride the **existing seams** so both surfaces (API and UI) cannot drift —
the same pattern `cases/update.py` + `cases/create.py` already use (deviation W616P2-3):

```
core/enums.py          value sets + constraint builders  (single source of truth)
   │
   ├─ cases/models.py  Case.<field> = CharField(choices=…, default=…)  + CheckConstraint
   │
   ├─ compat/enums.py  (Q-D3: expose) ── labels derived from core.enums
   │
   ├─ cases/update.py  CASE_SCALAR_FIELDS += …; _CASE_ENUM_FIELDS validation → clean 400
   │                   (shared by create + PATCH + UI forms)
   │
   ├─ cases/create.py  _create_scalars() iterates CASE_SCALAR_FIELDS → picked up for free
   │
   ├─ core/serializers.py::case_json  += keys (Q-D3)
   │
   ├─ query/engine.py::_case_fields()  += fields (Q-D7)
   │
   └─ ui/  _case_create_fields.html, _case_edit_fields.html, _tab_general.html
```

## 5. DB schema (locked — Q-D1/Q-D9)

Additive only, one migration `cases/migrations/0017_case_classification.py`.

```python
# core/enums.py (locked; labels are the display strings, values are stable strings)
CASE_SCOPE_CHOICES = (
    ("unset", "Unset/Unknown"),
    ("single_asset_user", "Single Asset/User"),
    ("multiple_assets_users", "Multiple Assets/Users"),
    ("single_department", "Single Department"),
    ("multiple_departments", "Multiple Departments"),
    ("complete_enterprise", "Complete Enterprise"),
    ("third_party_supplier", "Third Party/Supplier"),
)
CASE_TYPE_CHOICES = (
    ("unset", "Unset/Unknown"),
    ("malware", "Malware"),
    ("phishing", "Phishing"),
    ("password_leak", "Password Leak"),
    ("ransomware", "Ransomware"),
    ("bec", "BEC"),
)  # closed enum (Q-D9)
DATA_CLASSIFICATION_CHOICES = (
    ("unset", "Unset/Unknown"),
    ("no_data", "No Data"),
    ("public_data", "Public Data"),
    ("personal_data", "Personal Data (PII)"),
    ("sensitive_data", "Sensitive Data (Credentials, IP)"),
)
CASE_PHASE_CHOICES = (
    ("unset", "Unset/Unknown"),
    ("initial_triage", "Initial Triage"),
    ("containment", "Containment"),
    ("analysis", "Analysis"),
    ("remediation", "Remediation"),
    ("cleanup", "Cleanup"),
    ("close", "Close"),
)
```

```python
# cases/models.py — Case additions (Q-D8 defaults: "unset" where the truth is unknown)
scope               = models.CharField(max_length=32, choices=list(CASE_SCOPE_CHOICES), default="unset")
case_type           = models.CharField(max_length=32, choices=list(CASE_TYPE_CHOICES), default="unset")
data_classification = models.CharField(max_length=32, choices=list(DATA_CLASSIFICATION_CHOICES), default="unset")
phase               = models.CharField(max_length=32, choices=list(CASE_PHASE_CHOICES), default="initial_triage")

class Meta:
    constraints = [
        …,
        in_values("case_scope_valid", "scope", tuple(v for v, _ in CASE_SCOPE_CHOICES)),
        in_values("case_type_valid", "case_type", tuple(v for v, _ in CASE_TYPE_CHOICES)),
        in_values("case_data_classification_valid", "data_classification",
                  tuple(v for v, _ in DATA_CLASSIFICATION_CHOICES)),
        in_values("case_phase_valid", "phase", tuple(v for v, _ in CASE_PHASE_CHOICES)),
    ]
```

- **Defaults (Q-D8):** every field is `NOT NULL` with a default so the migration is safe on existing
  rows and matches the `severity`/`tlp`/`pap` idiom (int defaults, no nulls).
- **Indexes:** Q-D7 = queryable, but a `scope`/`phase` low-cardinality index is usually not worth it.
  Propose **no new index** unless a query-filter excuse appears.
- **No `db_index` on FKs** if `case_type` becomes a table (Q-D9) beyond the implicit one.

## 6. API / wire contract (Q-D3: expose the four keys)

- `POST /api/v1/case`, `PATCH /api/v1/case/{id}`: accept the four fields, validate against the
  vocabulary, out-of-vocabulary → **400** (never a 500 `IntegrityError` — same rule SEC-2026-10-11 L1
  already applies to severity/tlp/pap). An empty/`null` value keeps the model default (ADR-002 §D11
  tolerance).
- `case_json`: add the keys. Proposed names: `scope`, `caseType` (avoid a bare `type` on the case
  object — alert_json already uses `type` and the collision invites confusion), `dataClassification`,
  `phase`. **Final naming is Q-D3.**
- **Compat risk to check:** `tests/conformance/test_query_api.py:194,675` derive the queryable field
  set from `case_json(case)` and assert exact key sets — adding keys *will* move those assertions.
  Enumerate them in the P2 ACs.
- `docs/spec/api.md` §`case_json` + the `case_collection` POST/PATCH field lists must be updated in
  the same phase (doc-drift is a review finding in this repo).

## 7. UI / UX

- **Create + edit:** add four `<select>`s to `_case_create_fields.html` and `_case_edit_fields.html`
  (they are near-identical partials, each used by both a standalone page and a `_modal.html` footer
  form — the `{% if form_id %} form="{{ form_id }}"{% endif %}` rule is load-bearing; see the file
  headers). Selects are native, so keyboard + no-JS both work.
- **Display:** `_tab_general.html` meta `<dl>` — add `Scope`, `Type`, `Classification`, `Phase` rows.
  **Never colour-only** (AGENTS §6): the label is always text; if we add a severity-like chip, it
  carries its name.
- **Vocabulary source:** render `*_choices` from view context (as `severity_choices` is today), not
  hard-coded in the template, so the DB constraint and the form cannot disagree.
- **`?` cheat-sheet / shortcuts:** no new bindings proposed.
- **Empty/unknown:** display the human label, never the raw stored value.

## 8. Phases, tasks and Acceptance Criteria

Each phase ends green under `make check-fast`; the full `make check` at the end.

### P1 — Domain + migration (`db-postgres`, `django-backend`)
- T1.1 Add the four choice tuples + any new constraint builder to `core/enums.py`.
- T1.2 Add the four fields + four `CheckConstraint`s to `Case`; generate `0017_case_classification`.
- T1.3 Backfill defaults; confirm the migration applies on SQLite (dev) and is Postgres-safe
  (`AddField` with a default is a metadata-only change on PG 11+ — verify no table rewrite claim is
  made that isn't true).
- **AC1.1** `makemigrations --check` is clean; `migrate` on a fresh DB succeeds.
- **AC1.2** `test_enum_contracts.py` inventory test passes **after** `CHOICE_FIELDS` gains the four
  entries; each new constraint is graded (a bad value is refused by the DB, not just by `choices`).
- **AC1.3** Raw SQL `INSERT`/`UPDATE` with an out-of-vocabulary value raises `IntegrityError`.

### P2 — Shared applier + API + `case_json` (`django-backend`)
- T2.1 `cases/update.py`: add the four names to `CASE_SCALAR_FIELDS`; introduce enum validation
  (a `_CASE_ENUM_FIELDS` map, analogous to `_CASE_RANGE_FIELDS`) so a bad value is a clean 400 /
  form error from one place.
- T2.2 `cases/create.py`: confirm `_create_scalars()` picks them up unchanged; add a test.
- T2.3 `case_json`: emit the four keys (names per Q-D3); update `docs/spec/api.md`.
- T2.4 Update every test that pins the `case_json` key set or the create/patch field list
  (`test_query_api.py` key-set assertions, `test_thehive_fixtures.py` if it compares key sets).
- **AC2.1** `POST /api/v1/case` with each field → 201 and the value echoed by `case_json`.
- **AC2.2** Out-of-vocabulary value → **400** (both create and PATCH), no `IntegrityError`, no 500.
- **AC2.3** `null`/`""` → model default, not a rejection (tolerance preserved).
- **AC2.4** Full suite green; no test weakened (key-set updates only, each justified).

### P3 — Query DSL (`django-backend`)
- T3.1 `query/engine.py::_case_fields()` += the four fields (Q-D7); update `docs/spec/query-dsl.md`.
- **AC3.1** `{"_eq": {"_field": "scope", …}}` filters; each field sorts; unknown field still 400.
- **AC2/3 AC** The existing "field absent from a branch = cannot match" rule holds (no crash on
  alert/observable branches).

### P4 — UI (`django-frontend`)
- T4.1 The two field partials + `_tab_general.html`; view context supplies the choice tuples
  (`ui/views.py::_case_create_form_context` + the `case_detail`/`case_edit` contexts, alongside
  `severity_choices`/`tlp_choices`/`pap_choices`).
- T4.2 Prefill on edit from the instance; persist on POST through the shared applier.
- **AC4.1** Create a case with all four set → values persist and render on the general tab.
- **AC4.2** Edit modal pre-fills correctly and saves; a bad posted value shows an inline error.
- **AC4.3** `test_ui_case_create.py` extended; no colour-only signalling; every control keyboard
  reachable; **no multi-line `{% … %}` tags** and no `{% url as %}` used outside its block (the two
  land-mines from `PLAN-2026-10-13-case-detail-tabs.md` §13.2 item 11).

### P5 — compat parity (`django-backend`)
- T5.1 `compat/enums.py` labels derived from `core/enums.py`; `tests/conformance/test_enum_contracts.py`
  asserts the agreement (it already does for severity/tlp/pap).
- T5.2 Fixture/golden updates if a TheHive-shaped response is asserted byte-for-byte.
- **AC5.1** Every new label resolves from `core.enums` (no literal duplicated in `compat/`).

## 9. Security

- **S1 — Write gate (Q-D5).** `data_classification` is admin+ only. Enforce it in the shared applier
  (one place), return a uniform error per the ADR-004/006 convention, and audit every accepted change.
- **S2 — Audit.** Every classification change writes an `AuditLog` row + a `TimelineEvent`, so the
  ledger shows who reclassified an incident (the same bar as status transitions).
- **S3 — Viewer.** A viewer must not see a mutation control; if `data_classification` is judged
  sensitive, decide explicitly whether viewers see it (Q-D5b) rather than by accident of the template.
- **S4 — Input.** Values are validated against a closed set before any write; never interpolated into
  SQL; the query DSL resolves them through the existing whitelist (`_resolve`), so an unknown field
  stays a 400.
- **S5 — No colour-only, no icon-only** (AGENTS §6).

## 10. Testing

- Extend `tests/conformance/test_enum_contracts.py` (inventory + graded domains) — **mandatory**, the
  inventory test asserts equality with `CHOICE_FIELDS`.
- API round-trip + 400-path tests for each field.
- Query-DSL filter/sort tests (Q-D7).
- UI create/edit/display tests; viewer-affordance regression.
- Migration test: fresh DB + existing-row backfill.
- Confirm `test_tenant_scoping.py` route enumeration is unaffected (no new routes).

## 11. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | `phase` competes with `CaseStatus.stage`; automation triggers key on `stage` → two lifecycle axes confuse analysts and automations (TODO Q-D2). | **Resolved (Q-D2):** separate enum; document the two axes as distinct in `docs/spec/data-model.md`; no automation trigger reads `phase` in this plan. |
| R2 | `type` is an open list that will grow; a plain enum means a migration per new type (Q-D9). | **Accepted (Q-D9):** closed enum; a new type is a small additive migration. |
| R3 | `case_json` key additions ripple into the query API + fixtures. | Confirmed **low** (§13): the two key-set assertions derive from `case_json` dynamically; only the static `docs/spec/api.md` lists need editing. |
| R4 | Vocabularies churn; renaming a stored value later breaks data. | Store stable string values, label separately (`compat/enums.py` derives labels); document the migration cost; `unset` is the forward-compatible neutral. |
| R5 | "scope" collides with tenant "scoping" vocabulary (`identity/scoping.py`). | **Accepted (Q-D10):** keep `scope`; a docstring in `cases/models.py` states the difference. |
| R6 | `data_classification` is admin-gated (Q-D5) — a UI that renders the control for an analyst would be a broken affordance. | Gate the control with the same `can_*` context flag the viewer affordance uses; `security-auditor` to confirm. |

## 12. Decisions (locked 2026-10-14)

- **Q-D1 — Enum fields vs custom fields. → ENUM FIELDS.** First-class `CharField(choices=…)` +
  `CheckConstraint` for all four. `custom_fields` remains for org-specific extras.
- **Q-D2 — `phase` vs `CaseStatus`. → (a) SEPARATE `Case.phase` ENUM.** `CaseStatus.stage` stays the
  stable 3-bucket *openness* axis that automation triggers and `CASE_STAGES` key on; `phase` is the
  independent IR lifecycle. Document the two axes as distinct in `docs/spec/data-model.md`.
- **Q-D3 — compat exposure. → EXPOSE in `case_json`** as `scope`, `caseType`, `dataClassification`,
  `phase`. The keys are **Amalthea extensions**; we do not claim TheHive semantics for them.
- **Q-D4 — "Similar"/correlation surface. → OUT OF SCOPE**, deferred to `TODO.md` §12.3.
- **Q-D5 — Write permissions. → any writer for `scope`/`type`/`phase`; `data_classification` is
  admin+ only and audited.** Enforced in the shared applier (one place); a non-admin attempt is a
  uniform error per ADR-004/006; every accepted change appends a `TimelineEvent` (and an `AuditLog`
  row via `identity/audit.py`).
- **Q-D5b — Viewer visibility. → YES**, a `viewer` may see `data_classification` (it already sees the
  case); no mutation control is rendered for a viewer (existing affordance contract).
- **Q-D6 — `CaseTemplate` parity. → YES for `scope`, `case_type`, `phase`; NO for
  `data_classification`.** Applied by `cases/views.py::_apply_template`.
- **Q-D7 — Query DSL. → YES.** Add all four to `query/engine.py::_case_fields()` (filter + sort) and
  to `docs/spec/query-dsl.md`.
- **Q-D8 — Defaults. → an explicit `unset` ("Unset/Unknown") value in every vocabulary**; a fresh
  case says "not yet classified" rather than guessing. Defaults: `unset` / `unset` / `unset` /
  `initial_triage`.
- **Q-D9 — `type` mechanics. → (a) CLOSED ENUM tuple** (`CASE_TYPE_CHOICES`); a typo is a 400/DB
  refusal, and a new type costs a small migration.
- **Q-D10 — Naming. → keep** stored `scope` / `case_type` / `data_classification` / `phase`; wire
  `scope` / `caseType` / `dataClassification` / `phase`.

## 13. Facts confirmed during planning (so the brief is precise)

- `core/enums.py` already exposes the exact builder: `in_values(name, field, values)`
  (`core/enums.py:140`) → `Q(field__in=values)`. No new builder needed for a string enum.
- `cases/update.py::CASE_SCALAR_FIELDS` = `("title","description","summary","severity","tlp","pap","flag")`
  is the single allow-list shared by PATCH, bulk PATCH and the UI (`ui-case-edit`); int bounds live in
  `_CASE_RANGE_FIELDS` → the new string enums need a sibling `_CASE_ENUM_FIELDS` map (`{field: tuple(values)}`).
- `cases/create.py::_create_scalars()` iterates `CASE_SCALAR_FIELDS` and skips `title`, so the new
  fields are picked up for free **provided** they are added to `CASE_SCALAR_FIELDS`; the
  `_CASE_RANGE_FIELDS` empty-value tolerance must be mirrored for the string enums (an empty/`null`
  value keeps the model default — ADR-002 §D11).
- `query/views.py::_render()` calls `case_json` then applies `includeFields`/`excludeFields`, so the
  two `test_query_api.py` key-set assertions (lines 194, 675) are **derived** from `case_json` and do
  **not** break on added keys — but both must be re-run.
- `_case_fields()` (`query/engine.py:89`) is a **separate** allow-list for filter/sort; Q-D7 means the
  four keys are added there too (`_FieldSpec("scope")` etc.).
- `Case` guards `owner_org`/`number`/`id` from mass assignment; the new fields go in the explicit
  allow-list, never read from `**payload`.
- The UI already threads `severity_choices`/`tlp_choices`/`pap_choices` through `_case_create_form_context`
  (`ui/views.py:481-524`), `case_detail` (`:859-862`) and `case_edit` (`:2332-2335`, `:2447-2450`) — the
  new tuples ride the same three context builders.
- No new route is added, so `test_tenant_scoping.py` route enumeration is unaffected.

## 14. Deviations log

1. **Admin gate fail-open (F1)**: The original implementation of `_set_case_fields` had `if key == "data_classification" and actor is not None and not is_admin(actor):` which allowed `actor=None` to bypass the gate. Fixed to `if key == "data_classification" and not is_admin(actor):` with `actor` made a required keyword-only parameter. All callers updated.

2. **Bulk PATCH bypass (F2)**: `case_bulk_update` did not pass `actor` to `_set_case_fields`, and did not map camelCase wire names. Fixed to pass `actor=_actor(request)`, map `caseType`/`dataClassification`, and catch `CaseUpdateError` to report per-item 403 for AuthorizationError. `compat.bulk.bulk_patch` updated to preserve the Response status code.

3. **Read-only display (F3)**: Templates showed raw stored tokens and fell back to "unset" on error re-render. Added `choice_label` template filter in `ui.templatetags.ui_tags` to show human labels. Updated `_case_create_fields.html`, `_case_edit_fields.html`, `_tab_general.html` to use it. Fixed `_render_case_edit_form` to fall back to persisted case values for classification fields.

4. **Request.data mutation (F4)**: `_create_case` and `_update_case` mutated `request.data` directly, causing 500 on form-encoded bodies (immutable QueryDict). Fixed to build mutable copy: `QueryDict.dict()` for form-encoded, `dict(request.data)` for JSON.

5. **CaseTemplate null rejection (F5)**: `_create_case_template` rejected explicit `null`/`""` for enum fields. Aligned with Case create/update tolerance: empty/null now means "use model default".

6. **Test coverage (F6)**: Added `tests/conformance/test_case_classification.py` covering all AC2.1–AC2.7 plus form-encoded bodies, template filter, and bulk camelCase mapping.

7. **Bulk PATCH audit gap (G1)**: `case_bulk_update` called `_set_case_fields` directly and emitted only a generic "Case updated (bulk)" `TimelineEvent`, missing the `classification-changed` `TimelineEvent` and `AuditLog` rows required by S2. Fixed by extracting `_capture_classification_values()` and `_emit_classification_changes()` helpers in `cases/update.py`, shared by both `apply_case_update` and `case_bulk_update`. The strict `xfail` in `test_bulk_classification_change_is_audited` was removed (test now passes as a hard requirement).

8. **Standalone edit page missing classification fields (G2)**: `ui/templates/ui/case_edit.html` duplicated its field set inline and omitted `scope`/`case_type`/`data_classification`/`phase`, while the edit modal (via `_case_edit_fields.html`) included them. Added the four fields to `case_edit.html` following its existing inline style (`{% for val, label in ..._choices %}`, `form_values.X|default:case.X`). Q-D5 gate preserved: admin sees `<select>`, non-admin sees read-only human-labelled span via `choice_label` filter. Verified that removing the `_render_case_edit_form` classification fallback now fails a test (mutation check).

9. **Non-admin create error re-render echoes smuggled `data_classification` (G2b)**: `_case_create_form_context` and `_render_case_edit_form` echoed a smuggled `data_classification` value in the read-only span for non-admin users. Fixed to only include `data_classification` in `form_values` when `can_classify` is True; otherwise the default "Unset/Unknown" label is shown. Updated `test_create_error_rerender_analyst_shows_human_label_not_token` → `test_create_error_rerender_analyst_shows_unset_not_smuggled` to assert the new behaviour.

## 15. Implementation brief

`docs/planning/BRIEF-2026-10-14-case-classification-fields.md`, handed to `db-postgres`/`django-backend`
(P1–P3), `django-frontend` (P4), `qa-tester` (tests) and `security-auditor` (the S-items).
