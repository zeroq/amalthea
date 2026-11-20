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
- **Interactive Elements:** `<dialog>` for confirmation modals, rendered by the shared `ui/_modal.html` component and driven by `ui/static/ui/modal.js`; HTMX swaps rows and emits `HX-Trigger-After-Swap` to close the source dialog.
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
  - Table of results with "Reason" as a text-labelled badge; each `REJECTED_REASON_CHOICES` token gets a distinct dark-theme colour so severity is never signalled by colour alone.
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
    - Create `rejected_observables_list` in `ui/views/rejected_observables.py`.
    - Register `ui-rejected-observables` / `ui-rejected-observable-promote` in `ui/urls.py`.
    - Implement `owner_org` enforcement on the queryset.
    - Build the initial template using Tailwind CSS components.
- **Acceptance Criteria (AC):**
    - [x] User can see all rejected items for their organization.
    - [x] User cannot see records from other organizations (empty tenant-scoped queryset).
    - [x] Results are paginated and filterable by reason.

### Phase 2: Promotion Action & Modal
**Goal:** Enable the interactive promotion of a rejected item.
- **Tasks:**
    - Create a `PromotionView` for the `POST /promote/` action.
    - Implement the "Promote" button in the list template.
    - Build the Confirmation Modal using the `<dialog>` element.
    - Wire up the HTMX call to handle the promotion state change without a full page reload.
- **Acceptance Criteria (AC):**
    - [x] Clicking "Promote" opens a modal.
    - [x] Confirming in the modal updates the record and closes the modal.
    - [x] The UI reflects the updated `is_promoted` status immediately without a refresh.

### Phase 3: Contextual Links & Styling
**Goal:** Final polish and integration with Alert details.
- **Tasks:**
    - Add a link to the `Alert` that triggered the rejection.
    - Ensure "Reason" badges are color-coded based on the `RejectedReason`.
    - Verify all buttons have correct ARIA labels and keyboard focus.
- **Acceptance Criteria (AC):**
    - [x] Clicking the Alert ID opens the corresponding Alert detail page.
    - [x] All labels and icons meet WCAG contrast requirements in dark mode. **Manual browser QA passed** (see `VERIFICATION-2026-11-18-rejected-observables-ui-2026-11-20-083003.md`).

## Security
- **Tenant Isolation:** All queries for `RejectedObservable` MUST go through `observables.rejected.scoped_rejected_observables()` (or an equivalent `identity.scoping.org_q("owner_org_id", user)` filter). The project user field is `org`, not `organization`.
- **SuperAdmin Override:** Ensure SuperAdmins can view all records across organizations (if required by policy).
- **Viewer Guard:** Both the UI management page and the JSON list/promote endpoints deny `viewer` role; mutation returns 403 (API) / 404 (UI).

## Testing
- **Unit/Conformance Tests:** `tests/conformance/test_rejected_observables_ui.py` covers list rendering, dialog wiring, reason badges, named alert links, plain/HTMX promotion, and viewer denial. `tests/conformance/test_rejected_observables_api.py` covers tenant scoping, empty filter params, and API viewer denial.
- **Promotion Semantics:** Promotion sets `RejectedObservable.is_promoted = True`; it deliberately does **not** create an `ObservableSuggestion`.
- **Manual QA:** Keyboard focus, Esc-to-close, Tab trap, and WCAG-AA contrast were confirmed in a browser on 2026-11-20; JS/DOM behaviour is also pinned by textual conformance tests.

## Risks
- **Data Leakage:** Risk of showing rejected items from other organizations if the QuerySet filter is missed.
- **Double Promotion:** Ensure promotion logic handles cases where a record is already promoted or in an invalid state.

## Deviations / implementation notes
- The UI list at `/rejected-observables/` is paginated; the JSON API list at `/api/v1/rejected-observables/` remains unpaginated to preserve the project's existing top-level list-route conformance contract.
- Promotion marks `RejectedObservable.is_promoted = True`; it does not create an `ObservableSuggestion` automatically. This follows the Promotion Action logic above and avoids double-promoting artifacts that were already suppressed.
- Status filter wire tokens are `pending` and `promoted`; reason filter wire tokens are the `REJECTED_REASON_CHOICES` keys. Empty query params are treated as "no filter".
- The promotion dialog uses a per-row `<dialog id="promote-modal-<uuid>">` rendered from `ui/_modal.html`; `HX-Trigger-After-Swap: {"closePromoteModal": "<dialog-id>"}` is handled in `ui/static/ui/modal.js`. The button remains a real POST form submit for no-JS fallback.
- The JSON list endpoint denies `viewer` role with 403 to match the UI page guard.
- The left-rail Settings submenu in `ui/templates/ui/base.html` is a separate strip from `ui/_settings_nav.html`; it now expands on `ui-rejected-observables` (via the `is_settings_route` template filter) and lists both Agent access and Rejected observables for admin/superAdmin.
- The rejected-observables page now follows the Agent Access/settings layout conventions: shared heading block, settings tab strip, `card` + `card-header`, `filter-bar`, `table-wrap`, `data-table`, list summary/pager, and shared `pill`/`icon-link` components. Reason-specific pill colours live in `app.src.css`.

## Open Questions
1. Should "Promoted" items stay in the Rejected list or be moved to a "History" view? (Decision: Keep in list but filtered by status for now).
