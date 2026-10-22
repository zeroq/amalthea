# BRIEF — 2026-10-21 — SuperAdmin delete alerts and cases from lists

**For:** `django-backend` (P1), `django-frontend` (P2–P3)  
**Plan:** `docs/planning/PLAN-2026-10-21-superadmin-delete-alerts-cases.md` (APPROVED)

## Goal
Add trash icon to Actions column of alerts and cases lists, visible/usable only by superAdmin, with confirmation modal, using existing delete behavior.

## P1 Backend
1. `ui/views.py`: add `alert_delete_ui(request, alert_id)` and `case_delete_ui(request, case_id)` — POST only, require `request.user.role == SUPER_ADMIN_ROLE`, delete and redirect to list with success message. Use `get_object_or_404` or resolve_alert/resolve_case as appropriate.
2. `ui/urls.py`: add routes `alerts/<str:alert_id>/delete` name `ui-alert-delete`, `cases/<str:case_id>/delete` name `ui-case-delete`.

## P2 Alerts list
`ui/templates/ui/alert_list.html`: add Actions column header when `request.user.role == "superAdmin"` or `is_superadmin`; trash button `data-modal-open="delete-alert-{{ alert.id }}"` with icon `fa-trash`; include modal per alert with form_action to delete URL, confirm btn-err.

## P3 Cases list
`ui/templates/ui/case_list.html`: same for cases (both paginated and legacy paths).

## Constraints
- Only superAdmin
- Confirmation modal, no native confirm
- Redirect to list
- Follow existing modal patterns
