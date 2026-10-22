# Plan — SuperAdmin can delete individual alerts and cases (UI)

**Date:** 2026-10-21  
**Status:** APPROVED — ready for implementation  
**Owner:** Planner/Tech Lead  
**Scope:** Add trash icon to actions column of alerts and cases lists, visible/usable only by superAdmin. Use existing delete endpoints with confirmation modals.

## Existing delete capabilities
- **API:** `DELETE /api/v1/case/{idOrNumber}` in `cases/views.py` (permanent delete, CASCADE children, alerts survive via SET_NULL). `DELETE /api/v1/alert/{alertId}` in `alerts/views.py` (permanent delete, CASCADE AlertObservable).
- **UI patterns:** Trash icons use modals (`ui/_modal.html`) with `btn btn-err` confirm, forms POST to delete URLs.
- **Role:** `superAdmin` role value `"superAdmin"`, `SUPER_ADMIN_ROLE` in `core/enums.py`.

## Decisions
- Add trash icon to **Actions** column in both alerts and cases lists
- **Only superAdmin** can see and use
- Use confirmation modal (no native `confirm()`)
- Redirect back to the list after deletion
- Use existing API behavior where possible

## Implementation

### P1 – UI delete views + URLs
1. Add `alert_delete_ui` in `ui/views.py`:
   - `@not_viewer @login_required @require_POST`
   - Check `request.user.role == SUPER_ADMIN_ROLE` else 404
   - Resolve alert via `resolve_alert` or fetch, call `alert.delete()`, message success, redirect to `ui-alert-list`
2. Add `case_delete_ui` in `ui/views.py`:
   - superAdmin only, POST, delete case, redirect to `ui-case-list`
3. Add URLs in `ui/urls.py`: `alerts/<str:alert_id>/delete` → `ui-alert-delete`, `cases/<str:case_id>/delete` → `ui-case-delete`

### P2 – Alerts list UI
1. `ui/templates/ui/alert_list.html`:
   - Add "Actions" column header if superAdmin
   - Add trash button with `data-modal-open="delete-alert-{{ alert.id }}"` for superAdmin
   - Include modal per row using `ui/_modal.html` (title "Delete alert?", body warning permanent delete, confirm `btn-err`, form_action to delete URL)
   - Works in paginated path (uses `page_obj.object_list`)

### P3 – Cases list UI
1. `ui/templates/ui/case_list.html`:
   - Add "Actions" column header if superAdmin (both paginated and legacy paths)
   - Trash button + modal per row for superAdmin
   - Form action to case delete URL

### P4 – Verify
- Only superAdmin sees trash
- Non-superAdmin cannot POST (404)
- Modal confirmation works
- Deletion redirects correctly
- `make check` green

## Files to touch
- `ui/views.py` - add delete views
- `ui/urls.py` - add routes
- `ui/templates/ui/alert_list.html`
- `ui/templates/ui/case_list.html`
