# PLAN-2026-11-27 — Settings UI modularization (§13.10)

**Status:** Closed 2026-11-27 — implementation complete; user full `pytest` green.
**Owner:** Main agent; no subagents.
**Source:** `TODO.md` §13.10, §13 audit 2026-11-04.

## Summary
`ui/views/settings.py` (594 lines) was split into `ui/views/settings/` with organisation settings, user settings and the settings home separated. Superadmin guards, org-blocker text generation, audit calls, redirect targets and URL names are unchanged.

## Goals
- Move `_org_blockers_text` plus the three organisation views into `settings_orgs.py`.
- Move the three user views into `settings_users.py`.
- Move `settings_home` into `settings_home.py`.
- Preserve all imports from `ui.views.settings` through a package shim.

## Non-goals
- No URL changes in `ui/urls.py`.
- No changes to superadmin-only paths, org reassignment validation or last-superadmin guards.
- No template, form-field or message changes.

## Architecture
Three sibling modules with no imports between them; both settings domains import shared helpers from `..common`. The shim re-exports eight functions plus `logger` via explicit `__all__`. **ADR reference:** none.

## Data model / API / UI
No changes. All status codes, messages, redirect targets, context dictionaries and audit events are unchanged.

## Phases and tasks
- **T1 Baseline:** eight functions and `logger` inventoried; importers in `ui/views/__init__.py` and `ui/urls.py` identified.
- **T2 Split:** functions moved verbatim into org, user and home ownership.
- **T3 Shim:** compatibility exports added; old module removed.
- **T4 Verification:** AST body comparison, static gates, targeted settings, identity, URL and tenant suites.
- **AC:** no function missing or body-changed; all targeted suites pass.

## Security
Superadmin-only guards, admin org pins, lower-role hiding, uniform 404s for privileged targets, self-lockout and last-superadmin guards, and delete audit rows remain unchanged. No secrets introduced.

## Testing
- Structural AST comparison: 8/8 functions present, zero body changes.
- Targeted suites: **322 passed** across settings UI, settings-user scoping, org/user management, login throttle, user-org requirement, realtime org, pagination, P1 wiring and rejected-observable UI suites.
- Tenant settings/org/user subset: **44 passed, 59 deselected**.
- URL/viewer/fk smoke suites: **65 passed**.
- An xdist run of the tenant subset produced two unrelated `case-attachment-download` failures; the same subset and the two failing tests pass serially, so no production change was made.
- `make check`, `git diff --check`, secret scan clean.
- User-provided full `pytest` log `/tmp/amalthea-full-pytest.log` is green: **4464 passed, 3 skipped, 15 warnings, 2 subtests passed in 675.28s**.

## Risks
- Guard drift on superadmin/user-management paths: mitigated by verbatim moves and targeted suites.
- Import-depth mistakes: mitigated by static gates and compatibility exports.

## Deviations
- **D-13.10-1:** explicit `__all__` with 9 names added to the shim for ruff/mypy export clarity. Additive only.

## Open Questions
- None.
