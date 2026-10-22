# BRIEF — 2026-10-22 — Move organisation identity under the rail brand

**For:** `django-frontend`
**Scope:** markup + CSS + one test. No backend, model, API, or permission changes.

## Goal
The signed-in user's organisation is currently rendered near the bottom of the sidebar
(`.rail-foot`) as a bare, truncated name. Move it to the top of the rail, directly under the
"Amalthea" brand, and present it in two lines:

```
Organisation:
Default
```

- First line: the label `Organisation:`.
- Second line: the organisation name (`request.user.org.name`).
- SuperAdmin without an org membership: keep the existing `All organisations` link as the value.
- Signed-out pages: render no block at all (no empty label).

## Current state
- `ui/templates/ui/base.html:144-150` — org name / `All organisations` lives in `.rail-foot`.
- `ui/static/ui/app.src.css` — `.tenant-indicator` holds the old truncation rule; `.rail` is a
  24px-gap flex column.
- Tests `tests/conformance/test_ui_p1_wiring.py` F4a/F4c only assert the name is present
  somewhere in the page, so they stay valid after the move.

## Target markup (`ui/templates/ui/base.html`)
Wrap the brand and the new org block in `.rail-head`, and delete the org lines from `.rail-foot`.

```html
  <div class="rail-head">
    <a class="brand" href="{% url 'ui-dashboard' %}">
      <span class="brand-mark" aria-hidden="true">A</span>
      <span class="brand-text">Amalthea</span>
    </a>
    {% if request.user.is_authenticated %}
      {% if request.user.org or request.user.role == "superAdmin" %}
        <div class="rail-org">
          <span class="rail-org-label">Organisation:</span>
          {% if request.user.org %}
            <span class="rail-org-name" title="{{ request.user.org.name }}">{{ request.user.org.name }}</span>
          {% else %}
            <a class="rail-org-name" href="{% url 'ui-settings-organisations' %}">All organisations</a>
          {% endif %}
        </div>
      {% endif %}
    {% endif %}
  </div>
```

Then `.rail-foot` keeps only: Change password, Keyboard shortcuts, Sign out (or Sign in when
signed out). Remove the `{% if request.user.org %}` / `{% elif request.user.role == "superAdmin" %}`
block from it.

Keep every `{% ... %}` tag on a single line — the template lexer is line-based.

## Target CSS (`ui/static/ui/app.src.css`)
Add near the rail rules:

```css
/* Rail identity block — organisation context pinned under the brand. */
.rail-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rail-org {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  line-height: 1.35;
}

.rail-org-label {
  color: var(--color-text-dim);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.rail-org-name {
  color: var(--color-text-dim);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

a.rail-org-name:hover {
  color: var(--color-text);
}
```

- The `.rail-head` wrapper keeps the brand ↔ org gap tight while preserving the rail's existing
  24px gap before `.rail-links`.
- `--color-text-dim` (not `-faint`) keeps the label at WCAG-AA contrast on `--color-bg-inset`.
- Do not `text-transform: uppercase` — the requested display text is `Organisation:`.
- Delete the now-unused `.tenant-indicator` rules (only `base.html` used the class).

## Tasks and acceptance criteria

### T1 — Markup move (`ui/templates/ui/base.html`)
- [AC1] `class="rail-head"` wraps the brand and the org block; the org block appears before
  `<ul class="rail-links">` and is no longer inside `.rail-foot`.
- [AC2] User with an org sees the label on line 1 and `request.user.org.name` on line 2.
- [AC3] SuperAdmin without an org sees the label and the `All organisations` link (still pointing
  at `ui-settings-organisations`) as the value.
- [AC4] Signed-out pages render no `.rail-org` block and no label; the sign-in link remains in
  `.rail-foot`.

### T2 — CSS (`ui/static/ui/app.src.css` → `app.css`)
- [AC5] `.rail-head`, `.rail-org`, `.rail-org-label`, `.rail-org-name` implement the layout above;
  long names truncate with an ellipsis and expose the full value via `title`.
- [AC6] `.tenant-indicator` rules removed; no other selector regressions; `make css` regenerates
  `ui/static/ui/app.css` (never hand-edit the generated file).

### T3 — Regression test (`tests/conformance/test_ui_p1_wiring.py`)
- [AC7] A test asserts, for a user with an org, the order
  `.rail-head` → `.rail-org` → `.rail-links` → `.rail-foot`, plus the label and org name.
  A second case covers superAdmin without an org (`All organisations` inside `.rail-org`), and a
  third covers the signed-out login page (no `.rail-org`, no label).

### T4 — Verification
- [AC8] `make css` then `make check` green; run the targeted test file with
  `.venv/bin/python -m pytest tests/conformance/test_ui_p1_wiring.py -q`.
- No migrations, no new queries (the rail already resolves `request.user.org`).

## Constraints
- Dark theme only; no new colours; use existing design tokens.
- Do not change auth action order or the sign-out form.
- Do not add a context processor or template tag.
- Accessibility: plain text order is "Organisation: <name>"; no heading level change; no icon-only
  control; keep focus styles untouched.

## Decisions (confirmed 2026-10-22)
1. **Target** — the left navigation rail (the only sidebar).
2. **Spelling** — British `Organisation:`, matching the rest of the UI copy.
3. **SuperAdmin without org** — keep the label plus the existing `All organisations` link as the
   value. Keep the link text sentence-case as it is today.
4. **Long names** — single-line ellipsis with the full value in a `title` tooltip.

## Risks / out of scope
- The rail is `height: 100vh` without its own scrollbar; the extra block can overflow on very short
  viewports. Pre-existing behaviour — do not add rail scrolling in this change.
