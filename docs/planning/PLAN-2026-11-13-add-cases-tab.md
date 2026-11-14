# Plan: Add Cases Tab to Case Template Detail

## Summary
This plan describes the addition of a "Cases" tab to the `case_template_detail` view. This allows users to see all cases that were created using a specific template directly from the template's detail page, providing a better workflow for analyzing template usage and verifying outputs.

## Goals
- Add a "Cases" tab to `case_template_detail.html`.
- Display a list of cases associated with the current template.
- Show a count badge on the "Cases" tab label.
- Maintain UI consistency with `alert_detail.html`.

## Non-goals
- Modifying the underlying `Case` or `CaseTemplate` models.
- Adding global search filters for the cases list (MVP only shows all related cases).

## Architecture
- **Backend:** Update the `case_template_detail` view to include a `tabs` list.
- **Frontend:** Utilize the `tabs.js` and the HTML structure already established in `alert_detail.html`.
- **Query:** Use the `cases_from_template` related name on `CaseTemplate`.

## Data Model & APIs
The logic relies on the existing `Case` model relationship:
```python
source_template = models.ForeignKey(
    "CaseTemplate",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="cases_from_template",
)
```

## UI/UX
- **Tab Labels:**
  - General
  - Cases (count)
- **Table Columns:**
  - **ID:** The internal case number.
  - **Title:** Link to the case detail page.
  - **Status:** Current status of the case.
  - **Owner:** The user assigned to the case.
- **Accessibility:** Maintain the current standard of `role="tab"`, `aria-controls`, and keyboard navigation links.

## Phases

### Phase 1: View Updates
- **Task:** Update `case_template_detail` view.
- **Description:** 
  - Inject `tabs` list containing:
    - `{'slug': 'general', 'label': 'General'}`
    - `{'slug': 'cases', 'label': 'Cases'}`
  - Determine `active_tab` (defaulting to 'general').
  - Fetch `case_template.cases_from_template.all()` and pass to context.
- **AC:** View provides all data required for the new tab without breaking existing functionality.

### Phase 2: Template Changes
- **Task:** Update `ui/templates/ui/case_template_detail.html`.
- **Description:**
  - Wrap existing cards in `<section class="tabpanel" ... id="panel-general">`.
  - Add `<section class="tabpanel" ... id="panel-cases">`.
  - Inside `panel-cases`, implement the `data-table-tight` table.
- **AC:**
  - Tabs are visible and switchable.
  - "Cases" badge shows a number > 0 when cases exist.
  - Clicking a title in the table navigates to the correct case detail page.

## Security
- Ensure that `cases_from_template` is filtered by the user's organization membership.

## Testing
- **Manual:** Verify that clicking the "Cases" tab displays the correct list of cases.
- **Manual:** Verify that clicking a case title leads to the correct detail page.
- **Visual:** Verify the badge count matches the table row count.

## Risks
- **Performance:** Large sets of cases might cause slow rendering. 
  - *Mitigation:* For now, we will allow the default ID-based limit, but we should monitor the number of rows displayed.
