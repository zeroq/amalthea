# PLAN-2026-10-23 — Taxonomy Phase 2: Editable Compiled Enums

**Author:** planner  
**Date:** 2026-10-23  
**Status:** Draft (pending approval)  
**Scope:** Make the five compiled enums editable via UI/API (superAdmin only): Case Scope, Case Phase, Case Type, Data Classification, Verdict.  
**Related:** `docs/planning/PLAN-2026-10-22-taxonomy-editing.md` (§11), `docs/planning/BRIEF-2026-10-22-taxonomy-editing.md`, ADRs as applicable  
**Reference:** TheHive vocabulary management patterns

## 1. Goal

Allow superAdmins to create, rename/edit (code/label/order/hidden), and delete terms for:
- `case_scope` (CASE_SCOPE_CHOICES)
- `case_phase` (CASE_PHASE_CHOICES)  
- `case_type` (CASE_TYPE_CHOICES)
- `data_classification` (DATA_CLASSIFICATION_CHOICES)
- `case_verdict` (CASE_VERDICT_CHOICES)

All changes are global, audited, and reflected in pickers/forms/validation throughout the app.

## 2. Non-goals

- Per-org taxonomy scoping (taxonomy remains global)
- Making non-superAdmin users able to edit
- Changing wire/API shapes of case/alert/observable objects beyond how choices are sourced
- Bundling other taxonomy features

## 3. Current State Summary

- Five enums in `core/enums.py` with fixed choices
- Used in `Case` and `CaseTemplate` models with `choices=` and DB CheckConstraints via `in_values()`
- Used in `cases/update.py` for validation and `CASE_CLOSING_VERDICTS`
- Used in UI views for form contexts and taxonomy page
- Used in serializers, forms logic, templatetags
- ~164 references across codebase
- Taxonomy page currently shows them as read-only cards (Phase 2 planned)

## 4. Design Decisions (from plan + analysis)

| ID | Decision | Rationale |
|---|---|---|
| D1 | Use Option B1: keep fields as `CharField` with string codes, drop `choices=` and DB CheckConstraints, validate dynamically against DB | Smaller migration churn, less serializer/form breakage; integrity enforced at app level with unique constraint on DB |
| D2 | New model `TaxonomyTerm` in `core/models.py` (or `cases/models.py` - follow existing patterns) with category, code, label, order, hidden, protected, is_closing | Central place, easy to query/cached |
| D3 | `undefined` (verdict) and `unset` (all others) are `protected=True` and cannot be deleted/renamed in ways that break defaults | Prevents breaking fresh cases/templates |
| D4 | `is_closing` on verdict terms replaces hardcoded `CASE_CLOSING_VERDICTS`; `true_positive` and `false_positive` start as closing+protected, `undefined` as non-closing+protected | Flexible for future verdicts |
| D5 | SuperAdmin only for mutations; uniform 404 for non-superAdmin (same as Phase 1) | Consistent security posture |
| D6 | Global taxonomy (no org scoping) | Same as Phase 1 |
| D7 | Audit all mutations via `identity.audit.record_taxonomy_change` with `target_org=None` | Consistent audit trail |
| D8 | In-use checks prevent deletion of terms referenced by existing Cases/CaseTemplates | Data integrity |
| D9 | UI follows same pattern as Phase 1: modals, HTMX 422, plain POST errors, HX-Redirect on success | Consistent UX |

## 5. Data Model

### New Model: TaxonomyTerm

```python
class TaxonomyTerm(UUIDModel, TimeStampedModel):
    CATEGORY_CHOICES = (
        ("case_scope", "Case Scope"),
        ("case_phase", "Case Phase"),
        ("case_type", "Case Type"),
        ("data_classification", "Data Classification"),
        ("case_verdict", "Case Verdict"),
    )
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)
    code = models.CharField(max_length=64)  # e.g., "unset", "true_positive"
    label = models.CharField(max_length=128)
    order = models.IntegerField(default=0)
    hidden = models.BooleanField(default=False)
    protected = models.BooleanField(default=False)  # cannot be deleted
    is_closing = models.BooleanField(default=False)  # verdict only, meaningful for case_verdict

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "code"], name="taxonomy_term_category_code_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["category", "order"], name="taxonomy_term_cat_order_idx"),
        ]
        ordering = ["category", "order", "code"]

    def __str__(self):
        return f"{self.category}:{self.code}={self.label}"
```

### Migration Strategy
1. Create TaxonomyTerm model
2. Data migration: populate from core.enums values (preserve codes/labels/order)
   - Set protected=True for 'unset' in scope/phase/type/data_classification
   - Set protected=True for 'undefined' in verdict
   - Set protected=True, is_closing=True for 'true_positive', 'false_positive'
   - Set protected=False, is_closing=False for 'undefined'
3. Remove choices from Case and CaseTemplate fields
4. Remove CheckConstraints from Case and CaseTemplate Meta
5. Keep defaults as-is (strings) - defaults don't need to change
6. Update models to not import from core.enums for choices in a breaking way? Or keep for backward compat during transition

## 6. API Design

### Endpoints (new)
- `GET /api/v1/taxonomy/terms` - list all terms (all authenticated) - optional filter by category
- `POST /api/v1/taxonomy/terms` - create term (superAdmin only)
- `GET /api/v1/taxonomy/terms/{id}` - get term (all authenticated)
- `PATCH /api/v1/taxonomy/terms/{id}` - update term (superAdmin only)
- `DELETE /api/v1/taxonomy/terms/{id}` - delete term (superAdmin only)

Or follow existing pattern - could add to core views or create taxonomy app? Check existing structure.

Also need to update how existing APIs work - case/alert endpoints accept scope/phase/etc as strings, validation will move to dynamic lookup.

### Authorization
Same as Phase 1: unsafe methods require superAdmin, return uniform 404 for non-superAdmin.

### Validation
- code: required, max 64, alphanumeric/underscore, unique per category
- label: required, max 128
- category: must be valid choice
- protected terms: cannot delete; certain fields may be restricted on protected terms (code/label?)
- is_closing only meaningful for case_verdict category
- in-use check: cannot delete if used by any Case or CaseTemplate

## 7. UI Design

### Taxonomy page changes
- Replace read-only cards for the 5 enums with editable cards (like Phase 1) for superAdmin
- Show for all authenticated users (read-only for non-superAdmin)
- Add/Edit/Delete modals similar to Phase 1
- Show badges: Hidden, Protected (maybe), Closing (for verdict)
- Disable delete for protected or in-use terms

### Routes (9 new, following Phase 1 pattern)
- `ui-taxonomy-term-create` (POST)
- `ui-taxonomy-term-edit` (POST)
- `ui-taxonomy-term-delete` (POST)
- Or more specific routes per category?

Or single set of routes that take category/id - cleaner.

### Forms
New forms in `ui/taxonomy_forms.py` or separate module for term forms.

## 8. Integration Points

### cases/update.py
- Replace `CASE_CLOSING_VERDICTS` checks with dynamic lookup from DB
- `enforce_close_rule()` needs to check `is_closing` on verdict term
- Need to handle fallback if term missing? Or ensure migration is complete

### core/enums.py
- May keep enums for backward compatibility during transition or remove
- `CASE_CLOSING_VERDICTS` needs to become dynamic or be derived

### UI views
- Replace direct imports of choice tuples with DB queries or cached lookups
- `_taxonomy_context` needs to include terms grouped by category

### Serializers
- Check if any serializers validate these fields against enums

### Templetags
- `ui_tags.py` has `human_label` for choices

## 9. Caching Strategy
Since taxonomy is global and changes infrequently but needs to be reflected, consider simple caching or just query DB (terms table will be small).

## 10. Migration Plan
Need to be careful about order:
1. Create TaxonomyTerm model
2. Create migration to add model
3. Data migration to populate from enums
4. Update models to remove choices/checks (or make them flexible)
5. Update code to use dynamic lookups
6. Tests

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Breaking existing cases with invalid values | High | Data migration must be complete before removing constraints; add fallback validation |
| Performance of DB lookups everywhere | Medium | Cache terms in memory/Redis, or use select_related efficiently; terms are small |
| Migration on large DB | Medium | Data migration is simple inserts, should be fast |
| Circular imports | Medium | Careful about where TaxonomyTerm is imported from |
| CASE_CLOSING_VERDICTS used everywhere | High | Need to update all usages or provide compatibility function |

## 12. Acceptance Criteria

### AC1: Model & Migration
- [ ] TaxonomyTerm model created with all required fields
- [ ] Migration creates table and populates from existing enums
- [ ] Protected terms marked correctly
- [ ] is_closing set correctly for verdicts

### AC2: Case/CaseTemplate models updated
- [ ] choices removed from scope/phase/case_type/data_classification/verdict
- [ ] CheckConstraints removed
- [ ] Defaults still work
- [ ] Existing data unaffected

### AC3: Validation updated
- [ ] cases/update.py uses dynamic closing verdict check
- [ ] All validation uses TaxonomyTerm lookup
- [ ] Invalid codes rejected with appropriate errors

### AC4: API
- [ ] CRUD endpoints for taxonomy terms (superAdmin only)
- [ ] Non-superAdmin gets 404 on writes
- [ ] GET open to all authenticated
- [ ] Audit logging works

### AC5: UI
- [ ] Taxonomy page shows editable cards for all 5 categories (superAdmin)
- [ ] Create/edit/delete modals work
- [ ] Hidden terms shown with badge, excluded from pickers
- [ ] Protected terms cannot be deleted
- [ ] In-use terms cannot be deleted
- [ ] Balanced layout maintained

### AC6: Pickers/Forms
- [ ] Case create/edit forms use terms from DB (respecting hidden)
- [ ] All places using choices now use DB terms
- [ ] templatetags work

### AC7: Tests
- [ ] All existing tests pass
- [ ] New tests for taxonomy term CRUD
- [ ] Tests for closing verdict logic with dynamic terms

### AC8: Quality
- [ ] make check passes
- [ ] Full test suite passes

## 13. Open Questions

1. Where should TaxonomyTerm live - `core/models.py` or new `taxonomy` app?
2. Should we keep `core.enums` exports for backward compat or remove?
3. How to handle caching of terms?
4. Should API be at `/api/v1/taxonomy/terms` or nested differently?
5. Do we need to support reordering terms via UI?

## 14. Implementation Plan Phases

### Phase 2a: Foundation
- Create model and migration
- Data migration
- Basic service layer

### Phase 2b: Model integration
- Update Case and CaseTemplate models
- Update validation in cases/update.py
- Update core.enums or provide compatibility

### Phase 2c: API
- Add CRUD endpoints
- Authorization
- Audit

### Phase 2d: UI
- Update taxonomy page
- Add forms/views/routes
- Update all pickers

### Phase 2e: Cleanup & Testing
- Update tests
- Remove old code
- make check, full suite
