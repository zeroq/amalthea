# Rejected Observables - Management UI Plan
# Rejected Observables - Management UI Plan

## Summary
This plan covers Phase 3 of the Rejected Observables feature: building the frontend and management UI for analysts to audit, filter, and promote rejected items.

## Goals
- Provide a dedicated list view for all rejected items within an organization.
- Implement "Promotion" capability to move a rejected item back into the active workflow.
- Ensure the UI is keyboard-accessible and consistent with the Amalthea design system (Dark/Slate).
- Ensure strict organizational isolation: users only see rejects from their own organization.

## Non-goals
- Automated re-promotion based on similarity.
- Batch processing of multiple promotions (currently a single-action focus).
- Analytics on rejection rates.

## Architecture
- **Backend:** Django 5.x (Views/URLs) + DRF (for Promotion Action).
- **Frontend:** Django Templates + HTMX + Tailwind CSS.
- **Interactive Elements:** `<dialog>` for confirmation modals, managed by HTMX to keep the page state consistent.
- **Real-time:** Use Django Channels to broadcast a notification if an analyst is currently viewing a promoted item's case (optional refinement).

## Data Model
The model is already implemented as `RejectedObservable` in `observables/models.py`. 
Key fields for this phase:
- `owner_org`: Primary filter for isolation.
- `reason`: Categorization of why it was rejected.
- `is_promoted`: The target state for the promotion action.

## API & Views
### 1. List View (Page)
`GET /rejected-observables/`
- Filters: `owner_org` (enforced), `reason`, `status`.
- Display: List of items with ID, Type, Value (shortened), and Reason.

### 2. Promotion Action
`POST /rejected-observables/{id}/promote/`
- Logic:
    1. Validate ownership of the `RejectedObservable`.
    2. Update `is_promoted = True`.
    3. Return a success message and updated item info.

## UI/UX
- **Organization Context:** The list must only show items where `owner_org` matches the logged-in user's organization.
- **Layout:**
  - Table of results with "Reason" as a badge (color-coded by severity: Low, Medium, High).
  - "Promote" button in the Actions column.
- **Confirmation Modal:** 
  - Triggered by the Promote button.
  - Displays details of what will happen (e.g., "This item will be promoted to a Proposed state for Review").
  - Action: "Confirm Promotion" and "Cancel".
- **Accessibility:**
  - Keyboard focusable buttons.
  - `?` cheat-sheet availability.
  - Semantic HTML (`<article>`, `<button>`, `<table>`).

## Phases

### Phase 1: List View & Filtering (Backend/Frontend)
**Goal:** Display the list of rejected items with basic filters.
- **Tasks:**
    - Create `RejectedObservableListView` in `observables/views.py`.
    - Create a corresponding URL in `urls.py`.
    - Implement `owner_org` enforcement on the queryset.
    - Build the initial template using Tailwind CSS components.
- **Acceptance Criteria (AC):**
    - [ ] User can see all rejected items for their organization.
    - [ ] User cannot see records from other organizations (403/Empty).
    - [ ] Results are paginated and filterable by reason.

### Phase 2: Promotion Action & Modal
**Goal:** Enable the interactive promotion of a rejected item.
- **Tasks:**
    - Create a `PromotionView` for the `POST /promote/` action.
    - Implement the "Promote" button in the list template.
    - Build the Confirmation Modal using the `<dialog>` element.
    - Wire up the HTMX call to handle the promotion state change without a full page reload.
- **Acceptance Criteria (AC):**
    - [ ] Clicking "Promote" opens a modal.
    - [ ] Confirming in the modal updates the record and closes the modal.
    - [ ] The UI reflects the updated `is_promoted` status immediately without a refresh.

### Phase 3: Contextual Links & Styling
**Goal:** Final polish and integration with Alert details.
- **Tasks:**
    - Add a link to the `Alert` that triggered the rejection.
    - Ensure "Reason" badges are color-coded based on the `RejectedReason`.
    - Verify all buttons have correct ARIA labels and keyboard focus.
- **Acceptance Criteria (AC):**
    - [ ] Clicking the Alert ID opens the corresponding Alert detail page.
    - [ ] All labels and icons meet WCAG contrast requirements in dark mode.

## Security
- **Tenant Isolation:** All queries for `RejectedObservable` MUST include `.filter(owner_org=request.user.organization)`.
- **SuperAdmin Override:** Ensure SuperAdmins can view all records across organizations (if required by policy).

## Testing
- **Unit Tests:** Test the Promotion view's permission logic.
- **Integration Tests:** Verify that a promoted item actually appears in the `ObservableSuggestion` list.
- **Manual QA:** Verification of the modal behavior and keyboard navigation.

## Risks
- **Data Leakage:** Risk of showing rejected items from other organizations if the QuerySet filter is missed.
- **Double Promotion:** Ensure promotion logic handles cases where a record is already promoted or in an invalid state.

## Open Questions
1. Should "Promoted" items stay in the Rejected list or be moved to a "History" view? (Decision: Keep in list but filtered by status for now).
