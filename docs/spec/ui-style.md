# UI style guidelines (Amalthea)

Dark-theme, keyboard-driven, TheHive-aligned UI (AGENTS.md §6, ADR-003). This document captures the reusable layout rules called out by TODO §12.9.

## Form control sizing
- Inputs, selects, and adjacent buttons in the same action/filter row must have equal height (34px control height).
- Use `.control-row` / `.action-row` (and `.filter-bar` where appropriate) for aligned rows. These set `align-items:center`, consistent gaps, and normalize button/select/input height.
- Standalone primary actions may keep default button sizing, but never mix mismatched heights inside one row.

## Action columns
- In tight table action columns, prefer compact icon-only actions with accessible names (`aria-label` and/or `title`).
- Decorative icons must be `aria-hidden="true"`.
- Icon-only controls must have an accessible name; never rely on icon alone.
- Keep text buttons for primary standalone actions outside dense tables.

## List/table layout
- Every list table lives inside a `.card`.
- Filtering/search controls live in a separate `.card` above the table card.
- Reference implementation: **Observables list view** (`ui/templates/ui/observable_list.html`).
- When paginating, include the shared list summary/pager patterns used by existing list views.

## Accessibility and interaction
- No native `confirm()`/`alert()`/`prompt()`; use `<dialog>` modals (`ui/_modal.html`).
- Severity/status is never signalled by colour alone; pair with text labels.
- Keep keyboard reachability: primary actions focusable, Esc closes dialogs, tab trap in modals.

## Reuse before inventing
- Reuse `.action-row` for aligned action/control rows. `.case-actions` remains as a backward-compatible alias.
- Reuse existing card/table/filter/button primitives in `ui/static/ui/app.css` before adding view-specific utilities.
