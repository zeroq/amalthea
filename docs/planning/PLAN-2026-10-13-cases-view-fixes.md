# PLAN — Cases view: trailing-slash 404 + New-case modal

- **Date:** 2026-10-13
- **Status:** **Implemented, verified (16/16 ACs met) and security-reviewed** (0 Critical / 0 High /
  0 Medium; 2 Low, 3 Info — LOW-01 recorded as not-exploitable, LOW-02 accepted as W103-3). Gate:
  `make check` 1689 passed / 3 skipped.
- **Owner:** planner
- **Deliverables:** `docs/spec/deviations.md` "Wave 10.3" (W103-1…W103-3),
  `docs/verification/VERIFY-2026-10-13-cases-view-fixes.md`,
  `docs/reviews/REVIEW-2026-10-13-cases-view-fixes.md`
- **Related:** `AGENTS.md` §2 Module B, §6; `ADR-006` (viewer role); `docs/planning/PLAN-2026-10-13-ui-case-create.md`
  (the wave this amends); `docs/spec/deviations.md` (Wave 10.2, W102-*); `TODO.md` §10.2

---

## 0. Root cause (verified, not inferred)

Reported: `GET http://127.0.0.1:8000/cases/` → **404** as an `admin` user.

The cases **view** is fine. The **URL** is the problem:

```
$ resolve("/cases")    -> ui-case-list      (200)
$ resolve("/cases/")   -> Resolver404       (404)   ← the reported URL
$ resolve("/cases/new")  -> ui-case-create  (200)
$ resolve("/cases/new/") -> Resolver404     (404)
```

Every UI route in `ui/urls.py` is registered **slash-less** (`path("cases", …)`), and Django's
`APPEND_SLASH` only ever *appends* a slash (`/cases` → `/cases/` when the former 404s) — it never
**strips** one, so a slash-terminated URL like `/cases/` falls straight through to the 404 handler.

Confirmed by a role/org matrix — `/cases` is 200 for every role (`analyst`/`admin`/`superAdmin`/
`viewer`), with and without an org; only `/cases/` 404s. So this is a URL-normalisation gap, not a
broken view or an authorisation bug.

The API already solved the same problem the other way — `cases/urls.py`, `identity/urls.py`,
`compat/urls.py` etc. register **both** spellings as explicit `-slash` routes
(`test_t2_p1_surface.py::test_…_both_spellings`: *"`APPEND_SLASH` would 301 a POST and drop its
body, so both spellings must be explicit"*). The UI never did.

## 1. Summary

Two changes:

- **A. Trailing-slash tolerance for the UI** so `/cases/` (and every other UI page) reaches its view
  instead of 404ing.
- **B. Move "New case" off the left rail** into a **modal** opened from a button on the cases view,
  containing the form that is currently the `/cases/new` page.

## 2. Goals

- G1 `GET /cases/` (and the other UI page routes) resolves — with or without the trailing slash.
- G2 The left rail no longer offers "New case"; the cases view does, as a button.
- G3 The button opens a modal (native `<dialog>`, the existing `_modal.html`/`modal.js` pattern)
  containing the same fields as today's `/cases/new` page.
- G4 `/cases/new` remains a real page (deep link, no-JS fallback, progressive enhancement) — the
  button is `<a href="/cases/new" data-modal-open="new-case-modal">`, exactly like the existing
  "Manage shares" control.
- G5 The viewer guarantee is unchanged: no affordance, and the server still 404s a viewer's POST.
- G6 No regression in the hotkey/conformance invariants (`test_ui_nav.py`).

## 3. Non-goals

- No change to the API (`POST /api/v1/case` untouched).
- No change to the create service (`cases/create.py`) or its field set.
- No `case.created` automation trigger (still W102-4).
- Not converting other pages (alerts, observables) to modal creation in this plan.

## 3a. Open questions (asked; defaults applied unless the owner says otherwise)

| # | Question | Recommended default (assumed) |
|---|----------|-------------------------------|
| Q1 | Trailing slash: fix **only `/cases/`**, or make **every UI page** slash-tolerant? | **UI-wide** — a small middleware, not per-route aliases (DRY, future-proof; a single new page can't reintroduce the bug). |
| B1 | Mechanism: new middleware vs. explicit `-slash` aliases (the API idiom)? | **Middleware** `core/middleware.py::StripTrailingSlash`, scoped so it can never rewrite `/api/…` or `/admin/…`. |
| G3 | Keep `/cases/new` as a page, or modal-only? | **Keep the page** (deep link + no-JS fallback); the button enhances to a modal. |
| B2 | What happens to the `n` keyboard shortcut? | **Keep `n`** — it now opens the New-case modal (navigating to the cases view first when needed). Removing the rail item must not lose the shortcut (AGENTS.md §6 "keyboard-driven"). |
| B3 | On a modal validation error, where does it render? | **Re-render the cases list with the modal open and the submitted values kept** (not a post-redirect that closes the modal). |
| A2 | Should a `POST /cases/new/` work too? | **Yes** — the middleware returns **308** for unsafe methods (preserves method + body), **301** for GET/HEAD. |

## 3.3 Decisions taken (implemented)

- The rail `<li>` for "New case" is **removed**.
- The server-rendered cheat-sheet keeps a `New case` row (the shortcut survives) — `n` becomes a
  **non-navigating** key (`_NON_NAV_KEYS` in `test_ui_nav.py`).
- `case_list.html` gains a `New case` button (`href=/cases/new`, `data-modal-open="new-case-modal"`)
  and the server-rendered modal, both `can_mutate`-gated.

---

## 4. Design A — trailing-slash tolerance

`core/middleware.py` (new):

```python
class StripTrailingSlashMiddleware:
    """The mirror of ``APPEND_SLASH``: redirect ``/cases/`` → ``/cases``.

    The UI routes are registered slash-less and Django's ``APPEND_SLASH`` only *appends*;
    nothing strips, so a hand-typed or bookmarked ``/cases/`` 404s. Only a path whose
    slash-less form actually ``resolve()``s is redirected, and ``/api/`` + ``/admin/`` are
    excluded so a TheHive path is never rewritten. GET/HEAD → 301; anything unsafe → 308,
    which preserves the method and body (the reason the API registers explicit ``-slash``
    routes rather than relying on a 301).
    """
```

Registered in `MIDDLEWARE` immediately after `CommonMiddleware`.

## 5. Design B — the New-case modal

Reuse the established pattern (`case_detail.html` "Manage shares", `settings_organisations.html`
"New organisation"):

- **Extract the fields** from `case_create.html` into `ui/_case_create_fields.html`, parameterised by
  `form_id` (fields carry `form="{{ form_id }}"` when set, so they submit through the modal's footer
  `<form>`), plus `form_values`, `case_status_options`, `severity_choices`, `tlp_choices`,
  `pap_choices`. `case_create.html` keeps working (its own `<form>` wraps the same partial).
- **`_modal.html`** gains an `autoopen` flag → renders `data-autoopen="1"`.
- **`modal.js`** opens any `[data-autoopen]` dialog on `DOMContentLoaded` (progressive enhancement;
  without JS the flash message still shows).
- **`case_list.html`**: the actions-block button becomes
  `<a class="btn btn-primary" href="{% url 'ui-case-create' %}" data-modal-open="new-case-modal">New case</a>`,
  and the page includes `ui/_modal.html` with `modal_id="new-case-modal"`, `form_id="new-case-form"`,
  `body_template="ui/_case_create_fields.html"`, `form_action=reverse("ui-case-create")`,
  `confirm_text="Create case"`, `size="lg"`, `autoopen=open_new_case`, and a hidden `origin=cases`
  marker in the body partial.
- **`ui/views.py::case_create`**: on a validation error whose POST carries `origin=cases`, re-render
  **the cases list** with `open_new_case=True` + the bound `form_values` (same page the button lives
  on), instead of the standalone form page. Success still 302s to the case. A shared
  `_render_case_list(request, *, open_new_case=False, form_values=None)` helper supplies the modal's
  context so the two call sites cannot drift.
- **`n` shortcut**: `keys.js` drops `n` from `ROUTES`, keeps it in `CHEATSHEET`, and its keydown
  handler opens `[data-modal-open="new-case-modal"]` when present, else navigates to
  `/cases?new=1` (the view renders the list with `open_new_case=True`).

## 6. Files

| File | Change |
|---|---|
| `core/middleware.py` | **new** — `StripTrailingSlashMiddleware` |
| `amalthea/settings/base.py` | register the middleware |
| `ui/templates/ui/_case_create_fields.html` | **new** — shared field set |
| `ui/templates/ui/case_create.html` | use the shared partial (page unchanged in behaviour) |
| `ui/templates/ui/case_list.html` | modal button + include; `data-modal-open` |
| `ui/templates/ui/base.html` | remove the rail "New case" `<li>`; keep the `n` cheat-sheet row |
| `ui/templates/ui/_modal.html` | `autoopen` flag |
| `ui/static/ui/modal.js` | open `[data-autoopen]` dialogs on load |
| `ui/static/ui/keys.js` | `n` becomes a non-nav shortcut (opens the modal / goes to `/cases?new=1`) |
| `ui/views.py` | `case_create` error path → list + modal; `_render_case_list` helper |
| `tests/conformance/test_ui_trailing_slash.py` | **new** — `/cases/` → 301; `/api/v1/case/` untouched |
| `tests/conformance/test_ui_nav.py` | `_NON_NAV_KEYS` += `"n"` |
| `tests/conformance/test_ui_case_create.py` | modal + list error-path + `n` shortcut assertions |
| `docs/spec/api.md`, `docs/spec/deviations.md` | record the URL behaviour + the rail→modal change |

## 7. Acceptance criteria

- **T1.1** `GET /cases/` → **301** → `/cases` → 200 (for an authenticated writer).
- **T1.2** Every top-level UI page tolerates the trailing slash (`/alerts/`, `/observables/`,
  `/tags/`, `/taxonomy/`, `/settings/users/`, …), driven by one rule, not a per-route list.
- **T1.3** `/api/v1/case/` (and `/admin/…`) is **not** rewritten by the middleware.
- **T1.4** `POST /cases/new/` → **308** preserving method + body (asserted, not just GET).
- **T2.1** The rail contains no `New case` link (source-level, `base.html`).
- **T2.2** The cases list renders a `New case` control whose `href` is `/cases/new` and which carries
  `data-modal-open="new-case-modal"`.
- **T2.3** The cases list contains the server-rendered `#new-case-modal` dialog with the eleven form
  controls, and its footer form POSTs to `/cases/new`.
- **T2.4** A valid modal POST creates the case and 302s to it (identical behaviour to the page).
- **T2.5** An invalid modal POST (`origin=cases`) → 200, back on the **cases list**, modal present and
  marked auto-open, submitted values echoed, error shown.
- **T2.6** `n` opens the modal when the cases list is on screen, and navigates to `/cases?new=1`
  (which renders `open_new_case`) when it is not.
- **T2.7** `GET /cases?new=1` renders the dialog with `data-autoopen`.
- **T2.8** Viewer: no rail link, no button, no modal, no `#new-case-modal` in the case list, and
  `POST /cases/new` still 404s to the viewer via `@not_viewer` (Layer 1 unchanged). (`GET /cases/`
  correctly redirects for a viewer too — the list is readable; only creation is not.)
- **T2.9** `/cases/new` still renders standalone (no-JS fallback) with the same fields.
- **T3.1** `test_ui_nav.py` green: rail keys == `ROUTES` keys; cheat-sheet rows ≡ `CHEATSHEET`; `n`
  documented as a non-nav key.
- **T3.2** Full gate green: ruff + format + mypy + migrations + css-check + pytest.

## 8. Testing

- New `test_ui_trailing_slash.py`: 301/308 behaviour, the `/api/`+`/admin/` exclusion, and the
  no-op case (a genuinely unknown path stays 404).
- Extend `test_ui_case_create.py`: modal presence/gating, the `origin=cases` error re-render, the
  `n` shortcut markup, and the `?new=1` autoopen.
- `test_ui_nav.py` + `test_viewer_affordances.py` stay green (source-level rail/cheat-sheet parsing).
- Full `make check`.

## 8.1 Security

- The middleware only **redirects**; it never serves content and is scoped off `/api/` and `/admin/`.
- No new input surface: the modal posts exactly the fields the page already posted; CSRF is unchanged
  (`_modal.html` renders `{% csrf_token %}` in the footer form).
- The viewer guarantee is unchanged (server-side `not_viewer`; the modal is `can_mutate`-gated UX).
- `origin` is a fixed server-side literal (`cases`), never reflected into HTML or logs.

## 9. Risks

- **R1** Weakening `test_ui_nav`'s rail↔routes invariant by making `n` non-navigating — mitigated by
  keeping the cheat-sheet row and adding `n` to `_NON_NAV_KEYS` with a comment.
- **R2** A middleware that rewrites API paths — mitigated by the `/api/`+`/admin/` exclusion and a
  dedicated assertion (T1.3).
- **R3** Two form-render call sites drifting — mitigated by `_render_case_list` owning the modal context.
- **R4** Non-JS users lose the modal — by design: the button is a real link to `/cases/new`, and an
  error re-renders the list with the flash message.

## 10. Phases

- **P1** Middleware + settings + T1.* tests.
- **P2** Shared fields partial + modal markup + `_modal.html`/`modal.js` `autoopen`.
- **P3** `case_create` error path + `_render_case_list`; `n` shortcut rewiring.
- **P4** Conformance updates + docs; independent verification + security review.
