# Amalthea — UI Style & Layout Specification

Status: **derived from code** · Written 2026-10-19 (plan [`PLAN-2026-10-19-ui-layout-standardization.md`](../planning/PLAN-2026-10-19-ui-layout-standardization.md))
· Owner: planner

This document is the **house style** for Amalthea's server-rendered UI — the conventions every template
must follow so the platform reads as one product. It is the operational companion to `AGENTS.md` §6 and
is pinned by the conformance suite (`tests/conformance/test_ui_css_tokens.py`, `test_ui_loop.py`). Where
this document and the code disagree, the code wins and the disagreement is a bug in one of them.

## 0. Stack and asset pipeline

- **Markup:** Django templates, server-rendered. Semantic HTML first; HTMX for partial interactivity;
  plain forms/POSTs must still work when HTMX is unavailable (progressive enhancement).
- **Styling:** Tailwind CSS v4, compiled by the vendored `scripts/tailwindcss` binary (no Node in the
  runtime path).
- **Two files, two roles — do not confuse them:**
  - `ui/static/ui/app.src.css` — the **only hand-edited** stylesheet. Its header says so. All component
    CSS (`.btn`, `.card`, `.data-table`, `.md`, …) and the Tailwind entry live here.
  - `ui/static/ui/app.css` — the **generated** artifact, committed for serving. Its header says so.
    Never edit it by hand; your edit will be erased by the next `make css`.
  - `make css` compiles the source → the artifact. `make css-check` byte-compares the committed artifact
    against a fresh compile and **fails on drift** (on hosts where the binary cannot run it *skips
    loudly*, which is explicitly not a pass).
  - Consequence: a rule added directly to `app.css` is invisible to the compiler and vanishes on the next
    build. If a component class looks wrong in the browser, check `app.src.css` first.

## 1. Control heights and vertical rhythm

All interactive controls in a row must share one height so the row reads as a single line. The **unit of
account** used by every action row is **34 px**.

The base stylesheet gives bare inputs/selects `padding: 8px 10px` with a 13 px font (≈34 px tall). Buttons
(`.btn`) default to `padding: 8px 15px`. The two do not naturally match, so **any rule that puts buttons
and form controls on one line must pin both to 34 px** with:

```css
/* the deferred/neutral utility — implemented as .control-row in Phase C */
.control-row select,
.control-row .btn {
  box-sizing: border-box;
  height: 34px;
  padding-top: 0;
  padding-bottom: 0;
}
```

**Before** (controls drift apart — the row is ragged):

```html
<form class="inline-form">
  <input type="text" name="q" placeholder="Filter...">
  <select name="type"><option>All</option></select>
  <button class="btn btn-primary" type="submit">Filter</button>
  <a class="btn" href="...">Clear</a>
</form>
```

**After** (one row, equal heights):

```html
<form method="get" class="inline-form control-row">
  <input type="text" name="q" placeholder="Filter..." class="filter-q">
  <select name="type" class="filter-type"><option>All</option></select>
  <button class="btn btn-primary" type="submit">Filter</button>
  <a class="btn" href="...">Clear</a>
</form>
```

Never hand-set heights on individual controls inside a row; use the row utility so the rule lives in one
place. Do not use `!important`.

## 2. The aligned action-row utility

Amalthea has, historically, grown three names for the same idea — a horizontal, wrapping, vertically
centred row of controls of equal height: `.filter-bar`, `.case-actions`, and ad-hoc `inline-form` rows.
They are **one** utility and this specification names it **`.control-row`** (the neutral name chosen in
plan decision **D4**).

```css
.control-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.control-row .btn,
.control-row select { /* the 34px pin from §1 */ }
```

Usage rule:

- **Any** horizontal group of ≥2 controls (filter forms, per-row action groups, card headers with buttons)
  carries `class="... control-row"`.
- `.filter-bar` is **folded into** `.control-row` and kept only as a one-wave **alias** (plan decision
  **D5**) so the rename is reviewable; new markup uses `.control-row` and the alias is removed once no
  template references it (asserted by the guard test, AC-9).
- `.case-actions` is likewise an **alias** for one wave; the case-flavoured name was already a lie (it is
  used in `source_detail.html` and `settings_sources.html`, not only on cases).
- Do **not** invent a fourth name. If you need a new row, it is a `.control-row` with a modifier class, not
  a new base class.

**Before:** `<form class="inline-form filter-bar case-actions">` — three names, no single source of truth.
**After:** `<form class="inline-form control-row">`.

## 3. Icons

Icons come from the **vendored Font Awesome Free subset** at
`ui/static/vendor/fontawesome/css/fontawesome-subset.min.css`. It is a **subset**, not the full library —
see §3.2 for the exact inventory and the rule for adding one.

### 3.1 Markup rules

- **Decorative icons** (the icon repeats what the adjacent text already says, e.g. a pencil next to the
  word "Edit") must carry `aria-hidden="true"` and must not be the only carrier of meaning:

  ```html
  <button class="btn" type="submit"><i class="fa-solid fa-pen" aria-hidden="true"></i> Edit</button>
  ```

- **Icon-only controls** (the icon *is* the label) are allowed only for **secondary, ubiquitous row
  actions** (the View/Edit/Delete class) — plan decision **D6**. They must carry an **accessible name**
  via `aria-label` (a `title` may supplement it but never substitutes for it):

  ```html
  <a class="btn btn-icon" href="{% url 'ui-observable-detail' o.id %}"
     aria-label="View observable {{ o.data }}"><i class="fa-solid fa-eye" aria-hidden="true"></i></a>
  ```

  An icon-only control with no accessible name is a bug; the AC-15 guard fails the build on it.

- **Primary standalone actions keep a visible text label.** An icon may *supplement* the label, never
  replace it (AGENTS.md §6). "Escalate to case", "Create case", "Filter" stay as text buttons.
- **Destructive actions are never distinguished by colour alone.** An icon-only red trigger is not enough;
  the accessible name must carry the meaning, and a destructive confirm uses the modal pattern (§5).

### 3.2 The subset constraint, and adding an icon

The subset currently ships **127** icon classes. The names below **exist**:

`fa-eye`, `fa-pen`, `fa-trash`, `fa-xmark`, `fa-gear`, `fa-cogs`, `fa-download`, `fa-upload`, `fa-key`,
`fa-rotate-left`, `fa-rotate-right`, `fa-sync`, `fa-circle-check`, `fa-circle-xmark`, `fa-circle-info`,
`fa-circle-exclamation`, `fa-circle-question`, `fa-triangle-exclamation`, `fa-check`, `fa-plus`,
`fa-square-plus`, `fa-square-minus`, `fa-magnifying-glass`, `fa-bell`, `fa-tag`, `fa-user`, `fa-users`,
`fa-user-shield`, `fa-shield-halved`, `fa-server`, `fa-database`, `fa-robot`, `fa-bolt`, `fa-fire`,
`fa-clock`, `fa-calendar`, `fa-file-lines`, `fa-folder-open`, `fa-sitemap`, `fa-diagram-project`,
`fa-network-wired`, `fa-globe`, `fa-terminal`, `fa-code`, `fa-rss`, `fa-book`, `fa-gauge-high`,
`fa-hourglass`, `fa-stopwatch`, `fa-check`, `fa-flag`, plus the icon-family helpers `fa-solid`,
`fa-regular`, `fa-brands`.

**Do not use** these — they are **not** in the subset and render as empty boxes: `fa-play`, `fa-pause`,
`fa-ban`, `fa-pencil` (use `fa-pen`). Where the natural glyph is missing and no substitute fits, keep a
**text button** and note it, rather than shipping a blank icon (plan decision **D3**).

**Rule for adding a new icon:** do not hand-edit the subset. Extending it is a change to a vendored,
supply-chain-relevant asset and needs its own review via the vendoring path (`scripts/`), documented as a
follow-up. Until then, choose an icon from §3.2 or use text.

## 4. List-surface layout

Every list page is **two cards**: one card holds the **filter controls**; a separate card holds the
**table** (and its pager/empty state). The reference implementation is
[`ui/templates/ui/observable_list.html`](../../ui/templates/ui/observable_list.html).

**Before** (one card, filter inline above the table — the table is not contained, filters mix with data):

```html
<h1>Observables</h1>
<form method="get" class="inline-form">…</form>
<table class="data-table">…</table>
```

**After** (the reference shape — filters in their own card, table wrapped in a card + `.table-wrap`):

```html
<div class="card">
  <form method="get" class="inline-form control-row">
    <input type="text" name="q" value="{{ query|default:'' }}" placeholder="Filter by value..." class="filter-q">
    <select name="type" class="filter-type">…</select>
    <button class="btn btn-primary" type="submit">Filter</button>
    <a class="btn" href="{% url 'ui-observable-list' %}">Clear</a>
  </form>
</div>

<div class="card">
  {% include "ui/_list_summary.html" with entity_name="observable" entity_name_plural="observables" %}
  <div class="table-wrap">
    <table class="data-table">
      <caption class="sr-only">Observables</caption>
      …
    </table>
  </div>
  {% include "ui/_pager.html" with entity_name="Observable" %}
</div>
```

Rules:

- A `<table class="data-table">` is **always** inside a `.table-wrap` (which scrolls on narrow screens)
  and **always** inside a `.card`. No bare tables.
- Every table has a `<caption class="sr-only">` naming it (screen-reader users navigate by caption).
- The empty/no-match state is **honest**: an empty filter result reads "No X match your filters" with a
  link to clear them; a genuinely empty collection reads "No X found" — the two are distinct (§ the
  reference's two branches). Do not show an empty table and call it "no results".

## 5. Dialogs and modals

Confirmation and input dialogs use the custom modal pattern — a `<dialog>` element with a styled backdrop
— **never** the browser-native `confirm()`/`alert()`/`prompt()` (AGENTS.md §6). The `?` keyboard
cheat-sheet is itself a modal and reuses the pattern.

- The dialog box is styled by `dialog.cheatsheet-modal` in the stylesheet (border, raised background,
  `max-width: 400px`, `max-height: 80vh`, `overflow: auto`). Do not re-style per instance.
- Keyboard: `Esc` closes; focus is trapped within the dialog while open; focus returns to the trigger on
  close.
- Destructive confirms name the object and use a `.btn-err` (or `.btn` + accessible name) for the
  affirmative action.

## 6. Keyboard and accessibility baseline

- **Every primary action is reachable without a mouse**; shortcuts are discoverable via the `?` modal.
- Focus is always visible: the stylesheet's global `:focus-visible` rule (`outline: 2px solid
  var(--color-accent)`) must never be suppressed (`outline: none` is itself guarded by a test).
- **Severity is never signalled by colour alone** — the colour is always paired with a text label
  (AGENTS.md §6; guarded by `test_severity_is_shown_as_text_not_colour_alone`).
- Icon-only controls require an accessible name (§3.1). Primary actions keep visible labels.

## 7. Conformance

This document is only a convention if the build enforces it. The guards live in
`tests/conformance/test_ui_css_tokens.py` (stylesheet must not silently break: undefined `--color-*`
tokens, `outline: none`, tab scrollbar, cheat-sheet modal rule, filter input rule) and
`tests/conformance/test_ui_loop.py` (severity text pairing, list-surface markup). A change that breaks a
rule here must update this document and the guard in the same wave; a new convention must add a guard.
