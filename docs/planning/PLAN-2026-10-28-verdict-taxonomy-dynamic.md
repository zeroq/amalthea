# PLAN-2026-10-28 — Dynamic Taxonomy-Backed Case Verdicts

**Status:** Proposed  
**Owner:** planner  
**Related:** AGENTS.md §2 (Universal Ingestion / Case Lifecycle), `PLAN-2026-10-23-taxonomy-phase2-compilation.md`, ADR-002 §D4, proposed ADR-012  
**Primary request:** After a superadmin adds or edits a `case_verdict` `TaxonomyTerm` (including toggling `is_closing`), the new/changed term must be usable in case and alert verdict validation, close dialogs, and close enforcement without a code change or server restart.

---

## 1. Summary

Phase 2 introduced `core.TaxonomyTerm` and an editor for the five compiled case vocabularies, but runtime behaviour is still driven by `core/enums.py` constants. The taxonomy page can create a `red_team` verdict, yet case create/update rejects it because `_CASE_ENUM_FIELDS` validates against `CASE_VERDICT_VALUES`; close dialogs and alert close use `CASE_CLOSING_VERDICTS`; and `Alert.verdict` still carries a database CHECK limited to `undefined`, `true_positive`, `false_positive`.

This plan makes the taxonomy table the runtime source of truth for the five compiled vocabularies, with `core/enums.py` demoted to bootstrap/default data and a read-only resolver that never queries the database at import time. It switches all case and alert verdict paths, removes the stale alert CHECK, relaxes database enforcement to a non-blank structural guard, and adds tests proving that a newly added verdict and a newly closing verdict work end-to-end.

The same resolver is applied to `case_scope`, `case_phase`, `case_type`, and `data_classification`, because they suffer the identical stale-enum defect. Verdict is the first and critical path; the other four are completed in the same change to avoid a second round of fixes.

---

## 2. Goals

- **G1 — Immediate effect:** a `TaxonomyTerm(category="case_verdict")` created or edited through `/taxonomy` is accepted by the next case/alert request and appears in the next rendered picker.
- **G2 — Dynamic closing semantics:** `is_closing=True` on a verdict term makes it a valid closing verdict; `is_closing=False` removes it from closing pickers and from the close rule.
- **G3 — Consistent enforcement:** API create, API single update, API bulk update, UI create/edit, UI status/close, case templates, and alert close all resolve the same taxonomy.
- **G4 — Alert parity:** a case verdict can propagate to linked alerts, and an alert can close with the same dynamic verdict vocabulary. The hardcoded `alert_verdict_valid` CHECK must not reject a legitimate term.
- **G5 — Safe bootstrapping:** migrations and module imports never require the taxonomy table to be queryable; `core/enums.py` remains usable as static bootstrap data.
- **G6 — Defense in depth:** remove stale enumerated CHECK constraints, keep a non-blank structural CHECK on verdict columns, and validate vocabulary in the service layer.
- **G7 — No regression in protected defaults:** `undefined` remains present, protected, non-closing, and the default; `true_positive` and `false_positive` remain protected closing defaults.
- **G8 — Auditable changes:** taxonomy create/update/delete continues to write audit records, including `is_closing` changes.

---

## 3. Non-goals

- **N1** Per-organisation taxonomies. `TaxonomyTerm` remains global (AGENTS.md architecture).
- **N2** New taxonomy categories or arbitrary user-defined enums beyond the five compiled categories.
- **N3** New public API CRUD endpoints for taxonomy terms. The existing superadmin UI is the mutation surface for this change.
- **N4** A database foreign key from `Case.verdict` / `Alert.verdict` to `TaxonomyTerm`. That would be the strongest integrity model but requires a larger schema/wire compatibility change; record it as a future option.
- **N5** Live WebSocket propagation of taxonomy changes into already-open case/alert dialogs. “Immediate” means the next request/render; a page refresh is sufficient.
- **N6** Changing severity/TLP/PAP; those are fixed numeric domains, not taxonomy terms.
- **N7** Changing TheHive wire shapes. `verdict` remains a raw code string in JSON.
- **N8** Dynamic database CHECK constraints that query another table. PostgreSQL CHECK constraints cannot reference `core_taxonomyterm`; the plan uses service validation plus a structural CHECK.

---

## 4. Current State and Defect Map

| Layer | Current behaviour | Consequence |
|---|---|---|
| `core/enums.py` | `CASE_VERDICT_CHOICES`, `CASE_VERDICT_VALUES`, `CASE_CLOSING_VERDICTS` are hardcoded; same pattern for scope/type/classification/phase. | Runtime code cannot see new terms. |
| `core/models.py::TaxonomyTerm` | Global rows with `category`, `code`, `label`, `order`, `hidden`, `protected`, `is_closing`; unique `(category, code)`. | Data model already supports the requirement. |
| `core/migrations/0002_populate_taxonomy_terms.py` | Seeds `undefined` (protected, non-closing), `true_positive` and `false_positive` (protected, closing). | Defaults exist; do not rewrite the historical migration. |
| `TaxonomyTerm` UI | Superadmin create/edit/delete exists at `/taxonomy`, including the Verdict card and `is_closing` checkbox. | UI can add the term, but runtime ignores it. |
| `cases/update.py::_CASE_ENUM_FIELDS` | Maps `verdict` to `CASE_VERDICT_VALUES`; same for the other four. | Create/single PATCH/bulk reject a new term before close logic runs. |
| `cases/update.py::enforce_close_rule` | Already queries `TaxonomyTerm` for `is_closing` and builds the allowed list. | Partially dynamic, but unreachable for a new term because field validation fails first. |
| `cases/views.py::case_bulk_update` | Bypasses `apply_case_update`; calls `enforce_close_rule` separately. | Fixing only the applier would leave bulk inconsistent. |
| `cases/views.py` case-template create/update | Validates scope/type/phase against hardcoded `CASE_*_CHOICES`. | New case-type/scope/phase terms rejected by templates too. |
| `cases/migrations/0019...` | Removed case/case-template CHECKs for the five compiled enums. | Case DB no longer blocks new terms, but there is no structural verdict guard. |
| `alerts/models.py::Alert.verdict` | `choices=list(CASE_VERDICT_CHOICES)` and `in_values("alert_verdict_valid", ...)`. | Alert model rejects new terms via `full_clean`; DB rejects new terms on save/propagation. |
| `alerts/migrations/0015_alert_verdict.py` | Adds `alert_verdict_valid` CHECK. | Must be removed or propagated `red_team` fails at the database. |
| `ui/views.py` | Hardcoded `CASE_*_CHOICES` and `closing_verdict_choices` in case detail, alert detail, create/edit contexts, inline edit descriptors, and `_taxonomy_context`. | New terms do not appear; `choice_label` falls back to raw codes. |
| `ui/views.py::alert_close` | Rejects anything not in `CASE_CLOSING_VERDICTS`. | New closing verdicts cannot close alerts. |
| `core/taxonomy.py` | `create/update/delete_taxonomy_term` exists. Deletion checks only `Case` for verdict usage. | Alert usage is not considered; `undefined` could be made closing; last closing term could be deleted. |
| `core/serializers.py` | Emits raw verdict/classification codes. | No change required; codes remain stable. |
| Tests | `test_enum_contracts.py` expects `alert_verdict_valid`; verdict close/UI tests use the three seeded tokens. | Must be updated for the new source of truth and extended with dynamic-term coverage. |

---

## 5. Architecture

### 5.1 Decisions

| ID | Decision | Rationale |
|---|---|---|
| D1 | `TaxonomyTerm` is the runtime source of truth for the five compiled vocabularies. | Makes superadmin edits effective immediately; the table already exists. |
| D2 | `core/enums.py` remains static **bootstrap/default** data and numeric-domain definitions. It is not queried at runtime for pickers or validation. | Migrations and imports remain safe; no DB access at module import; historical migrations stay reproducible. |
| D3 | Add a read-only resolver module, `core/taxonomy_read.py`, with lazy `core.models` imports. | Avoids import cycles (`core.models` must not depend on `cases`/`ui`), keeps read concerns separate from `core.taxonomy` mutations. |
| D4 | No global cache in this change. UI views preload all five categories in one query; validation performs a single indexed `exists`/`values_list` query per category. | Immediate correctness beats micro-optimisation. A cache would require reliable invalidation and could violate G1. |
| D5 | Remove hardcoded vocabulary CHECKs from the DB; add `*_verdict_not_blank` structural CHECKs. Validate vocabulary in services. | PostgreSQL CHECK cannot query another table. A non-blank CHECK still catches empty-string corruption from raw writes. |
| D6 | Picker lists exclude `hidden=True`; validation accepts any existing term including hidden ones. | Hidden means “do not offer”, not “invalidate historical/API data”. Prevents edits of legacy records from failing. |
| D7 | Closing lists require `is_closing=True` and `hidden=False`; close validation requires an existing `case_verdict` term with `is_closing=True`. | Dynamic closing semantics with a safe UI. `undefined` is explicitly forced non-closing. |
| D8 | `Alert.verdict` uses the same `case_verdict` category and closing semantics as `Case.verdict`. | Preserves the existing propagation contract and TheHive-compatible token vocabulary. |
| D9 | Taxonomy changes remain superadmin-only and audit-logged through the existing UI service. | Least privilege; no new mutation surface. |
| D10 | No FK normalisation in this change. | Avoids a disruptive schema/wire migration; documented as a future hardening option. |

### 5.2 Proposed ADR

Create `docs/decisions/ADR-012-taxonomy-source-of-truth.md` covering:

- taxonomy as runtime source of truth for compiled enums;
- static enums as bootstrap/default only;
- no database lookup at import time;
- no global caching in v1;
- non-blank CHECK instead of dynamic vocabulary CHECK;
- hidden/protected/closing semantics;
- the future FK/trigger option and why it is deferred.

If the team prefers not to create a new ADR, record the same decisions in this plan and reference it from the implementation. The default is to create ADR-012.

### 5.3 Resolver contract

New module: `core/taxonomy_read.py`.

```python
CATEGORY_CASE_SCOPE = "case_scope"
CATEGORY_CASE_PHASE = "case_phase"
CATEGORY_CASE_TYPE = "case_type"
CATEGORY_DATA_CLASSIFICATION = "data_classification"
CATEGORY_CASE_VERDICT = "case_verdict"

def choices(category: str, *, include_hidden: bool = False) -> tuple[tuple[str, str], ...]: ...
def choices_by_category(
    categories: tuple[str, ...] = (...all five...),
    *,
    include_hidden: bool = False,
) -> dict[str, tuple[tuple[str, str], ...]]: ...
def codes(category: str, *, include_hidden: bool = True) -> tuple[str, ...]: ...
def exists(category: str, code: str) -> bool: ...
def get_term(category: str, code: str): ...  # TaxonomyTerm | None, lazy import
def is_closing_verdict(code: str) -> bool: ...
def closing_verdict_choices(*, include_hidden: bool = False) -> tuple[tuple[str, str], ...]: ...
def label(category: str, code: str) -> str: ...
```

Requirements:

- Import `TaxonomyTerm` inside functions, never at module top level.
- Order by `order`, then `code`.
- `choices()` hides `hidden=True` by default; `codes()` and `exists()` include hidden by default.
- `label()` returns the term label, or the code as a safe display fallback; empty/None returns `""`.
- `is_closing_verdict()` returns `False` for missing terms, `hidden` irrelevant, and explicitly `False` for `undefined`.
- `choices_by_category()` issues one query for all requested categories and groups in Python.
- No `lru_cache`, no module-level queryset, no app-registry access at import time.

### 5.4 Data model implications

- `TaxonomyTerm` needs no schema change.
- `Case.scope`, `Case.case_type`, `Case.data_classification`, `Case.phase`, `Case.verdict` need no schema change; they remain `CharField` codes.
- `Alert.verdict`: remove `choices`; keep `max_length=32`, default `"undefined"`, non-null.
- Remove `alert_verdict_valid`.
- Add `alert_verdict_not_blank` and `case_verdict_not_blank` (`~Q(verdict="")`).
- Migrations must normalise any legacy blank strings to `"undefined"` before adding the non-blank checks.
- No new FK; codes are deliberately denormalised for TheHive-compatible JSON and import/export.
- `TaxonomyTerm` remains global; no `owner_org`.

### 5.5 Bootstrapping and migration safety

- `core/migrations/0002_populate_taxonomy_terms.py` remains unchanged. Do not rewrite applied migrations.
- Runtime code may query taxonomy only after migrations have run.
- Migration files use `apps.get_model("core", "TaxonomyTerm")` for data migrations and static lists for defaults; they do not import `core.taxonomy_read`.
- `core/enums.py` stays importable with no database; it is the canonical source for default labels/tokens and numeric ranges.
- A new test should assert every default compiled-enum code in `core/enums.py` exists in the seeded taxonomy (guards drift), but it may use the test DB.
- If the taxonomy table is missing at runtime, fail closed with a server error rather than silently falling back to static values. Static fallback for vocabulary would let an unseeded/misconfigured deployment accept values the admin cannot see.

### 5.6 Caching / invalidation (explicitly deferred)

No cache in this change. If profiling later shows taxonomy queries are hot:

1. Add a version key in Django cache.
2. Bump it on `TaxonomyTerm` create/update/delete (and expose a `refresh` helper for bulk imports).
3. Keep request-scoped preloading in views.
4. Add a test that an edit is visible on the next request with the cache enabled.

Do not use `lru_cache` or model `choices=` as a cache.

---

## 6. API Changes

No new endpoints and no wire-shape changes are required.

### 6.1 Case create / update / bulk

- `POST /api/v1/case/`, `PATCH /api/v1/case/{id}`, and `PATCH /api/v1/case/_bulk` accept any existing taxonomy code for the five categories.
- Empty/`None` values continue to mean “keep the model default”, matching current tolerance.
- Unknown codes return the existing `CaseUpdateError` shape (`400`, `fields` with the field name).
- Error messages list the current dynamic codes, not the static enums.
- `data_classification` remains admin-gated.
- Bulk update continues to call `enforce_close_rule` per item; it must now resolve the same term list.
- Closing still requires a non-`undefined`, `is_closing=True` verdict and a non-empty closing comment.
- Reopening/leaving Closed is unchanged.

### 6.2 Case templates

- `POST/PATCH` case-template endpoints validate `scope`, `caseType`, and `phase` via the resolver.
- New terms work without changing the template payload shape.
- `verdict` is not a `CaseTemplate` field.

### 6.3 Alerts

- `PATCH /api/v1/alert/{id}` and alert bulk update do not currently accept `verdict`; keep that behaviour. Verdict changes on alerts come from case-close propagation and the UI alert-close flow.
- `Alert.verdict` serialization remains the raw code.
- `Alert.save()` / `full_clean()` must no longer reject a dynamic term because `choices` is removed.
- Propagation of a case verdict to linked alerts must succeed at the database layer after the migration.

### 6.4 Taxonomy API

- No term CRUD API in this change.
- Optional future extension: add a read-only `taxonomyTerms` collection to the aggregate taxonomy API so API clients can discover codes/labels. Record as an open question; not required for this plan’s acceptance.

### 6.5 Error contract

- Error bodies keep the existing `{"type", "message", "fields"}` shape.
- Close errors keep `ValidationError` with `verdict` or `comment` field errors.
- Alert-close UI flash messages become dynamic: list the currently configured closing verdicts rather than hardcoding “true positive or false positive”.

---

## 7. UI Changes

### 7.1 Case detail

- General tab labels for scope, type, classification, phase, and verdict use resolved display choices so edited labels appear immediately.
- Inline edit dialogs for the five fields use resolved picker choices, excluding hidden terms.
- The close dialog’s verdict select uses resolved closing choices (`is_closing=True`, `hidden=False`), ordered by `order`, `code`.
- A newly added `red_team` closing verdict appears in the next render of the close dialog.
- The close form remains a normal POST; HTMX is not required for correctness.
- No JavaScript-side hardcoded verdict list.

### 7.2 Case create/edit pages

- `ui/views.py` create/edit contexts at the existing hardcoded sites use `choices_by_category(...)`.
- `_inline_edit_fields` accepts the resolved maps (or calls the resolver once) so it cannot drift from the page context.
- `_case_create_fields.html`, `_case_edit_fields.html`, and `case_edit.html` keep their current variable names; only the context source changes.

### 7.3 Alert detail

- Alert-close dialog uses resolved closing verdict choices.
- Alert close view validates via `is_closing_verdict(verdict)`.
- Alert detail verdict display uses a resolved label map, not the static tuple.

### 7.4 Taxonomy page

- Verdict card continues to show all terms, including hidden and protected.
- `is_closing` remains editable for `case_verdict`; changing it takes effect on the next request.
- `protected` remains create-time only; `undefined` cannot be unprotected through the existing edit form.
- Create/edit/delete success continues to re-render or redirect via the existing HTMX/no-HTMX paths.

### 7.5 Hidden and protected semantics in UI

- Hidden terms remain visible in the taxonomy editor, excluded from pickers.
- Existing records holding a hidden term still render the term label where possible; if the display map includes hidden terms, use it for label rendering and keep a separate non-hidden map for selects.
- Protected terms cannot be deleted. `undefined` additionally cannot be made closing.

---

## 8. Phases and Tasks

### Phase 0 — Decisions and baseline

#### T0.1 — Write ADR-012 and record the source-of-truth decision
**Files:** `docs/decisions/ADR-012-taxonomy-source-of-truth.md`, this plan.

**Work:**
- Capture D1–D10, alternatives considered, and migration/bootstrapping constraints.
- Note that `core/enums.py` is bootstrap/default, not runtime authority.
- Note the deferred FK/trigger option.

**Acceptance criteria:**
- [ ] ADR exists and explicitly answers: source of truth, import-cycle policy, caching policy, DB CHECK policy, hidden/protected/closing semantics.
- [ ] ADR states why dynamic CHECK constraints are not used.
- [ ] This plan links to ADR-012.

#### T0.2 — Capture baseline
**Work:**
- Run the relevant conformance suites before changes: `tests/conformance/test_case_verdict_close.py`, `test_ui_case_verdict.py`, `test_enum_contracts.py`, `test_ui_taxonomy_editing.py`.
- Record any pre-existing failures; do not fix unrelated failures in this change.

**Acceptance criteria:**
- [ ] Baseline command and results are recorded in the implementation PR/notes.
- [ ] No pre-existing failure is attributed to this change.

---

### Phase 1 — Taxonomy resolver

#### T1.1 — Add `core/taxonomy_read.py`
**Files:** `core/taxonomy_read.py`, `tests/conformance/test_taxonomy_read.py`.

**Work:**
- Implement the contract in §5.3.
- Lazy-import `TaxonomyTerm`.
- Use `.values_list("code", "label")` with `order_by("order", "code")`.
- Add a helper to load all five categories in one query.
- Keep the module free of `core.enums` imports unless a static default label is genuinely needed; prefer DB rows only.

**Acceptance criteria:**
- [ ] Creating a `TaxonomyTerm` and immediately calling `choices("case_verdict")` includes it.
- [ ] Editing a label is reflected immediately.
- [ ] `include_hidden=False` excludes hidden terms; `codes()`/`exists()` still accept them.
- [ ] Ordering is deterministic (`order`, then `code`).
- [ ] Missing category/term returns empty/False, not an exception.
- [ ] Importing the module does not query the database.
- [ ] Unit tests cover empty category, hidden term, label edit, closing list, and `undefined` exclusion.

---

### Phase 2 — Service validation and close rule

#### T2.1 — Switch case field validation to the resolver
**Files:** `cases/update.py`, `cases/create.py`.

**Work:**
- Replace `_CASE_ENUM_FIELDS` with `_CASE_ENUM_CATEGORIES = {"scope": "case_scope", ...}`.
- In `_set_case_fields`, validate string enums with `exists(category, value)`.
- Build the error message from `codes(category)` so it reflects current terms.
- Preserve empty/`None` tolerance and the `data_classification` admin gate.
- Update `cases/create.py` imports/usages (`_CASE_ENUM_FIELDS` tolerance check) to the new mapping.
- Remove now-unused `CASE_*_CHOICES`/`CASE_VERDICT_VALUES` imports from these modules.

**Acceptance criteria:**
- [ ] A newly created `case_scope`/`case_type`/`data_classification`/`phase`/`case_verdict` term is accepted by case create and PATCH.
- [ ] Unknown codes still return the existing 400 error shape.
- [ ] Empty/`None` still keeps the default.
- [ ] Non-admin `data_classification` changes still return `AuthorizationError`.
- [ ] No runtime validation uses `core/enums.py` for these five fields.

#### T2.2 — Make the close rule taxonomy-only
**Files:** `cases/update.py`.

**Work:**
- In `enforce_close_rule`, use `get_term("case_verdict", verdict)` and `is_closing_verdict(verdict)`.
- Build the allowed list from `closing_verdict_choices()`.
- Reject `undefined` even if the row is misconfigured as closing.
- Keep the comment requirement unchanged.
- If no closing verdict exists, return a clear validation error: “No closing verdict is configured; ask an administrator.” Do not silently fall back to the static tuple.

**Acceptance criteria:**
- [ ] A case can close with a newly added `is_closing=True` term.
- [ ] A case cannot close with `undefined`, a non-closing term, an unknown code, or an empty verdict.
- [ ] A case cannot close without a comment.
- [ ] Re-closing an already-closed case remains a no-op for the close rule.
- [ ] The close error lists the currently configured closing verdicts.
- [ ] `case_bulk_update` inherits the same behaviour without duplicating the term query logic.

#### T2.3 — Switch case-template validation
**Files:** `cases/views.py`.

**Work:**
- Replace the hardcoded `allowed_scope`, `allowed_case_type`, and `allowed_phase` tuples in template create/update with resolver lookups.
- Preserve defaulting for empty/null values.
- Preserve existing error field names (`scope`, `caseType`, `phase`).

**Acceptance criteria:**
- [ ] A new case-type/scope/phase term is accepted by case-template create and update.
- [ ] Unknown template enum values still return the existing 400 shape.
- [ ] No `CASE_*_CHOICES` imports remain in `cases/views.py` solely for template validation.

---

### Phase 3 — Database migration and alert parity

#### T3.1 — Relax `Alert.verdict` and add structural CHECKs
**Files:** `alerts/models.py`, `alerts/migrations/00XX_relax_alert_verdict.py`, `cases/migrations/00XX_case_verdict_not_blank.py`.

**Work:**
- Remove `choices` from `Alert.verdict`; remove `CASE_VERDICT_CHOICES`/`CASE_VERDICT_VALUES` imports if no longer used.
- Remove `in_values("alert_verdict_valid", ...)` from `Alert.Meta.constraints`.
- Add `alert_verdict_not_blank` and `case_verdict_not_blank` as `~Q(verdict="")`.
- In each migration:
  1. normalise existing blank strings to `"undefined"` using the historical model;
  2. remove the old constraint where applicable;
  3. alter the field choices;
  4. add the non-blank constraint.
- Depend on the latest migration in each app. Do not depend on `core` for a vocabulary constraint.

**Acceptance criteria:**
- [ ] `Alert.objects.filter(pk=...).update(verdict="red_team")` succeeds after the migration.
- [ ] Blank verdicts are refused by the database on both tables.
- [ ] `Alert.full_clean()` no longer rejects a dynamic term.
- [ ] `makemigrations --check --dry-run` reports no changes.
- [ ] A case close that propagates a new verdict to linked alerts succeeds.
- [ ] Existing `undefined`/`true_positive`/`false_positive` data is unchanged.

#### T3.2 — Harden taxonomy mutations
**Files:** `core/taxonomy.py`, `tests/conformance/test_taxonomy_terms.py` (or existing taxonomy test file).

**Work:**
- `create_taxonomy_term`: reject `case_verdict` + `code="undefined"` + `is_closing=True`; force `is_closing=False` for `undefined` if you prefer normalisation over rejection.
- `update_taxonomy_term`: reject setting `is_closing=True` on `undefined`.
- Prevent turning off `is_closing` on the last closing verdict (otherwise closing becomes impossible).
- `delete_taxonomy_term`: for `case_verdict`, also check `Alert.objects.filter(verdict=term.code)`.
- Keep protected-term deletion refusal.
- Ensure `is_closing` remains meaningful only for `case_verdict`.
- Preserve audit payloads (`changed` includes `is_closing` when toggled).

**Acceptance criteria:**
- [ ] `undefined` cannot be made closing.
- [ ] The last closing verdict cannot be deleted or demoted.
- [ ] A verdict used by an alert cannot be deleted.
- [ ] A verdict used by a case still cannot be deleted.
- [ ] Protected terms cannot be deleted.
- [ ] Creating a term with `is_closing=True` for a non-verdict category still fails.
- [ ] Audit records reflect create/update/delete and `is_closing` changes.

---

### Phase 4 — UI integration

#### T4.1 — Resolve UI case/alert choices from taxonomy
**Files:** `ui/views.py`, `ui/templates/ui/_tab_general.html`, `ui/templates/ui/_case_close_form.html`, `ui/templates/ui/_alert_close_form.html`, `ui/templates/ui/case_edit.html`, `ui/templates/ui/_case_edit_fields.html`, `ui/templates/ui/_case_create_fields.html`.

**Work:**
- Add a small per-request helper in `ui/views.py`:
  ```python
  def _case_taxonomy_context(*, include_hidden_for_labels: bool = True) -> dict[str, Any]: ...
  ```
  It calls `choices_by_category(...)` once, returns picker maps and label maps, plus closing verdict choices.
- Replace the hardcoded contexts at the current sites:
  - alert detail (`~line 563`);
  - case detail (`~lines 1361–1366`);
  - `_inline_edit_fields` (`~lines 1458–1486`);
  - create page contexts (`~lines 772–775`, `~3567–3570`, `~3709–3712`);
  - `_taxonomy_context` (`~lines 4432–4489`).
- Use non-hidden choices for `<select>` controls.
- Use display choices including hidden for read-only labels in `_tab_general.html` and alert detail.
- Update `_inline_edit_fields` to accept the resolver output rather than reading module constants.
- Update template comments that mention `CASE_CLOSING_VERDICTS`; describe the taxonomy-backed closing list instead.
- Remove unused `CASE_*` imports from `ui/views.py` once all sites are migrated.

**Acceptance criteria:**
- [ ] A newly added verdict appears in the case General-tab inline edit and the close dialog on the next render.
- [ ] A newly added case type/scope/phase/classification appears in the corresponding create/edit controls.
- [ ] Toggling `is_closing` on an existing verdict immediately changes the close dialog list.
- [ ] Hidden terms do not appear in selects but existing records still show a usable label where feasible.
- [ ] No template or view hardcodes the verdict or closing-verdict list.
- [ ] The close forms remain plain POSTs with the existing names/IDs and CSRF contract.

#### T4.2 — Fix alert close validation
**Files:** `ui/views.py`.

**Work:**
- Replace `if verdict not in CASE_CLOSING_VERDICTS` with `if not is_closing_verdict(verdict)`.
- Build the failure message from `closing_verdict_choices()`.
- Keep the comment requirement, closed-status resolution, audit, and transaction unchanged.

**Acceptance criteria:**
- [ ] An alert can close with a newly added closing verdict.
- [ ] `undefined`, non-closing, and unknown verdicts are refused.
- [ ] An empty comment is refused.
- [ ] The flash message names the currently configured closing verdicts.

---

### Phase 5 — Tests, documentation, verification

#### T5.1 — Update existing conformance tests
**Files:** `tests/conformance/test_enum_contracts.py`, `tests/conformance/test_case_verdict_close.py`, `tests/conformance/test_ui_case_verdict.py`, `tests/conformance/test_ui_taxonomy_editing.py`, `tests/conformance/test_ui_p2_wiring.py` as needed.

**Work:**
- Remove `("alert", "verdict")` from `CHOICE_FIELDS` and `alert_verdict_valid` from `GRADED_DOMAIN`.
- Add a direct non-blank CHECK test for alert and case verdict if the new constraints are added.
- Keep the existing close/verdict/value tests; they should pass with the seeded taxonomy.
- Update any assertion that assumes the static tuple is the only source.
- Add a test that the taxonomy page’s verdict card still shows `is_closing` and protected rows.
- Add a test that `undefined` remains protected and non-closing.

**Acceptance criteria:**
- [ ] The enum-contract suite is green without asserting a hardcoded alert verdict vocabulary.
- [ ] Existing seeded verdict close tests remain green.
- [ ] The taxonomy UI tests cover the Verdict card and `is_closing` edit.
- [ ] No test asserts that only three verdicts can ever exist.

#### T5.2 — Add dynamic-taxonomy regression tests
**File:** `tests/conformance/test_taxonomy_verdict_runtime.py` (new).

**Required scenarios:**
1. Create `TaxonomyTerm(category="case_verdict", code="red_team", label="Red Team", is_closing=True)`.
2. Case API create/update accepts `red_team`; case close succeeds with `red_team` + comment.
3. Case close propagates `red_team` to linked alerts; DB accepts it.
4. Alert close UI succeeds with `red_team`.
5. Editing the term label updates the case General tab and the close option text.
6. Toggling `is_closing=False` removes it from the close list and makes close fail.
7. Toggling `is_closing=True` on an existing non-closing custom term adds it.
8. Hidden term is excluded from pickers but an existing record still renders; API can still read it.
9. `undefined` cannot be made closing; protected deletion remains refused.
10. Deleting a verdict used by an alert or case is refused.
11. Deleting/demoting the last closing verdict is refused.
12. A new `case_type`/`case_scope`/`phase`/`data_classification` term works through the corresponding case path.
13. Case-template create/update accepts a new scope/type/phase term.
14. Bulk case update accepts a new verdict and enforces close.

**Acceptance criteria:**
- [ ] Every scenario above has an automated test.
- [ ] At least one test fails if any of `_CASE_ENUM_FIELDS`, `CASE_CLOSING_VERDICTS`, or `alert_verdict_valid` is reintroduced.
- [ ] Tests use `TaxonomyTerm` creation, not hardcoded monkeypatching of enums, to prove runtime behaviour.

#### T5.3 — Documentation and handoff
**Files:** ADR-012, this plan, `docs/spec/deviations.md` if behaviour changes, verification report.

**Work:**
- Document that taxonomy is runtime authority; `core/enums.py` is bootstrap/default.
- Document hidden/protected/closing semantics and the non-blank CHECK choice.
- Record any deviation from this plan in the plan itself (do not silently diverge).
- Run the full relevant test suite, `makemigrations --check --dry-run`, and the staged secret scan.
- Hand off to `verifier` with the AC checklist; hand off to `security-auditor` for the taxonomy mutation and close-rule review.

**Acceptance criteria:**
- [ ] ADR and plan are updated with implementation notes.
- [ ] All ACs in Phases 0–5 are checked or explicitly marked not met with a reason.
- [ ] Verification report references this plan and ADR-012.
- [ ] No new secret is introduced; secret scan passes.

---

## 9. Security Review

| Area | Control |
|---|---|
| Authorization | Only superadmin can create/edit/delete `TaxonomyTerm` (existing UI gate). Case/alert verdict writes remain available to authorised case/alert writers. |
| Fail closed | Missing/empty taxonomy does not fall back to static vocabulary. Close with no configured closing verdict is refused with a clear error. |
| Input validation | Reuse `_clean_term_code`/`_clean_label` limits; codes are regex-constrained and immutable after create. Validate all verdict/classification inputs through the resolver. |
| Audit | Taxonomy mutations continue to write audit rows; include `is_closing` in `changed`. Close actions remain audited as today. |
| Integrity | Non-blank CHECKs catch empty-string corruption from raw writes. Vocabulary correctness is service-enforced because a CHECK cannot reference `TaxonomyTerm`. |
| Tenant isolation | Taxonomy is global by design and contains no tenant data. Case/alert writes remain org-scoped by existing resolvers. |
| Race conditions | A term deleted between validation and save can leave a stale code. The in-use delete checks reduce this; document it as a known limitation of the denormalised design. A future FK would close it. |
| DoS | `code`/`label` length limits, indexed `(category, order)`, and superadmin-only mutation limit abuse. Validation uses indexed lookups. |
| XSS | Labels are rendered through Django templates with autoescaping. Do not use `safe` for taxonomy labels. |
| Secrets | No secrets involved. Run the staged secret scan before commit. |

---

## 10. Testing Strategy

- **Unit:** resolver ordering, hidden, label fallback, `undefined` guard, empty category, no import-time query.
- **Service:** `_set_case_fields` for all five categories; close rule; case-template validation; taxonomy mutation guards; alert deletion guard.
- **API:** case create/single/bulk, case-template create/update, unknown-code 400 shape, `data_classification` admin gate.
- **DB:** non-blank CHECK on case/alert verdict; dynamic code accepted; old CHECK removed.
- **UI:** case General tab, inline edit, close dialog, alert close, taxonomy Verdict card, create/edit pages.
- **Regression:** existing verdict/close/UI/taxonomy suites; enum contracts updated.
- **Commands:**
  - `pytest tests/conformance/test_taxonomy_read.py tests/conformance/test_taxonomy_verdict_runtime.py`
  - `pytest tests/conformance/test_case_verdict_close.py tests/conformance/test_ui_case_verdict.py tests/conformance/test_enum_contracts.py`
  - `pytest tests/conformance/test_ui_taxonomy_editing.py tests/conformance/test_ui_p2_wiring.py`
  - `python manage.py makemigrations --check --dry-run`
  - `scripts/secret-scan.sh --staged`

---

## 11. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Import cycle via `core.models` | Medium | High | Lazy imports inside resolver functions; never import `cases`/`ui` from `core`. |
| Migration queries taxonomy table before it exists | Low | High | No resolver/DB access in migrations; use historical models and static seed data. |
| A new alert verdict rejected by leftover DB CHECK | High if missed | High | Explicit migration removes `alert_verdict_valid`; integration test propagates `red_team` to an alert. |
| Bulk path drifts from single update | Medium | High | Shared `_set_case_fields` + `enforce_close_rule`; dedicated bulk test. |
| Stale enum usage remains in UI | Medium | Medium | Repo-wide search for `CASE_VERDICT`/`CASE_CLOSING` after implementation; AC requires no runtime use. |
| Hidden terms render as raw codes | Medium | Low | Pass a hidden-inclusive display map for label rendering; picker map stays non-hidden. |
| Protected `undefined` made closing | Low | High | Service guard in create/update + test. |
| Last closing verdict removed | Low | High | Service guard on delete/demote + test. |
| Delete race leaves stale code | Low | Medium | In-use checks; document; future FK. |
| Cache added later violates immediacy | Low | Medium | Plan explicitly defers caching; any future cache must invalidate on taxonomy writes. |
| Tests still assert only three verdicts | Medium | Medium | Audit test suite for hardcoded closing lists; update enum-contract maps. |
| Removing `choices` weakens `full_clean` | Medium | Medium | Service validation on all case paths; alert close view validation; non-blank DB guard. Add tests. |

---

## 12. Open Questions

1. **All five categories now or verdict only?** Proposed: all five, because the same stale-enum defect affects scope/type/phase/classification and fixing only verdict leaves a known inconsistency. Verdict is the acceptance-critical path.
2. **Non-blank CHECK or no CHECK?** Proposed: add `case_verdict_not_blank` and `alert_verdict_not_blank`. It is cheap defense-in-depth without pretending the DB can enforce the vocabulary.
3. **Hidden terms in API/close validation:** Proposed: accept existing hidden terms for writes/legacy data, exclude hidden from pickers and closing UI. Confirm whether hidden closing terms should be rejected entirely.
4. **Taxonomy term API:** Proposed defer. Add a read-only `taxonomyTerms` collection later if API clients need discovery; no mutation API in this change.
5. **Protected `undefined` immutability:** Proposed: undeletable, forced non-closing, label/order editable, code immutable. Confirm whether label edits should be forbidden too.
6. **Realtime taxonomy update:** Proposed out of scope. “Immediate” means next request/render; no WebSocket broadcast to open dialogs.
7. **Future FK:** Proposed defer. Record as a hardening option if stale codes become a real issue.

---

## 13. Implementation Brief (for `django-backend` / `django-frontend`)

**Read first:** this plan, ADR-012, `PLAN-2026-10-23-taxonomy-phase2-compilation.md`, `AGENTS.md`.

**Backend work order:**
1. `core/taxonomy_read.py` + unit tests.
2. `cases/update.py` + `cases/create.py` resolver validation and close rule.
3. `cases/views.py` case-template validation.
4. `alerts/models.py` + `alerts/migrations/00XX_relax_alert_verdict.py` + `cases/migrations/00XX_case_verdict_not_blank.py`.
5. `core/taxonomy.py` mutation guards.
6. Update conformance tests; add dynamic runtime tests.

**Frontend work order:**
1. `ui/views.py` per-request taxonomy context helper.
2. Replace hardcoded contexts and `_inline_edit_fields`.
3. Update `_tab_general.html` display maps and close-form comments.
4. Fix `alert_close` dynamic validation.
5. Add/update UI conformance tests.

**Key file paths:**
- `core/taxonomy_read.py` (new)
- `core/enums.py` (comments only; demote to bootstrap)
- `core/taxonomy.py`
- `cases/update.py`
- `cases/create.py`
- `cases/views.py`
- `alerts/models.py`
- `alerts/migrations/00XX_relax_alert_verdict.py` (new)
- `cases/migrations/00XX_case_verdict_not_blank.py` (new)
- `ui/views.py`
- `ui/templates/ui/_tab_general.html`
- `ui/templates/ui/_case_close_form.html`
- `ui/templates/ui/_alert_close_form.html`
- `ui/templates/ui/_case_edit_fields.html`
- `ui/templates/ui/_case_create_fields.html`
- `ui/templates/ui/case_edit.html`
- `tests/conformance/test_taxonomy_read.py` (new)
- `tests/conformance/test_taxonomy_verdict_runtime.py` (new)
- `tests/conformance/test_enum_contracts.py`
- `docs/decisions/ADR-012-taxonomy-source-of-truth.md` (new)

**Definition of done:**
- [ ] A taxonomy term created through `/taxonomy` is accepted by case create/update/bulk and alert close without a restart or code change.
- [ ] A term’s `is_closing` toggle changes the close dialogs and close enforcement on the next render.
- [ ] `Alert.verdict` no longer has a hardcoded `choices`/CHECK vocabulary; propagation of a new case verdict to alerts succeeds.
- [ ] `undefined` remains protected, default, and non-closing; the last closing verdict cannot be removed.
- [ ] No runtime path uses `CASE_VERDICT_CHOICES`, `CASE_VERDICT_VALUES`, or `CASE_CLOSING_VERDICTS` for validation or pickers.
- [ ] Migration checks, targeted conformance suites, and staged secret scan pass.
- [ ] Verification report maps every AC above to met/not met, with deviations recorded in this plan.

---

## 14. Acceptance Criteria Summary

- [ ] **AC-1:** Superadmin creates a `case_verdict` term with `is_closing=True`; it appears in the next case close dialog and alert close dialog.
- [ ] **AC-2:** The new term is accepted by case create, single PATCH, bulk PATCH, and case close.
- [ ] **AC-3:** Closing a case with the new term propagates it to linked alerts without a DB error.
- [ ] **AC-4:** Editing the term label updates all verdict labels on the next render.
- [ ] **AC-5:** Toggling `is_closing` off removes it from close dialogs and makes close validation fail; toggling on reverses both.
- [ ] **AC-6:** `undefined` remains protected, default, and never satisfies the close rule.
- [ ] **AC-7:** A verdict used by a case or alert cannot be deleted; the last closing verdict cannot be removed.
- [ ] **AC-8:** Case templates accept new scope/type/phase terms.
- [ ] **AC-9:** No hardcoded runtime verdict/closing list remains in Python or templates.
- [ ] **AC-10:** `makemigrations --check --dry-run` is clean; targeted tests and secret scan pass.
