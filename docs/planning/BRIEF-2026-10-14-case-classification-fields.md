# BRIEF-2026-10-14 — Case classification fields (`scope`, `case_type`, `data_classification`, `phase`)

**Plan:** `docs/planning/PLAN-2026-10-14-case-classification-fields.md` (**APPROVED** 2026-10-14).
Read it first — it holds the rationale, the locked decisions (§12) and the facts (§13). This brief is
the executable task list.
**Rule:** any deviation from the plan must be recorded in the plan's **§14 Deviations log** in the
same change set.
**Gate:** every phase ends green under `make check-fast`; run `make check` before hand-off to the
verifier. Do not use `--no-verify`.

## Locked decisions you must implement (do not re-litigate)

1. Four **first-class enum fields** on `Case` (`CharField(choices=…)` + DB `CheckConstraint`).
2. `phase` is a **separate** enum — do **not** touch `CaseStatus` or `CASE_STAGES`.
3. `case_json` **exposes** them as `scope`, `caseType`, `dataClassification`, `phase`
   (Amalthea extensions — do not claim TheHive semantics).
4. **`data_classification` writes are `admin`/`superAdmin` only**; the other three are any writer.
5. `viewer` may **see** `data_classification`; a viewer renders no mutation control.
6. `CaseTemplate` gains `scope`, `case_type`, `phase` — **not** `data_classification`.
7. All four are **queryable + sortable** in the Phase-8 query DSL.
8. Every vocabulary has an explicit `unset` → label `"Unset/Unknown"`. Defaults: `scope="unset"`,
   `case_type="unset"`, `data_classification="unset"`, `phase="initial_triage"`.
8. `case_type` is a **closed enum tuple** (no table).
9. Stored names `scope` / `case_type` / `data_classification` / `phase`; wire names `scope` /
   `caseType` / `dataClassification` / `phase`.

## Locked value sets (copy verbatim into `core/enums.py`)

```python
CASE_SCOPE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("single_asset_user", "Single Asset/User"),
    ("multiple_assets_users", "Multiple Assets/Users"),
    ("single_department", "Single Department"),
    ("multiple_departments", "Multiple Departments"),
    ("complete_enterprise", "Complete Enterprise"),
    ("third_party_supplier", "Third Party/Supplier"),
)
CASE_TYPE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("malware", "Malware"),
    ("phishing", "Phishing"),
    ("password_leak", "Password Leak"),
    ("ransomware", "Ransomware"),
    ("bec", "BEC"),
)
DATA_CLASSIFICATION_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("no_data", "No Data"),
    ("public_data", "Public Data"),
    ("personal_data", "Personal Data (PII)"),
    ("sensitive_data", "Sensitive Data (Credentials, IP)"),
)
CASE_PHASE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("initial_triage", "Initial Triage"),
    ("containment", "Containment"),
    ("analysis", "Analysis"),
    ("remediation", "Remediation"),
    ("cleanup", "Cleanup"),
    ("close", "Close"),
)
# + a helper mirroring severity_choice_kwargs(), e.g.
#   def choices_kwargs(choices):  -> or per-field literal `choices=list(...)` as the models already do
```

---

## P1 — Domain + model + migration — owner: `db-postgres`

**Files:** `core/enums.py`, `cases/models.py`, `cases/migrations/0017_*.py` (generated),
`tests/conformance/test_enum_contracts.py`.

- **T1.1** Add the four tuples above to `core/enums.py`, next to the existing `*_CHOICES`, with a
  comment naming the plan and stating that `phase` is distinct from `CaseStatus.stage`.
- **T1.2** `cases/models.py::Case` — add the four fields with the locked defaults and
  `choices=list(...)`; add four constraints to `Case.Meta.constraints` using the existing
  `in_values(...)` builder (`core/enums.py:140`):
  `case_scope_valid`, `case_type_valid`, `case_data_classification_valid`, `case_phase_valid`.
  Add a class docstring note distinguishing `scope` (blast radius) from tenant "scoping"
  (`identity/scoping.py`).
- **T1.3** `CaseTemplate` — add `scope`, `case_type`, `phase` with the same choices/defaults and
  matching `in_values` constraints (`case_template_scope_valid`, `case_template_case_type_valid`,
  `case_template_phase_valid`). **No** `data_classification` (Q-D6).
- **T1.4** Generate the migration (additive `AddField` + `AddConstraint`). Do **not** hand-roll it.
- **T1.5** Extend `tests/conformance/test_enum_contracts.py`: add the seven new constraint names to
  `CHOICE_FIELDS` with a graded row + a refused value so the exhaustive inventory assertion
  (`actual == set(CHOICE_FIELDS)`) still holds and each CHECK is *proven* to refuse a bad value.
- **AC1.1** `python manage.py makemigrations --check --dry-run` is clean after generating.
- **AC1.2** Migration applies on a fresh SQLite DB **and** on an existing DB with cases (defaults
  backfill).
- **AC1.3** `test_enum_contracts.py` green, including the inventory equality test.
- **AC1.4** Raw `Case.objects.filter(...).update(scope="bogus")` raises `IntegrityError`
  (prove it for each of the four `case_record` constraints).

**Hand-off note:** report the exact constraint names you created and the migration filename.

---

## P2 — Applier + API + `case_json` + query DSL + template parity (`django-backend`)

Depends on **P1**.

### P2a — shared applier (`cases/update.py`)
- Add the four names to `CASE_SCALAR_FIELDS`.
- Add `_CASE_ENUM_FIELDS: dict[str, tuple[str, ...]]` mapping each key to its allowed values
  (derived from `core/enums.py`, never a second hand-written list) and validate in
  `_set_case_fields` exactly as `_CASE_RANGE_FIELDS` does: an out-of-vocabulary value raises
  `CaseUpdateError("BadRequest", …, {key: […]})`; an empty/`None` value is **dropped** (keeps the
  model default — mirror the existing tolerance).
- **Audit + timeline:** when `data_classification` actually changes, append a `TimelineEvent`
  (`kind="classification-changed"`, metadata `{"from":…, "to":…}`, `actor=`) and an `AuditLog` row
  (`identity/audit.py`; `action="case.data_classification"`, `target_type="case"`,
  `target_id=case.id`). Only when the value *changes*.
- **Admin gate (Q-D5):** if the payload contains `data_classification` and the actor's role is not
  `admin`/`superAdmin`, raise `CaseUpdateError("AuthorizationError", …)`. Add a small
  `is_admin(user)` helper in `identity/scoping.py` next to `is_viewer` (compare against role values,
  no new literals) and use it in both the API and the UI. Do not hardcode `"admin"` strings.

### P2b — create path (`cases/create.py`)
- `_create_scalars()` already iterates `CASE_SCALAR_FIELDS`; mirror the `_CASE_RANGE_FIELDS`
  empty-value tolerance for `_CASE_ENUM_FIELDS` so `""`/`null` keeps the default.
- Confirm the `data_classification` admin gate also applies here (a create is a write).

### P2c — wire (`core/serializers.py::case_json`)
- Add `"scope"`, `"caseType"`, `"dataClassification"`, `"phase"` keys.
- Update the static field lists in `docs/spec/api.md` (case create/PATCH accepted fields, §~155/161;
  and the `case_json` description).

### P2d — query DSL (`query/engine.py::_case_fields()`)
- Add `"scope"`, `"caseType"`, `"dataClassification"`, `"phase"` → `_FieldSpec("scope")` etc.
- Update `docs/spec/query-dsl.md`.

### P2e — template parity (`cases/views.py::_apply_template`)
- Copy `scope`/`case_type`/`phase` from the template onto the case (mirror how `severity`/`tlp` are
  copied). Do not copy `data_classification`.

### P2f — spec
- `docs/spec/data-model.md`: document the four `Case` fields, the vocabularies, and a paragraph making
  the `Case.status.stage` vs `Case.phase` distinction explicit (R1 mitigation).

- **AC2.1** `POST /api/v1/case` with each field → 201 and the value echoed by `case_json`.
- **AC2.2** Out-of-vocabulary value → **400** on create **and** PATCH, no `IntegrityError`/500.
- **AC2.3** `null`/`""` → model default (unchanged tolerance).
- **AC2.4** `viewer`/`analyst` PATCH of `data_classification` → refused; `admin` → accepted, with an
  `AuditLog` row + a `timeline` entry of kind `classification-changed`.
- **AC2.5** Query DSL: each field filters and sorts; an unknown field is still a 400.
- **AC2.6** Applying a `CaseTemplate` copies `scope`/`case_type`/`phase` and leaves
  `data_classification` untouched.
- **AC2.7** Full suite green (re-run `test_query_api.py` key-set assertions — they derive from
  `case_json` and should pass unchanged).

---

## P4 — UI (`django-frontend`)

Depends on **P1** (enums) and the P2 context shape.

- `ui/views.py`: thread `scope_choices`/`case_type_choices`/`data_classification_choices`/
  `phase_choices` from `core/enums.py` through the three context builders that already carry
  `severity_choices`/`tlp_choices`/`pap_choices`:
  `_case_create_form_context` (~L481–524), `case_detail` (~L859–862), `case_edit` (~L2332–2335,
  ~L2447–2450).
- `ui/templates/ui/_case_create_fields.html` + `ui/templates/ui/_case_edit_fields.html`: add four
  `<select>`s following the exact existing pattern (`id="f-…"`, `{% if form_id %} form="{{ form_id }}"{% endif %}`).
- **`data_classification` gate:**
  - for an admin/superAdmin (use a new `can_classify` context flag derived from
    `identity/scoping.is_admin`), render the editable `<select>`;
  - for everyone else, render the value as **read-only text** (a `<dd>`), never a disabled form
    control that silently drops the value on submit.
- `ui/templates/ui/_tab_general.html`: show all four in the `<dl class="meta">` (label + human
  value; never a colour-only signal; icons decorative only with `aria-hidden="true"`).
- Persist through the shared applier only (`cases/update.py`) — no second field list in the view.
- **AC4.1** Create a case with all four set → persisted, and rendered on the general tab with human
  labels.
- **AC4.2** Edit modal pre-fills from the instance and saves.
- **AC4.3** A bad posted value shows an inline form error (no 500).
- **AC4.4** An analyst sees `data_classification` as text, not a control; an admin sees a control.
- **AC4.5** No multi-line `{% … %}` tags; no `{% url … as X %}` consumed outside its own `{% block %}`
  (the two defect classes from `PLAN-2026-10-13-case-detail-tabs.md` §13.2 item 11). Re-check with the
  lexer scan (`TokenType.TEXT` contents matching `\{[#%]`), not a node walk.
- **AC4.5b** `ui-static` / `test_ui_icons.py` still green if any icon is added.

---

## Cross-cutting — `qa-tester`

- Extend `tests/conformance/test_ui_case_create.py` for create/edit/display + the 400 path.
- Add API round-trip + refusal tests per field.
- Query-DSL filter/sort tests.
- Regression: `test_viewer_affordances.py`, `test_viewer_readonly_ui.py`,
  `test_tenant_scoping.py`, `test_thehive_fixtures.py`.
- Repair, don't weaken, any pinned assertion that moves — each edit must be justified in the commit
  message and the plan's deviations log.

## `security-auditor` review targets

- The admin gate is on **both** create and update, server-side, not just in the template (P2a/P2b).
- No mass assignment: the write path still reads from the explicit allow-list only.
- Enum values are validated before any DB write (no 500 on bad input); the query DSL resolves them
  through the existing whitelist.
- `AuditLog` rows carry the actor and the org; a cross-org write stays impossible (ADR-004).
