# Rejected Observables Management Plan

## Summary
Implement a management system for "Rejected Observables"—entities that were identified during automated analysis but determined by an analyst to be false positives or irrelevant. This system provides a way to suppress these items from reappearing in future analyses while preserving the reason for their rejection for audit and training purposes.

## Goals
- Provide a structured way to reject specific observations without cluttering the active `Observable` table.
- Ensure rejected values are automatically suppressed in the extraction/mapping pipeline.
- Support organization-specific suppression to prevent cross-tenant interference.
- Allow for "Promotion" of a rejected item back into the active workflow.

## Non-goals
- **No automated full-text matching:** The system will not automatically suggest rejections based on similarity scores (that is covered by the `Assistant` and `Similar` features).
- **No cross-org suppression:** A rejected IP in Org A must not be suppressed in Org B.
- **Not a replacement for Alert filters:** This handles specific *values* (e.g., an IP), not alert types or rules.

## Architecture
- **Backend:** Django 5.x, DRF, and Postgres.
- **Database:** New `RejectedObservable` model.
- **Suppression Logic:** Modification to the `ExtractionEngine` to check the `RejectedObservable` table during the extraction of entities from raw logs.
- **Real-time Updates:** Use Django Channels/WebSockets to notify analysts if a promoted item affects an active investigation.

## Data Model

### RejectedObservable (New Model)
- `id (UUID)`
- `alert_id (FK, Unique)`: The specific alert that triggered the rejection.
- `organization (FK)`: The scope of suppression.
- `value (String)`: The extracted value (e.g., IP, Domain).
- `reason (Enum)`: Dropdown of common reasons (False Positive, Known Good, Too Noisy, etc.).
- `is_promoted (Boolean)`: Status flag for the Promote action.
- `created_at (Timestamp)`
- `notes (Text)`: Optional analyst notes.

## API Contracts

### 1. List Rejected Observables
`GET /api/v1/rejected-observables/`
- Query params: `org`, `reason`, `status`.
- Returns paginated list of `RejectedObservable` records.

### 2. Promotion Action
`POST /api/v1/rejected-observables/{id}/promote/`
- Action: Toggles `is_promoted` to `True`.
- Response: 200 OK, returns the updated record.

## UI/UX
- **Location:** A new tab in the **Case Detail** view (or a dedicated section in the analyst workbench) for "Rejected Items".
- **Interaction:**
  - List of records with "Reason" tags and "Action" buttons.
  - "Promote" button triggers a confirmation modal (`<dialog>` element).
  - Promotion of an item marks it as "Eligible for Review" in the UI, moving it to a secondary list or highlighting it.
- **Theme:** Dark/Slate (standard Amalthea design).

## Phases

### Phase 1: Database & Model (Backend)
- Create `RejectedObservable` model.
- Migration scripts to ensure no-orphans on `Alert` deletion.
- Registration in Admin for initial testing.
- **AC:** Model can be created/retrieved via `python manage.py shell`.

### Phase 2: Suppression Engine (Logic)
- Update `ExtractionEngine` to query `RejectedObservable` before finalizing an observation.
- Logic: If `value` exists in `RejectedObservable` for current `organization`, mark as `REJECTED` and do not add to Case/Alert.
- **AC:** Mocked log entry containing a rejected IP does not appear in the analyst's result list.

### Phase 3: Frontend & Management UI
- Create `RejectedObservableList` view and template.
- Implement "Promote" button and Confirmation Modal.
- Link to `Alert` details from the rejection list.
- **AC:** Analyst can see a list of rejected items, select a reason, and confirm a promotion.

### Phase 4: Permissions & Verification
- Apply SuperAdmin overrides for cross-org visibility.
- Run full conformance tests on organization boundaries.
- **AC:** Regular analyst cannot see results from other organizations; SuperAdmin can.

## Security
- **Data Isolation:** Ensure `Organization` filter is applied to every query for `RejectedObservable`.
- **Audit Logging:** Every "Promote" action must be logged in the `AuditLog`.
- **Safe Records:** Ensure no raw alert payloads are stored in the `RejectedObservable` model (only the ID link).

## Testing
- **Unit Tests:** Test suppression logic with various `Organization` IDs.
- **Integration Tests:** Verify Promotion action updates DB state.
- **Concurrency:** Ensure promotion/rejection doesn't deadlock on high-frequency alerts.

## Risks
- **False Positives in Suppression:** A rejected IP might be a legitimate indicator in a different context (solved by the organization-specific scope).
- **Database Scale:** `RejectedObservable` table could grow large; ensure GIN/Index on `value` and `organization`.

## Open Questions
1. Should we support "Temporary" rejections (TTL)? (Decision: No, for now.)
2. Does "Promote" actually remove it from the Rejected list? (Decision: Yes, or move to a 'Reviewed' status.)
