# Plan — Case-template severity “None”

**Status:** Implemented 2026-11-10 — focused tests green (163 passed); full suite user-run pending.
**Scope:** `CaseTemplate.severity` storage, UI, API, and apply semantics.
**Related:** `PLAN-2026-10-22-case-template-defaults.md`, `PLAN-2026-10-14-case-classification-fields.md`.

## Summary

Let an analyst set a case template’s severity to **None**. Applying such a template copies every
other template default as today but **leaves the case’s current severity unchanged**. This also holds
for re-application (`apply_to_existing`, `resync=True`, and the bulk API).

Current behaviour overwrites severity unconditionally:

```python
# cases/views.py::_apply_template
case.severity = template.severity
```

Required behaviour:

```python
if template.severity is not None:
    case.severity = template.severity
```

## Goals

- Add an explicit “None” choice to case-template severity.
- Preserve the target case’s severity when a “None” template is applied or re-applied.
- Keep the existing template UI/API usable with no migration of existing template data.
- Keep severity labels text-based and accessible; never signal “None” by colour alone.
- Preserve TheHive-compatible integer severity values where a value exists.

## Non-goals

- Changing other template fields (TLP, PAP, flag, scope, phase, tasks, tags, custom fields).
- Changing `Case.severity` or any alert/observable severity enum.
- Adding a new global severity value such as `0` to cases, alerts, or search taxonomies.
- Backfilling existing templates to `None`.
- Per-tenant template severity semantics.
- New CSS components unless the existing `.sev` / `.dim` styles cannot express the state.

## Decisions

1. Store “None” as SQL `NULL` on `CaseTemplate.severity`.
2. **New default is `None`** for newly created templates and API/UI create without an explicit
   severity. Existing rows keep their current integer severity; no data migration/backfill.
3. API: explicit `"severity": null` means `None`; omitted on create also means `None`;
   omitted on patch means unchanged.
4. UI select value `"none"` maps to `None`; absent/empty form value also maps to `None`.
5. List shows `None`; detail/form/apply picker explain “keeps case severity”.
6. For `None`, API output is `"severity": null, "severityLabel": ""`.
7. Visible labels: form `None (keep case severity)`, list `None`, detail
   `None — keep case severity`, apply picker `(keeps case severity)`.
8. The apply picker shows the “keeps case severity” hint. No template import/export surface is
   added.

## Architecture

No new architectural boundary and no ADR required. This stays inside the existing Django/DRF/UI
surfaces. A nullable template-only default is represented in the model and serialized as `null`;
the apply path is centralized in `_apply_template`, so UI, bulk API, and resync inherit one rule.

## Data model

`cases/models.py::CaseTemplate`:

```python
severity = models.SmallIntegerField(
    null=True,
    blank=True,
    default=None,
    choices=list(SEVERITY_CHOICES),
)
```

Meta constraint must allow `NULL` while still rejecting `0`, `5`, `-1`, etc.:

```python
in_range(
    "case_template_severity_range",
    "severity",
    SEVERITY_MIN,
    SEVERITY_MAX,
    allow_null=True,
)
```

Implementation detail: extend `core/enums.py::in_range` with a keyword-only `allow_null=False`;
when true, OR the range predicate with `Q(<field>__isnull=True)`. Other graded fields keep their
current behaviour.

Migration (generated, expected `cases/migrations/0022_...`):

- `AlterField` on `case_template.severity` (`null=True`, `blank=True`, `default=None`, same choices).
- Remove and re-add `case_template_severity_range` with the nullable predicate.
- Existing rows are untouched; no data migration. Only newly created templates default to `None`.

Update `docs/spec/data-model.md` to state that a template’s severity is nullable and `NULL` means
“do not overwrite the case’s severity when applied”.

## API contract

Endpoints: `GET/POST /api/v1/caseTemplate`, `GET/PATCH/DELETE /api/v1/caseTemplate/{idOrName}`.
`taxonomy.caseTemplates` inherits the same output.

Create:

| Input | Stored value |
|---|---|
| `severity` omitted | `None` (new default) |
| `severity: null` | `None` |
| `severity: 1..4` | that value |
| `severity: 0`, `5`, `"abc"`, other invalid | `400` |

Patch:

| Input | Behaviour |
|---|---|
| key omitted | unchanged |
| `severity: null` | set `None` |
| `severity: 1..4` | set value |
| invalid | `400` |

Output (`case_template_json`):

```json
{"severity": null, "severityLabel": ""}
```

`severityLabel` remains a string, so clients see a type-stable label field.

Add a shared parser, e.g. `cases/template_defaults.py::parse_case_template_severity`, accepting
`None`/`"none"` and integer/numeric-string `1..4`, raising `ValueError` otherwise. UI and API use
the same parser so they cannot drift.

Update `docs/spec/api.md` with the null severity extension and the create/patch distinction.

## Apply semantics

In `cases/views.py::_apply_template`:

- Copy severity only when `template.severity is not None`.
- Keep all other scalar overwrites unchanged.
- A re-apply of a `None` template must not reset the case to the model default `None`/`2`.
- `case.source_template` and the existing ledger event still update.
- No new ledger field is required; the event continues to record `{"template": name}`.
- Existing numeric templates keep overwriting severity exactly as today.

This automatically covers:

- `ui/views/case_templates.py::case_apply_template`
- `case_template_edit` with `apply_to_existing=1` / `resync=True`
- `POST /api/v1/case/_bulk/caseTemplate`

There is no current “create a new case from a template” path. If one is added later, it must use
`_apply_template` so the same skip rule applies.

## UI/UX

### Template form

Add an explicit first option:

```html
<option value="none">None (keep case severity)</option>
```

- Create form: default is None; the “None” option is selected by default.
- Edit form with `severity is None`: select the “None” option; do **not** also select Medium.
- Use a `form_severity` context value (`None` on create, `template.severity` on edit) to avoid the
  current `|default:2` fallback selecting Medium for a null value.
- `_parse_template_form`: `"none"` → `None`; absent/empty → `None`; otherwise parse and validate
  `1..4`.
- Help text below the select: “None leaves the case’s existing severity unchanged when applied.”

### List and detail

- List: render a neutral `<span class="dim">None</span>` when severity is `None`; no `sev-` class.
- Detail: render `None — keep case severity` (or equivalent) with `.dim`; no severity swatch.
- Numeric severities keep the existing semantic swatch and text label.

### Apply picker

`case_template_apply.html`: append a hint for `None` templates, e.g.
`{{ t.name }} — {{ t.display_name }} (keeps case severity)`, so the analyst knows the case’s
severity will not change. The hint is required.

### Accessibility

- “None” is communicated by text, never by colour alone.
- The select has a visible label and explanatory help text.
- Keep full keyboard operation; no JS required.
- Existing `?` shortcuts/modal patterns are unchanged.

## Phases and acceptance criteria

### Phase 1 — Model, constraint, migration (`django-backend`)

1. Make `CaseTemplate.severity` nullable with default `None` and existing choices.
2. Extend `in_range` with `allow_null`; make the template constraint accept `NULL` but reject
   out-of-range integers.
3. Generate/apply migration; no data backfill.
   - **AC-CTN-1:** `CaseTemplate.objects.create(name="x")` stores `severity=None`.
   - **AC-CTN-2:** Explicit numeric severities remain storable; an existing row with `2` is not
     backfilled.
   - **AC-CTN-3:** `severity=0`, `5`, `-1`, `99` still violate the CHECK constraint.

### Phase 2 — Apply + API (`django-backend`)

1. Update `_apply_template` to skip `None` severity; update the docstring.
2. Add shared severity parser; use it in API create/patch and UI form parsing.
3. Use `None` as the create default; keep patch-absent semantics; return `400` for invalid values.
4. Serialize `null` severity/label without a 500.
   - **AC-CTN-4:** Applying a `None` template leaves an existing case’s severity byte-for-byte
     unchanged (test e.g. from `1` and from `4`).
   - **AC-CTN-5:** Re-applying / `apply_to_existing` with `None` also leaves severity unchanged.
   - **AC-CTN-6:** Numeric templates still overwrite severity on apply and re-apply.
   - **AC-CTN-7:** API create with omitted severity or `severity: null` returns `severity: null`;
     patch with `null` sets `None`; patch absent leaves unchanged.
   - **AC-CTN-8:** Invalid API severity returns `400`, never a 500/CHECK failure.

### Phase 3 — UI (`django-frontend`)

1. Add the “None” option + help text; correct create/edit selected-state handling.
2. Update list/detail rendering for `None`.
3. Update the apply picker hint.
4. Reuse existing `.dim`; no new CSS unless required.
   - **AC-CTN-9:** Create defaults to None; selecting a numeric severity persists it; selecting
     None persists `NULL` and re-renders selected.
   - **AC-CTN-10:** Editing a null template selects None and not Medium.
   - **AC-CTN-11:** List/detail show a textual “None” state with no invalid `sev-` class.
   - **AC-CTN-12:** UI apply and edit-with-resync preserve the case severity for None templates.

### Phase 4 — Tests, docs, verification

1. Extend template/apply/API/UI conformance tests.
2. Update enum-contract test coverage for nullable template severity.
3. Update `docs/spec/data-model.md` and `docs/spec/api.md`.
4. Run focused tests, then hand off to `verifier`; user runs the full suite.
   - **AC-CTN-13:** Focused tests cover model null/range, apply/re-apply, API create/patch/output,
     UI create/edit/apply, and list/detail.
   - **AC-CTN-14:** No regression to numeric template severity or source-template provenance.
   - **AC-CTN-15:** Plan/brief deviations recorded in this document.

## Handoff for implementation

**Backend — first**

- `core/enums.py`: extend `in_range(..., allow_null=False)`.
- `cases/models.py`: nullable `CaseTemplate.severity`; constraint `allow_null=True`.
- `cases/migrations/0022_*`: generated AlterField + constraint replacement.
- `cases/template_defaults.py` (or an agreed helper module): shared severity parser.
- `cases/views.py`: `_apply_template` skip; `_create_case_template` and
  `_update_case_template` parse/validate severity.
- `core/serializers.py`: `case_template_json` null-safe.
- Tests: `tests/conformance/test_case_template_defaults.py`,
  `tests/conformance/test_enum_contracts.py`, new focused null-severity test module if smaller.

**Frontend — second**

- `ui/views/case_templates.py`: `form_severity` context; parse `"none"`.
- `ui/templates/ui/case_template_form.html`: None option + help text; selected state.
- `ui/templates/ui/case_template_list.html`: null-safe severity cell.
- `ui/templates/ui/case_template_detail.html`: null-safe severity row.
- `ui/templates/ui/case_template_apply.html`: picker hint.
- Tests: `tests/conformance/test_case_template_defaults.py` and UI wiring tests.

**Constraints**

- Do not change `Case.severity` to nullable.
- Do not add `0` to the severity enum.
- Do not backfill existing templates.
- Do not commit; user runs the full suite.
- Record any deviation here before closing.

## Security

- Validate severity as an exact integer `1..4` or explicit `null`; reject booleans/strings that are
  not numeric and never let invalid data reach the CHECK constraint as a 500.
- No new write path: existing role/tenant checks on template edit/apply remain authoritative.
- Template application remains tenant-scoped through the existing case resolution path.
- Null rendering is autoescaped; no user-controlled HTML enters the status label.
- No secret, credential, or configuration change.

## Testing

Model/DB:

- Null accepted; `0`, `5`, `-1`, `99` rejected.
- New-template default is `None`; existing rows are not backfilled.

Apply:

- Numeric template overwrites both fresh and re-applied cases.
- None template preserves severity on fresh apply, re-apply, UI edit+resync, and bulk API.
- Other template fields still copy when severity is None.

API:

- Create: omitted, null, valid, invalid.
- Patch: absent, null, valid, invalid.
- `case_template_json` and `taxonomy` output.
- No 500s for null/invalid.

UI:

- Create default None; numeric selection persists; None persists.
- Edit null shows None selected and not Medium.
- List/detail no `sev-` class for None.
- Apply picker hint.
- Existing viewer/role gates unchanged.

## Risks

- **Selected-state bug:** `|default:2` treats `None` as Medium. Mitigate with explicit
  `form_severity` context and a regression test. `None` must be the create default and must not
  accidentally select Medium.
- **Django choices + null:** `blank=True`/`null=True` must be set together for forms; model checks
  still enforce the range for non-null values.
- **Constraint replacement:** ensure the migration removes the old range constraint before adding
  the nullable one; test `NULL` and out-of-range values.
- **API compatibility:** returning `null` is an extension. Document it and keep numeric output
  unchanged for existing templates.
- **Apply regression:** all apply call sites share `_apply_template`; test every path to ensure the
  skip is not accidentally reimplemented.
- **Full-suite drift:** focused tests first; user runs the full suite after implementation.
- **Pre-existing backend issue:** The `_update_case_template` API view returns `HTTP 204 No Content`
  for PATCH requests instead of `HTTP 200 OK` with updated template JSON. This is a pre-existing
  issue in the backend, not related to the UI changes.
- **Case severity default:** The `Case` model defaults to severity 2 (Medium), not 3 (High).
  Tests that expect severity 3 must explicitly set `severity=3` when creating a case.

## Resolved questions

1. **Storage:** SQL `NULL`.
2. **API output:** `"severity": null, "severityLabel": ""`.
3. **Defaults:** `None` is the default for newly created templates; existing rows are unchanged.
4. **Labels:** form `None (keep case severity)`, list `None`, detail `None — keep case severity`,
   apply picker `(keeps case severity)`.
5. **UI apply picker:** include the hint.
6. **Import/export:** no additional surface.
