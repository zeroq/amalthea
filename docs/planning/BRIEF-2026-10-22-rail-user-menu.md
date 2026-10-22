# BRIEF — 2026-10-22 — Rail user menu (avatar + account submenu)

**For:** `django-frontend`
**Scope:** markup + CSS + one small progressive-enhancement script + tests. No backend, model,
API, permission, or auth-flow changes.

## Goal
Replace the three flat account links at the bottom of the left navigation rail with a compact
profile control:

```
       ( user )          <- round avatar, generic user glyph
        goebej           <- username directly below
```

Clicking the round avatar (or the username) opens a small submenu above it:

- Change password
- Keyboard shortcuts
- Sign out

`Sign out` remains a POST form with CSRF. The existing `cheatsheet-modal` continues to be the
single source of the shortcut list, and the global `?` shortcut is unchanged. Signed-out pages
show only the existing `Sign in` link.

## Current state
- `ui/templates/ui/base.html:157-166` — `.rail-foot` holds `Change password`, a
  `data-modal-open="cheatsheet-modal"` button, and the logout POST form.
- `ui/static/ui/app.src.css` — `.rail-foot` is bottom-pinned by `margin-top: auto`; `.linkish`
  styles the current controls.
- `ui/static/ui/modal.js` — owns `<dialog>` open/close; do not conflate it with this menu.
- `tests/conformance/test_ui_p1_wiring.py::test_base_html_has_password_change_link` asserts
  Change password appears before Sign out; that ordering is preserved.

## Decisions (confirmed 2026-10-22)
1. **Trigger shape** — generic user glyph (`fa-user`) in a 36px round, accent-tinted circle.
   Username below, 12px, single-line ellipsis with `title`; both the circle and the username are
   part of the same clickable `<summary>`.
2. **Alignment** — centred in the rail at the bottom. The submenu opens upward
   (`bottom: calc(100% + 8px)`) because there is no space below.
3. **Copy** — keep the app's sentence case: `Change password`, `Keyboard shortcuts`, `Sign out`.
   (The username is already visible above, so `Sign out` does not repeat it.)
4. **Disclosure mechanics** — native `<details>` / `<summary>` so opening works without JS and
   the summary exposes expanded/collapsed state to assistive tech. A tiny
   `ui/static/ui/rail-user.js` adds outside-click and Escape dismissal and closes the menu after
   an item is chosen; with JS disabled the menu still opens, closes by clicking the summary, and
   all three actions work.

## Target markup (`ui/templates/ui/base.html`, `.rail-foot`)

```html
    <div class="rail-foot">
      {% if request.user.is_authenticated %}
        <details class="rail-user">
          <summary class="rail-user-trigger" aria-label="Account menu for {{ request.user.login }}" title="Account menu">
            <span class="rail-user-avatar"><i class="fa-solid fa-user" aria-hidden="true"></i></span>
            <span class="rail-user-name" title="{{ request.user.login }}">{{ request.user.login }}</span>
          </summary>
          <ul class="rail-user-menu" id="rail-user-menu">
            <li>
              <a class="rail-user-menu-item" href="{% url 'ui-password-change' %}">
                <i class="fa-solid fa-key" aria-hidden="true"></i> Change password
              </a>
            </li>
            <li>
              <button type="button" class="rail-user-menu-item" data-modal-open="cheatsheet-modal">
                <i class="fa-solid fa-circle-question" aria-hidden="true"></i> Keyboard shortcuts
                <span class="k">?</span>
              </button>
            </li>
            <li class="rail-user-menu-sep">
              <form method="post" action="{% url 'logout' %}">{% csrf_token %}
                <button type="submit" class="rail-user-menu-item">
                  <i class="fa-solid fa-right-from-bracket" aria-hidden="true"></i> Sign out
                </button>
              </form>
            </li>
          </ul>
        </details>
      {% else %}
        <a href="{% url 'login' %}">Sign in</a>
      {% endif %}
    </div>
```

Font Awesome icons `fa-user`, `fa-key`, `fa-circle-question`, `fa-right-from-bracket` are already
present in `ui/static/vendor/fontawesome/css/fontawesome-subset.min.css`; do not add a new icon
pack.

## Target CSS (`ui/static/ui/app.src.css`)

Add a small block near the rail rules (exact values may be tuned by one or two pixels):

```css
/* Rail user menu — avatar trigger pinned at the bottom of the rail. */
.rail-user {
  position: relative;
}

.rail-user-trigger {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  color: var(--color-text-dim);
  cursor: pointer;
  list-style: none;
}
.rail-user-trigger::-webkit-details-marker {
  display: none;
}
.rail-user-trigger::marker {
  content: "";
}

.rail-user-avatar {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--color-accent);
  border-radius: 50%;
  background: color-mix(in oklab, var(--color-accent) 16%, transparent);
  color: var(--color-accent);
  font-size: 14px;
  line-height: 1;
}
.rail-user-trigger:hover .rail-user-avatar,
.rail-user[open] .rail-user-avatar {
  border-color: var(--color-text);
  color: var(--color-text);
}

.rail-user-name {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-dim);
  font-size: 12px;
}

.rail-user-menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  left: 0;
  z-index: 50;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--color-bg-raised);
  border: 1px solid var(--color-line-strong);
  border-radius: var(--radius);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
}

.rail-user-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 9px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--color-text-dim);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}
.rail-user-menu-item:hover {
  background: var(--color-bg-inset);
  color: var(--color-text);
}
.rail-user-menu-item i {
  width: 14px;
  text-align: center;
  opacity: 0.85;
}
.rail-user-menu-item .k {
  margin-left: auto;
}
.rail-user-menu form {
  margin: 0;
}
.rail-user-menu-sep {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--color-line);
}
```

Do not hand-edit `ui/static/ui/app.css`; regenerate with `make css`.

## Target JS (`ui/static/ui/rail-user.js`)

New external file, loaded in `base.html` with `defer` next to the other UI scripts:

```html
<script src="{% static 'ui/rail-user.js' %}" defer></script>
```

Behaviour:

1. For each `details.rail-user`, listen for document clicks; if the details is open and the click
   target is outside it, set `open = false`.
2. On `Escape` while open, close the details and return focus to its `<summary>`.
3. When a `.rail-user-menu-item` is activated, close the menu after a `setTimeout(..., 0)` so the
   default action (navigation, form submit, or `data-modal-open`) still runs.

Native `<details>` handles the primary open/close toggle and expanded state; the script is
progressive enhancement only. CSP-safe: no inline script, no `innerHTML`, no data.

## Tasks and acceptance criteria

### T1 — Markup (`ui/templates/ui/base.html`)
- [AC1] `.rail-foot` for an authenticated user renders `<details class="rail-user">` with a
  `<summary>` trigger containing a round generic user glyph and the username below it; the
  summary has `aria-label="Account menu for <login>"`.
- [AC2] The submenu contains, in order: Change password link, Keyboard shortcuts button wired to
  `cheatsheet-modal`, Sign out POST form with CSRF. The three old flat controls are gone.
- [AC3] Signed-out pages render the existing `Sign in` link and no `.rail-user` block.
- [AC4] Keyboard shortcuts still opens the existing `#cheatsheet-modal`; the global `?` shortcut is
  untouched.

### T2 — CSS (`ui/static/ui/app.src.css`)
- [AC5] Avatar is a 36px circle with the generic user glyph centred; the username is directly
  below (also centred) and truncates with an ellipsis while exposing the full value via `title`.
- [AC6] Menu is bottom-anchored, opens upward, spans the rail content width, sits above the nav,
  and its items have a visible focus/hover state using existing tokens. No new colours.
- [AC7] `make css` regenerates `app.css`; CSS drift check passes.

### T3 — JS (`ui/static/ui/rail-user.js`, `base.html`)
- [AC8] Outside click closes the open menu; `Escape` closes it and returns focus to the summary;
  choosing an item closes it without cancelling the item's default action. Without JS, the
  `<details>` still opens/closes on summary activation.

### T4 — Tests (`tests/conformance/test_ui_p1_wiring.py`)
- [AC9] Authenticated page: `.rail-user` exists inside `.rail-foot`; summary `aria-label` contains
  the login; avatar contains the `fa-user` icon; username appears in `.rail-user-name` with a
  matching `title`; the three menu actions appear in the order above and the logout form posts to
  `reverse("logout")`.
- [AC10] Signed-out login page: no `.rail-user`, no “Change password”/“Keyboard shortcuts”/“Sign
  out” menu; `Sign in` is present.
- [AC11] Existing `test_base_html_has_password_change_link` still passes (Change password before
  Sign out); update its docstring to point at the menu rather than “next to Sign out”.
- [AC12] `rail-user.js` is referenced by `base.html` and exists on disk.

### T5 — Verification
- [AC13] `make css` then `make check` green.
- [AC14] `.venv/bin/python -m pytest tests/conformance/test_ui_p1_wiring.py -q` green.
- No migrations; no new queries; no auth/permission changes.

## Constraints
- Keep every Django template tag on one line (line-based template lexer).
- Use the existing dark-theme tokens; `--color-text-dim` for readable labels (AA contrast).
- Use the disclosure pattern (`<details>`/`<summary>`); do not add `role="menu"` or arrow-key
  menu semantics — plain tab order through the three actions is the intended interaction.
- Sign out stays a POST with CSRF; never turn it into a link.
- Do not move the cheatsheet modal or change `keys.js`.
- Do not commit or push; this lands on top of the existing uncommitted work.

## Risks / out of scope
- The rail is `height: 100vh` with no scroll container; the avatar block is shorter than the
  current three-line footer, so this does not increase overflow risk. Do not add rail scrolling.
- No avatar image upload exists; a generic user glyph is the identity representation. A future
  user-profile image can replace the inner content without changing the menu.
