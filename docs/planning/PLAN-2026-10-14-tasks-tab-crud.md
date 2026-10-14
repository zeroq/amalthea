# PLAN 2026-10-14 — Tasks tab: create / edit / remove tasks + task detail & notes

Status: **APPROVED (2026-10-14) — all decisions locked (Q2 = B2, new `TaskNote` model).** Decisions in
§11. No code written yet. Tests remain **frozen** (BINDING): the user runs every gate; nothing here is
committed.
Owner: planner → `django-frontend` (+ `django-backend` for the views/urls)
Related: `PLAN-2026-10-14-case-pages-ui.md` and `PLAN-2026-10-14-observables-tab-crud.md` (the
tab-CRUD idiom this mirrors), `PLAN-2026-10-14-ttp-tab.md` (modal-body `form=` contract),
AGENTS.md §2 Module B (live ledger: "every comment, **task assignment**, and state transition must
emit an event via WebSockets"), §3 (Task entity), ADR-002 §D11 (unrecognised assignee → unassigned),
ADR-006 / `PLAN-2026-10-12-viewer-role-enforcement.md` (viewer read-only).

---

## 1. Summary

The case **Tasks** tab (`ui/templates/ui/_tab_tasks.html`) can only do two things today:

- **list** tasks as `<li>` rows (title, a `Waiting`/`Completed` tick, raw status text, assignee login);
- **create** a task inline with **title + assignee only** (`POST cases/<id>/task` → `case_task_create`),
  and flip it Waiting↔Completed via the tick (`ui-case-task-toggle`).

There is **no** description, **no** due date, no status control beyond Waiting/Completed, no edit, no
remove, and **no task detail surface** — a task title is plain text, not a link.

The `Task` model already carries every field we need (`title`, `description`, `status`, `assignee`,
`due_date`, `group`, `flag`, `order`, `started_at`, `ended_at`, `mandatory`) and the REST API already
renders all of them (`core/serializers.py::task_json`). This plan adds the missing **UI**:
a create/edit **modal** on the tab, per-row **remove**, and a **task detail page** with a Markdown
**notes log**.

## 2. Goals

- **G1** Create a task from the tab via a modal: **title, description, assignee (person picker),
  due date, status**.
- **G2** Edit any task from the tab via the same modal body (prefilled).
- **G3** Remove a task from the tab via a confirmation modal (no native `confirm()`).
- **G4** Clicking a task **title** opens a **task detail page**: all fields + a **notes** log with an
  add-note composer.
- **G5** The tab keeps its list + a clear **status** signal (status pill + overdue cue), and keeps the
  quick complete/reopen toggle.
- **G6** Contracts unchanged: `@not_viewer` on every mutating route, `resolve_case(..., write=True)`,
  CSRF via the modal footer form, `?tab=tasks` preserved, viewer read-only.
- **G7** Fix the one latent correctness bug in the current create view: `status` is taken straight
  from the POST body with **no** validation, so any value outside `TASK_STATUS_CHOICES` invites a DB
  `CHECK` `IntegrityError` (500). Validate it (see §13 D-1).

## 3. Non-goals

- **N1** No change to the `Task` model and **no migration** *unless* Q2 lands on a dedicated notes
  model (see §12/§11).
- **N2** No new **REST** endpoint (the JSON `/api/v1/task/*` surface is unchanged). Only UI views.
- **N3** No `group` / `order` / `mandatory` / `started_at` / `ended_at` editing in the UI (API-only,
  as `order` is for Pages — it is not rendered, so it is not exposed).
- **N4** No new tests (BINDING freeze; the user owns test edits). See §10 for the intended coverage.
- **N5** No kanban/board view, no drag-reorder, no per-task attachment upload.
- **N6** No bulk task operations.

## 4. Architecture / approach

| Concern | Decision | Rationale |
| --- | --- | --- |
| Transport | plain `POST` → `_case_detail_redirect(request, case.number)` | matches every write on this page; degrades to a normal form |
| Create UI | "New task" button → shared `_modal.html` + new `_task_form_modal_body.html` | the documented create idiom; `modal.js` gives Esc / backdrop / focus-trap / `[autofocus]` |
| Edit UI | per-row **Edit** → same body template, prefilled (task-scoped URL) | one shared form body, mirrors `_page_form.html` / `_observable_form_modal_body.html` |
| Remove UI | per-row confirmation modal | AGENTS.md: never native `confirm()` |
| Detail UI | new page `/tasks/<uuid>` (`ui-task-detail`), mirrors `observable_detail` | deep-linkable, keyboard-reachable; the tab title becomes an `<a>` |
| Notes store | new `TaskNote` model (§5.1) + a mirrored `TimelineEvent(kind="task-note")` in one transaction | user decision (Q2=B2); addressable task log **and** a case-audit event (Module B) |
| Notes render | Markdown via `|markdown` (the `kind == "comment"` branch of `_timeline_entry.html`) | AGENTS.md Module B: notes are Markdown |
| Assignee write | reuse the existing org-scoped login resolution (`User.objects.filter(org_q(...), login=...)`) | ADR D11: unknown login → unassigned, never auto-create |
| Live list | publish a `task` frame on create/edit/delete; `live.js` `refreshTasks()` mirrors `refreshPages()` | parity with the Pages card (Q5) |
| Validation | `status` checked against `TASK_STATUS_CHOICES`; `due_date` parsed defensively | prevents the DB-constraint 500 (§13 D-1) |

**Reuse, not duplication:** the create/edit body templates must wire every control with
`form="{{ form_id }}"` (the modal `<form>` lives in the **footer**, outside `_modal.html`'s body —
the D1 bug from `_procedure_form_modal_body.html`). Reference implementation:
`ui/templates/ui/_page_form.html`.

## 5. DB schema

**No change to `Task`.** **One additive migration** for the new `TaskNote` model (§12 Q2 = **B2**).
`Task.due_date` is reused as-is. The existing `Task` columns are sufficient:

| Field | Type | UI-exposed? |
| --- | --- | --- |
| `title` | CharField(500) | ✅ required |
| `description` | TextField(blank) | ✅ (Markdown) |
| `status` | CharField(20) ∈ `TASK_STATUS_CHOICES` | ✅ select |
| `assignee` | FK `User` (SET_NULL, null) | ✅ person picker |
| `due_date` | DateTimeField(null) | ✅ date input |
| `flag` | BooleanField(default False) | ⚪ optional (Q7) |
| `group`,`order`,`started_at`,`ended_at`,`mandatory` | — | ❌ API-only (N3) |

If **Q2 = dedicated model**, add `TaskComment(UUIDModel, TimeStampedModel)` with `task` FK,
`message` TextField, `created_by` FK — one migration. (Only if the user wants notes editable, since
the ledger is append-only.)

### 5.1 `TaskNote` (Q2 = B2 — locked)

New model in `cases/models.py`, additive migration under `cases/migrations/`:

```python
class TaskNote(UUIDModel, TimeStampedModel):
    """A log entry on a Task (TheHive "task log" shape, plan §6). Its own row rather than a
    `TimelineEvent` projection so a task's notes are addressable independently of the case ledger."""

    # db_index=False: `task` is the left prefix of `task_note_task_created_idx`.
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="notes", db_index=False)
    message = models.TextField()
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="task_notes"
    )

    class Meta:
        db_table = "task_note"
        indexes = [
            models.Index(fields=["task", "-created_at", "-id"], name="task_note_task_created_idx"),
        ]
```

Notes are **append-only** (no `updated_by`, no edit endpoint) — a task log, not a document.
**Each note is also mirrored into the case ledger** (`TimelineEvent(kind="task-note",
metadata={"task_id": …, "note_id": …})`) in the same transaction, so Module B's "every note emits an
event via WebSockets" holds and the note appears in the case **Audit** timeline.

## 6. API

**No REST change.** The UI mirrors the API's field vocabulary so the two cannot drift:

- status vocabulary → `core/enums.py::TASK_STATUS_CHOICES` = `Waiting | InProgress | Completed | Cancel`
- due date → the API reads epoch ms (`parse_timestamp`); the UI posts an HTML date and stores a
  `datetime` (see Q6 for the exact instant)
- assignee → login string; unknown → unassigned

New **UI** routes (`ui/urls.py`), all `@not_viewer @login_required @require_POST` unless noted:

| Name | Path | Verb | Purpose |
| --- | --- | --- | --- |
| `ui-task-detail` | `tasks/<uuid:task_id>` | GET | task detail page (login required, viewer allowed) |
| `ui-case-task-create` | `cases/<str:case_id>/task` | POST | **existing** — extend with description / assignee / due date / status |
| `ui-case-task-edit` | `tasks/<uuid:task_id>/edit` | POST | update fields |
| `ui-case-task-delete` | `tasks/<uuid:task_id>/delete` | POST | delete (confirm modal) |
| `ui-case-task-toggle` | `cases/<str:case_id>/task/<uuid:task_id>/toggle` | POST | **existing** — keep (quick complete/reopen) |
| `ui-task-note` | `tasks/<uuid:task_id>/note` | POST | append a note |

`resolve_case(..., write=True)` for the case-scoped routes; task-scoped routes resolve the task and
re-derive the case (any case **writer** may act — parity with Pages/tasks; Q4).

## 7. UI/UX

### 7.1 `_tab_tasks.html` (rewrite)
- Row: `[tick] **title (link)** · status pill · assignee · due date · [Edit] [Remove]`.
- Overdue due date: semantic **danger text + the word "Overdue"**, never colour alone (AGENTS.md §6).
- "New task" button opens the create modal (replaces today's inline title+assignee form).
- Viewer (`not can_mutate`): no tick, no buttons, no create button — list only.
- `id="case-tasks"` on the list wrapper so `refreshTasks()` has a swap target (Q5).

### 7.2 `_task_form_modal_body.html` (new, shared create/edit)
Fields: **title** (`required`, maxlength 500), **description** (textarea, rows≈4), **assignee**
(person picker), **due date** (`type="date"`), **status** (`<select>` of `TASK_STATUS_CHOICES`).
Every control carries `form="{{ form_id }}"` + a hidden `tab=tasks`. Edit mode prefills and shows the
task title in the modal heading.

### 7.3 `_task_delete_modal.html` (new)
Confirmation with the task title; `<form method="post" action="{% url 'ui-case-task-delete' task.id %}">`.

### 7.4 `ui/task_detail.html` (new)
- Heading: task title + status pill; meta: assignee, due date, created, flag, case link.
- Description rendered as Markdown.
- **Notes** section: `ol.timeline` of the task's `task-note` events (`_timeline_entry.html`) +
  add-note `<textarea>` posting to `ui-task-note`. Same HTMX-append-or-POST-fallback shape as
  `_tab_comments.html`.
- Actions: Edit (modal), Remove (modal), quick complete/reopen.
- Viewer: read-only (notes list, no composer).

### 7.5 Person picker (decided: native select + type-ahead)
A native `<select name="assignee">` populated from `task_assignees` (org-scoped, active, uncapped),
**enhanced** by a new reusable `ui/static/ui/picker.js`: a small text input that filters the
`<option>`s (hides non-matches, keeps the selected value). Accessible and keyboard-driven; no
framework. The option **value is the login** (what `case_task_create` and `_resolve_login` expect);
an empty option is "Unassigned". Without JS the select still works (progressive enhancement).

## 8. Phases + Acceptance Criteria

**P1 — Backend views + routes (`django-backend`)**
- T1.1 Extend `case_task_create` (description, assignee, `due_date`, validated `status` — D-1).
- T1.2 Add `case_task_edit`, `case_task_delete`, `ui-task-detail`, `task_note` (`ui/views.py`).
- T1.3 Add the routes (`ui/urls.py`); register the task-scoped paths.
- T1.4 Publish `task` frames from create/edit/delete; add `refreshTasks()` to `live.js`.
- T1.5 Add the `TaskNote` model (§5.1) + its additive migration; the note write path mirrors into the
  ledger in one transaction.
- **AC:** a viewer gets `403`/redirect on every mutating route; a writer can create→edit→delete a
  task; an out-of-vocabulary `status` re-renders the modal with an inline error (no 500); a note is
  written per §11 Q2 and mirrored into the ledger as `kind="task-note"` in one transaction;
  create/edit/delete publish a `task` frame the tab consumes.

**P2 — Tab UI (`django-frontend`)**
- T2.1 Rewrite `_tab_tasks.html` (title link, pills, due/overdue, Edit/Remove, New-task button).
- T2.2 `_task_form_modal_body.html` + `_task_delete_modal.html`.
- T2.3 Extend `ui/_modal.html` with optional `form_hx_post` / `form_hx_target` (default off) for the
  inline-error path (D-8); wire create/edit to it and render errors in the body.
- T2.4 New `ui/static/ui/picker.js` (assignee type-ahead) + styling.
- T2.5 Add `task_assignees` (org-scoped, active, **uncapped**) to the `case_detail` context.
- **AC:** every primary action reachable by keyboard; modal controls all submit (the `form=` contract);
  a bad title/date keeps the modal open with the error shown; empty state preserved; no colour-only
  signalling; the type-ahead filters the select and still submits the login value.

**P3 — Task detail + notes (`django-frontend` + `django-backend`)**
- T3.1 `ui/task_detail.html`: all fields, Markdown description, notes log + composer.
- **AC:** clicking a title navigates to `/tasks/<id>`; a note appears in the task's notes **and** in
  the case Audit timeline; viewer sees the log but no composer.

**P4-optional — live detail:** subscribe the detail page to `/ws/case/<case_id>/` and append
`task-note` frames whose `metadata.task_id` matches (deferred unless asked — §11 Q7).

## 9. Security

- **AuthZ:** every mutating route `@not_viewer @login_required @require_POST`; `resolve_case(write=True)`
  / task→case re-derivation. No new permission model (N5).
- **Tenant isolation:** assignee resolution stays org-scoped (`org_q("org", request.user)`), so a
  crafted login cannot bind a task to another org's user — same rule as `_resolve_login`.
- **Injection:** status validated against the enum; due date parsed with `parse_timestamp`/`parse_date`
  and rejected on garbage; Markdown rendered by the existing sanitised `markdown` filter.
- **CSRF:** modal footer forms include `{% csrf_token %}`; task-scoped URLs take a UUID (no traversal).
- **No secrets:** nothing new touches config.

## 9.1 Testing (intended — the user runs it; BINDING freeze)
- Model/view: create→edit→delete round-trip; invalid status → error, no row; viewer → denied on all
  four mutating routes (the conformance test `test_viewer_readonly_ui.py` should pick up the new
  routes automatically).
- Notes: a note creates one `TimelineEvent(kind="task-note")` and is visible on the detail page.
- Fallback: watcher-free `modal.js` absence still yields a working `POST`.
- Regression: `test_case_detail_procedure_form_is_real_form_with_csrf` is unaffected (different form).

## 10. Risks

- **R1 — modal `form=` wiring.** The D1 class of bug: a control inside the modal body that is not
  bound to the footer form silently submits nothing. Mitigation: copy `_page_form.html` exactly.
- **R2 — notes visibility.** A `task-note` will appear in the case **Audit** timeline (it is a real
  ledger event). That is intended (Module B) but worth confirming (Q2) — the Comments tab filters
  `kind="comment"`, so it will *not* show there.
- **R3 — due-date timezone.** `Task.due_date` is a naive-UTC `DateTimeField`; an HTML `date` must be
  converted deliberately (Q6). Getting this wrong shifts "today" by a day at the boundary.
- **R4 — assignee list truncation.** `assignees` in `case_detail` is capped at `PAGE_SIZE` (200);
  a person picker should use a dedicated, uncapped org-scoped query (`task_assignees`).
- **R5 — `refreshTasks()` fetch amplification.** Mirror `refreshPages()`'s single-in-flight guard.

## 11. Decisions (locked 2026-10-14)

Answers to the §12 questions, as given by the user:

- **Q1 — detail surface:** **dedicated page** `/tasks/<uuid>` (`ui-task-detail`), mirrors
  `observable_detail`. The tab title becomes an `<a>`.
- **Q2 — note storage:** **B2 — a new `TaskNote` model** (task FK, `message`, `created_by`), one
  additive migration; each note is also mirrored into the case ledger (§5.1).
- **Q3 — person picker:** native `<select>` + a **type-ahead filter** → new reusable
  `ui/static/ui/picker.js`, applied to the assignee control.
- **Q4 — assignee list:** **uncapped, org-scoped, active** users — a dedicated `task_assignees`
  context (the shared `assignees` list is capped at `PAGE_SIZE = 200`).
- **Q5 — due date:** `<input type="date">`, **day granularity**. Stored at **end-of-day**
  (`23:59:59` in `settings.TIME_ZONE`, saved aware → UTC) so a task due *today* is not "overdue"
  at 00:00 (decided here to satisfy R3; correct me if you want local midnight).
- **Q6 — status:** **keep** the Waiting↔Completed quick toggle **and** add the full
  `TASK_STATUS_CHOICES` select to the create/edit modal.
- **Q7 — live frames:** publish a **`task` frame** on create/edit/delete; add `refreshTasks()` to
  `live.js` (mirrors `refreshPages()`, incl. the single-in-flight guard). Task-detail notes stay
  server-rendered (no live append) unless asked.
- **Q8 — permission:** **any case writer** may create/edit/delete (parity with Pages); viewers get
  `not_viewer` rejection on all mutating routes.
- **Q9 — flag:** **expose `Task.flag`** as "Important"; keep `group`, `order`, `mandatory`,
  `started_at`, `ended_at` API-only.
- **Q10 — invalid input:** **return the modal with inline errors** (not redirect-with-flash). Requires
  extending `_modal.html` with an optional HTMX error path (§7.6) — plan **D-8**.
- **Q11 — D-1 fix:** *pending the user's check* — evidence in §11.1; fix cost is one guard, so it stays
  in the plan (P1 T1.1) unless the user vetoes it.

### 11.1 Evidence for D-1 (the unvalidated `status`)
- `ui/views.py:918` — `status=request.POST.get("status") or "Waiting"`, written straight into the row.
- `cases/models.py:202` — `in_values("task_status_valid", "status", …)` → a `CheckConstraint` on
  Postgres.
- Consequence: a writer who POSTs `status=Evil` (with a valid CSRF token) makes `Task.objects.create`
  violate the CHECK → `IntegrityError` → **500**. Not reachable through today's UI (no status field
  is rendered), but the modal in this plan *will* render one, so the fix belongs in this pass.
- Fix: validate against `TASK_STATUS_CHOICES` in create **and** edit; reject with an inline error.

### 11.1b Supporting decisions

- **D-2:** `ui-case-task-toggle` stays as a quick complete/reopen.
- **D-3:** no migration **if** Q2 lands on the ledger; otherwise a single additive migration.
- **D-4:** per-row actions + create button hidden for viewers (`can_mutate`), matching other tabs.
- **D-5:** due date rendered with a text cue ("Overdue"), never colour alone.
- **D-6:** due date stored at end-of-day (§11 Q5).
- **D-7:** `flag` exposed as "Important".
- **D-8 (`_modal.html`):** add optional `form_hx_post` / `form_hx_target` params (default off, so
  existing modals are byte-identical) to support the inline-error path (§8 P2 T2.3).
- **D-1:** validate `status` (see above).

## 12. Open Questions

None — all 11 answered (see §11). **Q2 resolved to B2** (§5.1): a new `TaskNote` model, one additive
migration, each note mirrored into the case ledger as `kind="task-note"`.

Residual choices made on the user's behalf (correct if wrong):
- due date stored at **end-of-day** local (§11 Q5);
- task-detail notes are **server-rendered** (no live append) for now (§11 Q7, P4-optional);
- the create/edit error path uses the new **`_modal.html` HTMX hook** (D-8), degrading to
  POST→redirect+flash when HTMX is absent.

## 13. Planner review of the implementation (2026-10-14)

Review of the `django-backend` + `django-frontend` deliverables against §8 ACs. **No gates were run**
(binding freeze). The only execution was `python3 -m py_compile` on the touched modules, throwaway
Django-template snippets (no project settings, no DB) to settle R-3, and a standalone render of
`_tab_tasks.html` (stub URLconf + fake task) to diagnose R-9. All fixes below are in the working tree.

**Standing rule (R-9): a template tag must not span a newline.** Django's lexer does not match tags
across lines (`tag_re` has no `re.DOTALL`), so a wrapped `{% include … %}` (or `{% if … %}`, `{{ … }}`,
`{# … #}`) silently degrades to literal page text. Keep each tag on one line; use `{% comment %}`
(newline-safe, since only its delimiters are tags) for anything longer. Verify with the lexer scan in
R-9, not a compiled-node walk.

### Bugs found and fixed

- **R-1 — inline-error path orphaned its controls (fixed).** `_render_task_form_error`
  (`ui/views.py`) rendered `ui/_task_form_modal_body.html` **without** `form_id`/`modal_id`. Every
  control in that body references the modal footer form via `form="{{ form_id }}"` (§0 contract), so
  on an HTMX error the re-rendered inputs/selects had `form=""` and were **not associated with the
  submit form** — a second submit would post nothing (empty title → error again). Labels also
  collapsed to `id="-title"`. Fix: derive the ids deterministically and never from the request —
  create → `task-create-modal`/`task-create-form`; edit → `task-edit-<task.id>` /
  `task-edit-form-<task.id>` (matches `_tab_tasks.html` and `task_detail.html`).

- **R-2 (bug, fixed):** `_render_task_form_error` and the `task_detail` view both omitted
  `task_assignees`, so the assignee `<select>` rendered only "Unassigned" — on the error path the
  analyst's choice vanished, and on the detail page an Edit would **clear the assignee**. Fix: pass
  the same uncapped, org-scoped active-user list as `case_detail`.

- **R-3 (bug, fixed):** `task_detail.html` declared `{% now "U" as now_u %}` at the **top level of
  a template that `{% extends %}`**. Verified empirically that the variable is then *not* in scope
  inside blocks (the probe rendered `C:`, i.e. empty) — so the detail page's "Overdue" cue silently
  never fired. Fix: move `{% now "U" as now_u %}` inside `{% block content %}`. (The `_tab_tasks.html`
  include was always correct; a `{% now %}` at the top level of an included template does render.)

- **R-4 (cosmetic, fixed):** `_tab_tasks.html` used `.card-header` and `.btn-ghost`, neither of
  which existed in `app.css`. Added both, and had `.card-header > h2` re-declare the `.card>h2`
  heading treatment (the `h2` is no longer a direct child of `.card`).

- **R-5 (cosmetic, fixed):** `.field-error` (only the new task modal emits it) had no rule. Added
  it, scoped via `.field-row:has(.field-error)` so the message wraps to its own line without
  disturbing the shared `.field-row` used by the page/observable/procedure forms. Also added a global
  `.required` colour (previously styled only inside `.form-grid`).

- **R-6 (deviation, recorded):** `task_detail.html`'s `{% block extra_scripts %}` is **dead** —
  `base.html` defines no such block. Harmless because `picker.js` is loaded globally from `base.html`
  (line 183); the dead block was removed rather than left to mislead.

- **R-7 (pre-existing, noted):** `app.css` already defined `.tasks/.task/.tick/.task-title/.task-done`
  in the component layer; the new task rules are appended unlayered, so they win. Benign, but the
  duplication should be collapsed in a later cleanup.

- **R-8 (verified, no change needed):** `date:"U"` is supported (Django `django.utils.dateformat`
  has `def U`), so `{% now "U" %}` and `{{ task.due_date|date:"U" }}` are both decimal strings of the
  same width — the string comparison is correct. Left as-is (the fragility noted in the brief was a
  false alarm).

- **R-9 — multi-line `{% include %}` tags never parsed → modal missing + literal `{%` on the page
  (fixed; the real cause of the reported symptoms).** Django's template lexer uses
  `tag_re = r"({%.*?%}|{{.*?}}|{#.*?#})"` **without `re.DOTALL`**, so `.` does not match `\n`: a tag
  whose `{%` and `%}` are on different lines is **not tokenised as a block tag at all** — it is
  emitted as literal `TextNode` text. Three include calls had been wrapped across lines:
  `_tab_tasks.html` (create modal, and the per-row edit + delete modals) and `task_detail.html` (edit
  modal). Effect: the `<dialog id="task-create-modal">` was never rendered while the `New task`
  button (a correctly-parsed single-line `{% if %}` branch) still emitted
  `data-modal-open="task-create-modal"` → clicking it found no dialog, so the button "did nothing";
  and the raw `{% include "ui/_modal.html" with … %}` text was printed into the page (the `{%` the
  user saw — it is *not* a comment bug; the leaked text just contains `{%`). Fix: put each
  `{% include %}` back on a single line. Verified by rendering the tab standalone: `literal '{%':
  False`, `dialogs == openers == [task-create-modal, task-edit-<id>, task-del-<id>]`, and no
  `form=""` controls (the `form="{{ form_id }}"` binding survives the `{% with %}` → `{% include %}`
  without `only`).

  **Detector note (corrects an earlier method).** The correct check walks the *lexer*, not the
  compiled node tree: for each template file, tokenise and flag any `TokenType.TEXT` token whose
  `contents` matches `\{[#%]` — a leaked tag is by definition TEXT. The earlier compiled-node walk
  reported "0 leaks" and was **wrong** (it only followed `node.nodelist`/`nodelist` attributes it
  knew about and missed the leaked region), so R-9 went undiagnosed until the standalone render. The
  lexer scan now reports only `_modal.html:13`, which is inside its own `{% comment %}` docblock
  (documentation of the call syntax, inert — a `{% comment %}` body is plain text). CI should run
  the lexer scan; `{% comment %}` bodies are exempt by construction.

Still open / carried, not fixed here: `app.css` `.btn-form` remains undefined (used only on the
toggle form in `task_detail.html`; the contained `.btn` keeps the same `margin-top` as its
page-action siblings, so it is visually inert).

Verification is **not** claimed: the test suite was not run (freeze). R-1/R-2/R-3 are the items the
frozen regression contracts and the MVP smoke loop should exercise first on unfreeze.

