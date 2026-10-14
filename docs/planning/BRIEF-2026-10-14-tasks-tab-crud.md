# IMPLEMENTATION BRIEF — Tasks tab CRUD + task detail & notes

Plan: `docs/planning/PLAN-2026-10-14-tasks-tab-crud.md` (§11 = locked decisions, §5.1 = `TaskNote`).
Status: approved 2026-10-14. **BINDING: tests are frozen** — do **not** run `pytest`, `ruff`, `mypy`, or
`manage.py check`; the user runs every gate. Do **not** commit. Deviations must be appended to the
plan (§13, add the section).

Target stack: Django 5.x, DRF, Channels, Postgres. Dark-only UI, Tailwind + HTMX, keyboard-driven.

---

## 0. Contract (both agents implement to this exactly)

**UI route names / paths** (all `ui/urls.py`, `ui-` prefix):

| name | path | verb | decorators |
| --- | --- | --- | --- |
| `ui-task-detail` | `tasks/<uuid:task_id>` | GET | `@login_required` (viewer allowed) |
| `ui-case-task-create` | `cases/<str:case_id>/task` | POST | `@not_viewer @login_required @require_POST` (**exists**) |
| `ui-case-task-edit` | `tasks/<uuid:task_id>/edit` | POST | `@not_viewer @login_required @require_POST` |
| `ui-case-task-delete` | `tasks/<uuid:task_id>/delete` | POST | `@not_viewer @login_required @require_POST` |
| `ui-case-task-toggle` | `cases/<str:case_id>/task/<uuid:task_id>/toggle` | POST | (**exists**, unchanged) |
| `ui-task-note` | `tasks/<uuid:task_id>/note` | POST | `@not_viewer @login_required @require_POST` |

**Context added to `case_detail`** (templates depend on these names):
- `task_assignees` — `User.objects.filter(org_q("org", request.user), is_active=True).order_by("login")` (**uncapped**; the existing `assignees` stays as-is for other templates).
- `task_status_options` — `TASK_STATUS_CHOICES` from `core.enums`.

**Template names:**
- `ui/_tab_tasks.html` (rewrite), `ui/_task_form_modal_body.html` (new, shared create/edit),
  `ui/_task_delete_modal.html` (new), `ui/task_detail.html` (new).

**Modal-body contract (non-negotiable):** every control inside a `_modal.html` body carries
`form="{{ form_id }}"`, because the `<form>` lives in the modal **footer**. Reference:
`ui/templates/ui/_page_form.html`. Getting this wrong silently submits nothing (the D1 bug).

**WS frame:** `publish_case_event(str(case.id), "task", {"event": task_json(task)})` on create/edit/delete,
scheduled via `transaction.on_commit(partial(...))` (copy `case_page_create`, `ui/views.py:977`).
`live.js` routes `event_type === "task"` to `refreshTasks()`.

**Ledger mirror (every note):** `append_timeline_event(case, title="Task note", description=body[:10000], kind="task-note", actor=request.user, metadata={"task_id": str(task.id), "note_id": str(note.id)})` in the same transaction as the `TaskNote` insert.

---

## 1. Backend tasks — `django-backend`

### T1.1 `TaskNote` model — `cases/models.py` (+ migration)
Add exactly as plan §5.1 (own `UUIDModel, TimeStampedModel`; `task` FK CASCADE `related_name="notes"`
`db_index=False`; `message` TextField; `created_by` FK `SET_NULL` `related_name="task_notes"`;
`db_table="task_note"`; index `task_note_task_created_idx` on `["task","-created_at","-id"]`).
Migration: next number is `0016_*` (latest is `0015_tenant_owner_org.py`). `makemigrations` is the only
Django command permitted (it is code-gen, not a gate); otherwise hand-write it in the same style.

### T1.2 Extend `case_task_create` (`ui/views.py:930`) — keep the existing test contract
- Keep: default `status="Waiting"`, assignee resolved by **login** (org-scoped), unknown login → unassigned.
- Add: `description` (strip), `due_date` (parse → end-of-day aware, see T1.5), `flag` (`"on"`/truthy),
  and **validate `status` against `TASK_STATUS_CHOICES`** (D-1; reject → inline error, never a 500).
- Publish the `task` frame on success.
- **Error path:** if `HX-Request` → re-render `ui/_task_form_modal_body.html` with `errors`
  (dict field→message) and the submitted values, HTTP 200 (HTMX swaps it into the modal). Else flash +
  `_case_detail_redirect(request, case.number)`.

### T1.3 New views (`ui/views.py`)
- `case_task_edit(request, task_id)` — resolve the task and its case **scoped to the user's org**
  (add/reuse a `resolve_task` in `identity/scoping.py`: `Task` joined through `case` using
  `case_visible_q`; mirror `resolve_observable`). Same field/validation/error logic as T1.2; publish frame.
- `case_task_delete(request, task_id)` — delete (scoped), publish frame, redirect to the case (resolved
  **before** deletion, since `task.case` is gone afterwards).
- `task_detail(request, task_id)` — GET, viewer-readable; renders `ui/task_detail.html` with `task`,
  `case`, `notes` (`task.notes.select_related("created_by").order_by("created_at")`), `can_mutate`,
  `task_status_options`.
- `task_note(request, task_id)` — validate non-empty `body`; one transaction: create `TaskNote`
  (+ `created_by=request.user`) and `append_timeline_event(...)` as above; then, mirroring
  `case_comment` (`ui/views.py:864`): `HX-Request` → render the note partial (200), else
  `redirect("ui-task-detail", task_id=task.id)` + flash.

### T1.4 `ui/urls.py`
Register the five routes above (four new; the two existing stay). Keep the `ui-` prefix.

### T1.5 Due-date helper
Parse the `<input type="date">` `YYYY-MM-DD` value; build an **aware** datetime at **23:59:59** in
`settings.TIME_ZONE` (via `zone.make_aware`), store it (Postgres stores UTC). Empty → `None`.
Bad input → inline field error. Same rule on create and edit. *(Rationale: a task due today must not be
"Overdue" at 00:00.)*

### T1.6 Context + frame wiring
- `case_detail` context: add `task_assignees`, `task_status_options` (§0).
- `live.js` is the **frontend** agent's file — backend only publishes the frame.

**Backend AC:** viewer is rejected on all four mutating routes; out-of-vocabulary `status` → inline
error (no 500); create/edit/delete publish a `task` frame; a note writes one `TaskNote` **and** one
`TimelineEvent(kind="task-note")`; `test_ui_loop.py` (`Waiting` default, unknown assignee, toggle) and
`test_viewer_affordances.py` remain satisfiable.

---

## 2. Frontend tasks — `django-frontend`

### T2.1 Rewrite `ui/_tab_tasks.html`
- List `id="case-tasks"` (Live swap target). Row: quick tick (existing `ui-case-task-toggle`, unchanged) ·
  **title as `<a href="{% url 'ui-task-detail' task.id %}">`** · status pill
  (`pill-{{ task.status|lower }}` — reuse existing pill classes) · assignee login · due date.
- Overdue cue: if `task.due_date and task.due_date < now`** → semantic danger text **plus the word
  "Overdue"** (never colour alone). Use the existing due/overdue markup style if one exists; else text.
- Per-row **Edit** + **Remove** buttons (`data-modal-open`) → the modals; all under `{% if can_mutate %}`.
- "New task" button (writers) opens the create modal (replaces the old inline form). Its footer form
  action **must** be `{% url 'ui-case-task-create' case.number %}` — `test_viewer_affordances.py`
  requires that URL to be present for analysts and absent for viewers.
- Keep `_tab_tasks.html` rendering **all** tabs in the DOM (the panel is `hidden`, never conditionally
  included) — the conformance tests scan the full page HTML.

### T2.2 `ui/_task_form_modal_body.html` (new)
One template for create + edit. Fields (each `form="{{ form_id }}"`, hidden `tab=tasks`):
`title` (required, maxlength 500, `[autofocus]`), `description` (textarea rows≈4), `assignee`
(person picker, §T2.4; `<option value="{{ person.login }}">`), `due_date`
(`<input type="date" name="due_date" value="{{ due_date_value }}">`), `status`
(`<select>` over `task_status_options`), `flag` (checkbox, label "Important").
Prefill from `task` on the edit path (same dual-path trick as `_page_form.html`); render `errors`
inline next to their fields.

### T2.3 `ui/_task_delete_modal.html` (new)
Confirmation naming the task; footer form action `{% url 'ui-case-task-delete' task.id %}`, `btn-err`.

### T2.4 Person picker — `ui/static/ui/picker.js` (new) + `ui/static/ui/main.css`
Progressive-enhancement type-ahead over the assignee `<select>`: adds a text `<input>` that hides
non-matching `<option>`s (case-insensitive substring on the option label), keeps the selected value
submittable, and does nothing when absent. No inline `<script>` (CSP `script-src 'self'`); load it the
same way `modal.js` is loaded. Keyboard-usable; the `<select>` works untouched without JS.

### T2.5 `ui/_modal.html` (extend, default-off)
Add optional params `form_hx_post` and `form_hx_target`. When `form_hx_post` is set, the footer form
gains `hx-post="{{ form_hx_post }}" hx-target="{{ form_hx_target }}" hx-swap="innerHTML"`. Also give
`.modal-body` `id="{{ modal_id }}-body"` so the target is addressable. **Existing modals must render
byte-identically** (both params absent → no change except the new body id).

### T2.6 `ui/task_detail.html` (new)
`{% extends "ui/base.html" %}` (see `observable_detail.html`). `{% block actions %}`: Back (to the case,
`?tab=tasks`), Edit (opens the shared modal, prefilled), Delete (modal), toggle — all `can_mutate`-gated.
`{% block content %}`: heading = task title + status pill; meta = assignee, due date, created, flag,
case link; description via `|markdown`; **Notes** `ol.timeline` over `notes` (reuse style of
`_tab_comments.html`; each note shows message + author + timestamp — do **not** reuse
`_timeline_entry.html` unless it renders `TaskNote`, which it does not) + an add-note `<form
method="post" action="{% url 'ui-task-note' task.id %}">` with `{% csrf_token %}` (viewer: list only).

### T2.7 `ui/static/ui/live.js` — `refreshTasks()`
Mirror `refreshPages()` (single-in-flight guard, fetch-with-`X-Requested-With`, detached parse, swap
`#case-tasks`). Route `message.event_type === "task"` to it in `handle()`.

**Frontend AC:** analyst sees title links, status pills, overdue text cue, Edit/Remove/New-task; viewer
sees none of the mutation controls; the create modal posts to `ui-case-task-create`; every modal body
control is bound via `form="{{ form_id }}"`; a bad title/date re-renders with an inline error and the
modal stays open; `refreshTasks()` is a no-op when `#case-tasks` is absent; the assignee type-ahead
filters and still submits the login.

---

## 3. Known test contracts (do not break; do not edit)
- `tests/conformance/test_ui_loop.py::test_a_task_can_be_added_and_completed_from_the_page` — POST
  `{title, assignee:"analyst"}` → `status=="Waiting"`, `assignee.login=="analyst"`; toggle Waiting↔Completed.
- `…::test_a_task_with_an_unknown_assignee_is_left_unassigned` — unknown login → `assignee is None`.
- `tests/conformance/test_viewer_affordances.py::test_case_detail_offers_a_viewer_no_mutation_controls` —
  `ui-case-task-create` **present for analyst**, **absent for viewer**, in the case-detail HTML.
- `tests/conformance/test_viewer_readonly_ui.py` — expects `ui-case-task-create` under `not_viewer`;
  new mutating routes must carry it too.
- `tests/conformance/test_t1_surface.py` — API task shape/vocabulary unchanged (no REST change here).

## 4. Order & ownership
Backend (T1) first, then frontend (T2) against the landed views/context. Disjoint file sets:
backend = `cases/models.py`, `cases/migrations/`, `identity/scoping.py`, `ui/views.py`, `ui/urls.py`;
frontend = `ui/templates/ui/*`, `ui/static/ui/*`. Neither runs gates; both record deviations in
`PLAN-2026-10-14-tasks-tab-crud.md` §13.
