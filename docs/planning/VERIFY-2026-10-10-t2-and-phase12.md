# Verification Report: T2 endpoints (P1–P5) + Phase 12 (usability & orchestration)

- **Plans under review:**
  - `docs/planning/PLAN-2026-10-09-t2-endpoints.md` — status `SHIPPED` (header line 4)
  - `docs/planning/PLAN-2026-10-09-usability-orchestration.md` — status `Approved` (header line 4; T7.2 "set plan status SHIPPED" **not done**)
- **Verified at:** 2026-10-10 06:55 UTC
- **Revision:** `70327d7` (working tree clean)
- **Method:** read-only. Every verdict cites a file:line or a test name; gate re-run below.

## Gate re-run (evidence for "`make check` green" claims)

```
$ DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/pytest -q
1 failed, 991 passed, 3 skipped in 50.48s
FAILED tests/conformance/test_zzz_probe.py::test_z_delete_the_seed_row  (no such table: case_record)
$ DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/python manage.py check
System check identified no issues (0 silenced).
```

- `ruff`/`mypy`/`migrate --check` were **not** run here (only pytest + `manage.py check`).
- **Premise correction:** the brief said the gate was *941 passed / 3 skipped*; the actual count is
  **995 collected → 991 passed / 3 skipped / 1 failed** (SQLite-only `test_zzz_probe`).
  `COMPLETED.md` (§Summary, line 893) still says **986 / 3 + 3 failed** — all three numbers disagree.

## Verdict summary

### PLAN A — T2 endpoints (`AC6.1-P*`)

| AC | Verdict | One-line evidence |
|---|---|---|
| AC6.1-P1-a twin spellings | **MET** | `test_t2_p1_surface.py:276` posts to both `/tag` and `/tag/` → 201 |
| AC6.1-P1-b in-use delete + **mutation guard** | **DEVIATED** | refusal tested (`:126`, `test_t2_p5_surface.py:98`); the required "delete the reference first, then the guard allows it" guard is absent |
| AC6.1-P1-c `describe`/`user/current` vs pinned fixture | **DEVIATED** | `user/current` keys pinned (`test_t1_surface.py:770`); **`describe` has no pinned fixture** — only structural asserts (`test_t2_p1_surface.py:229`) |
| AC6.1-P1-d gate + boundary | **MET** | `test_wire_boundary.py:111` green; `test_t2_p1_surface.py` added; see gate note (1 known SQLite failure) |
| AC6.1-P2-a WS event + anon denied | **MET** | `test_t2_p2_surface.py:111,147,181` |
| AC6.1-P2-b read-only share cannot write | **MET** | `test_t2_p2_surface.py:200,250` |
| AC6.1-P2-c shapes + file | **MET** | `test_t2_p2_surface.py:278,314,323,367` |
| AC6.1-P3-a 413/415/traversal | **MET** | `test_t2_p3_attachments.py:75,90,99,111` |
| AC6.1-P3-b download name/type/sha | **MET** | `test_t2_p3_attachments.py:133` |
| AC6.1-P3-c cross-org rejected | **MET** | `test_t2_p3_attachments.py:190,208,220` |
| AC6.1-P4-a per-item bulk isolation | **MET** | `test_t2_p4_surface.py:57,83` |
| AC6.1-P4-b merge idempotent + provenance | **MET** | `test_t2_p4_surface.py:147,170` |
| AC6.1-P4-c template defaults applied | **MET** | `test_t2_p4_surface.py:192` |
| AC6.1-P5-a export round-trip + raw + authz | **MET** | `test_t2_p5_surface.py:131,148` |
| AC6.1-P5-b TTP in-use guard | **MET** | `test_t2_p5_surface.py:98` |

**Plan A: 12 MET · 3 DEVIATED · 0 NOT MET** (no AC is wholly unimplemented; the three deviations
are missing *proof*, not missing behavior).

### PLAN B — Phase 12 (`AC6.12-P*`)

| AC | Verdict | One-line evidence |
|---|---|---|
| P1-a no external request + HTMX fragment | **NOT MET** | HTMX fragment assert exists (`test_realtime.py:263,487`); **no "no external network" smoke**; CSP unasserted (T1.5) |
| P1-b `css-check` fails on staleness | **NOT MET** | `Makefile:94-96` compiles to `/dev/null` only; not in `check`; `ADR-003:55,62` falsely claims it runs in CI |
| P1-c icons `aria-hidden`/labelled + a11y test | **NOT MET** | no `icon` tag (`ui/templatetags/ui_tags.py`), no `fa-` markup, no a11y test |
| P2-a UI-authoring e2e (`Success` + ledger) | **NOT MET** | `test_ui_loop.py:69` seeds the playbook via ORM; no UI playbook route is referenced by any test |
| P2-b write-time config validation | **MET** | `test_playbook_authoring.py` validation matrix |
| P2-c `_meta` non-drift | **MET** | `test_playbook_authoring.py:43-68` |
| P2-d task.completed emits + fires playbook | **DEVIATED** | receiver + "exactly 2 emissions" assert (`test_playbook_authoring.py:361`); **no assert that a bound playbook fires** |
| P3-a artifact → N cases with working links | **NOT MET** | only N=2 *count* on case detail (`test_ui_loop.py:514`); no ≥3, no links, no `ui-observable-detail` test |
| P4-a each T2 family reachable in UI + UI test | **NOT MET** | no UI tests for tags/attachments/templates/procedures/export/bulk/observables/playbooks; **2 pages are broken** (below) |
| P4-b forms degrade to plain POST | **MET** | `test_realtime.py:487,514` (comment form only) |
| P5-a N failed logins → 429 + logged + reset | **NOT MET** | UI returns a 200 form error (`ui/views.py:109-125`), not 429; no test; no logger |
| P5-b cookie flags asserted | **DEVIATED** | `SESSION_COOKIE_HTTPONLY` set (`prod.py:53`) but **not asserted**; CSP unasserted |
| P6-a 10k events bounded + client pages | **NOT MET** | impl present (`cases/views.py:241-264`, `core/serializers.py:322`); zero tests; no UI paging; undocumented |
| P6-b shared-observable force + impact warning | **DEVIATED** | API 409/force tested (`test_t1_surface.py:604-730`); **UI impact warning absent** |
| P6-c EXPLAIN picks the GIN index | **NOT MET** | GIN shipped as migrations (`alerts/0010-0011`, `cases/0013`, `observables/0006`); no EXPLAIN test; `django.contrib.postgres` added to **`base.py:26`** not "Postgres only" |
| P7-a routes in `api.md` + no undocumented deviation | **NOT MET** | see "Doc drift" below |

**Plan B: 3 MET · 3 DEVIATED · 10 NOT MET.**

---

## Criterion-by-criterion detail (deviations & misses)

### A — AC6.1-P1-b (mutation guard)
The AC text is explicit: *"proven by a **mutation guard** that deletes the reference first and asserts
the guard then allows it."* `test_t2_p1_surface.py:126` and `test_t2_p5_surface.py:98` assert only the
**refusal** (400/409) and that the row survives. No test removes the referencing observable/procedure
and then re-tries the delete. The guard's *positive* half is unproven.

### A — AC6.1-P1-c (pinned fixture for `describe`)
`user/current` is pinned (`test_t1_surface.py:770 == PINNED_KEYS["user_example.json"]`), but there is
**no `describe_*.json` fixture** (`ls tests/fixtures/thehive/`: alert_observable, case, custom_event,
custom_field, task, user) and `test_describe_catalogue_covers_the_new_entities`
(`test_t2_p1_surface.py:229`) asserts hand-written expectations. The `describe` half of the AC is
fixture-free.

### B — AC6.12-P1-b (`css-check` staleness)
```
Makefile:94  css-check:
Makefile:95      @$(TAILWIND) -i ui/static/ui/app.css -o /dev/null --minify
Makefile:96      @echo "✓ CSS compiles cleanly"
```
This proves the CSS *parses*; it cannot detect a stale compiled artifact (it never diffs the output).
`css-check` is **not** a dependency of `check` (`Makefile:34-41`), and `.github/workflows/ci.yml:69`
runs only `make check`. `docs/decisions/ADR-003-frontend-assets.md:55,62` states the opposite
("CI runs `make css-check` as part of `make check`") — a documentation-vs-reality conflict.

### B — AC6.12-P1-c (icons / a11y)
`ui/templatetags/ui_tags.py` has no `icon` tag; `grep 'fa-' ui/templates/ui/*` is empty; the only
`aria-hidden` is the brand mark (`base.html:29`). No test asserts icon labelling. T1.4 was never
implemented even though TODO §6.10 / COMPLETED are marked `[x] SHIPPED`.

### B — AC6.12-P2-a (UI-authoring e2e)
`test_ui_loop.py` builds its playbook via the ORM (`:69 mail_playbook`); no test references any
`ui-playbook*` route (`grep 'reverse("ui-playbook' tests` → 0 hits) nor `POST /api/v1/playbook`. The
run is asserted as a **row**, not as a `Success` `AutomationRun` + ledger note produced through the
UI authoring path. `automation/urls.py` + views + seed migrations (`0005`, `0006`) exist — the
*surface* is there; the *end-to-end proof* required by the AC is not.

### B — AC6.12-P3-a (observable fan-out)
Only `test_ui_loop.py:514` (`test_an_artifact_seen_in_two_cases_shows_its_case_count`) covers this,
and it asserts the literal text `"2 case(s)"` on the **case** page — not N≥3, not "exactly those N
cases with **working links**", and not the fan-out page itself (`ui/observable_detail.html`, view
`ui/views.py:1070`). AC6.12-P3-b (co-occurrence wording) has **no** explanatory copy in the template
and no test.

### B — AC6.12-P4-a (UI reachability) — two pages are broken
`ui/urls.py` has no `path(...)` for the names the templates use:

```
$ DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/python -c "<reverse check>"
FAIL ui-case-attachment-upload -> NoReverseMatch   # ui/templates/ui/case_attachments.html:13
FAIL ui-case-tag-toggle        -> NoReverseMatch   # ui/templates/ui/case_tags.html:34
FAIL ui-case-attachment-delete -> NoReverseMatch   # ui/templates/ui/case_attachments.html:46
OK   ui-case-attachment-list   OK ui-case-tags     OK ui-observable-detail   OK ui-playbook-run
```
`{% url %}` resolves at render time, so `GET /cases/<id>/attachment` and `GET /cases/<id>/tags` raise
`NoReverseMatch` (HTTP 500). The views **do** exist but are unrouted:
`ui/views.py:1161 case_attachment_upload` and `ui/views.py:834 case_tag_toggle`. No test exercises
either page — the defect is invisible to the gate. This directly contradicts COMPLETED.md:822-831
("All T2 P1–P5 endpoints now have UI coverage", "Attachments UI", "Tags UI").

Also unrouted/dead: `case_attachment_upload` is never referenced anywhere except the broken template.

Other families (templates/export/bulk/procedures/observables/playbooks) have views + routes but **zero
UI tests** (only `test_ui_loop.py` and `test_realtime.py` use any `ui-*` name; neither touches them).

### B — AC6.12-P5-a (login throttling)
`identity/ratelimit.py` (5 attempts / 15 min window, 5 min lockout) is used **only** by
`ui/views.py:109-125`, where an exceeded limit renders `form_invalid(...)` → **HTTP 200**, not 429.
`compat/views.py:72 api_login` uses only the generic DRF `AnonRateThrottle` (100/min, pre-existing),
not an N-failed-login lockout. There is **no test** for the lockout (`grep ratelimit|lockout|429` in
tests → only `test_t1_surface.py:852`, the unrelated 101st-request throttle). Failures are surfaced
via the messages framework, not a logger. The reset-vs-window question the AC requires to be
"documented" lives only in a module docstring. COMPLETED.md:834-841 claims this phase shipped.

### B — AC6.12-P6-a / P6-c (timeline paging, GIN EXPLAIN)
- Paging works in code (`cases/views.py:241-264` reads `timelineAfter`/`timelineLimit` default 50 max
  200; `core/serializers.py:322` emits `timelinePagination.hasMore`) but **no test references any of
  those tokens**, `api.md:151` does not document them, and no UI "load more" exists.
- GIN indexes ship as `AddIndex(opclasses=['gin_jsonb_ops'])` migrations, but no test asserts the
  planner uses them (`test_indexes.py` only covers `ar_pending_idx`). `django.contrib.postgres` is in
  `amalthea/settings/base.py:26` (every environment), contradicting T6.3 ("for the Postgres settings
  only"). Neither choice is recorded in `deviations.md`.

### B — AC6.12-P7-a (routes / deviations register) — doc drift
- **Undocumented query params:** `timelineAfter`, `timelineLimit`, `timelinePagination` (api.md:151).
- **Undocumented behavior:** observable `?force=true` + `409` + `affected_cases` (implemented
  `cases/views.py:882-935`, tested `test_t1_surface.py:604-730`) — `grep force docs/spec/api.md` → 0
  hits; api.md:169-171 still says only "Not org-scoped (global mutation deferred — F2)".
- **Undocumented HTTP route:** `/ws/case/<id>/` HTTP fallback (`realtime/routing.py`, routed via
  `amalthea/urls.py`) is absent from `api.md` **and** from `realtime.md` (grep → no "fallback").
- **Plan-required `deviations.md` entry missing:** T7.1 mandates recording "the ADR-003 asset choice";
  `grep ADR-003 docs/spec/deviations.md` → 0 hits.
- **`data-model.md` stale:** GIN indexes are not listed for `Alert`/`Observable`/`Case`
  (`data-model.md:60-63,190-203`), and the new per-link `tags` JSONField (`cases/models.py:285`,
  `alerts/models.py:199`, migrations `cases/0014`, `alerts/0012`) is absent from
  `data-model.md:128-131,70-73`.
- **`api.md` mislabels** the playbook section "Playbook authoring (T2/Phase P2)" (api.md:222) — it is
  Phase 12 P2; its Evidence block (api.md:357-362) stops at `test_t2_p3_attachments`, missing
  `test_t2_p4_surface` (10), `test_t2_p5_surface` (7), `test_playbook_authoring` (15).
- **Plan status:** Phase 12 header still reads `Approved`; T7.2 "set plan status SHIPPED" not done.

---

## Extra checks requested

### Duplicate / contradictory `TODO.md` entries
- Header line 4 lists "§6.11 login rate-limiting shipped" and "§6.11 SESSION_COOKIE_HTTPONLY shipped"
  yet the Priority table (lines **19-23**) still lists **6.11 as Priority 1**, plus 6.5/6.2/6.3 as
  open — every one of which the same file marks `[x] SHIPPED` at lines 112-124.
- Duplicate IDs: **two `6.6`** (lines 117, 118), **two `6.7`** (lines 119, 120); line **124 (`6.11`)**
  duplicates the `6.6 leftover` label; line 4 uses "§6.11" for both the keyboard cheatsheet and login
  hardening.
- "Next Recommended Items" (lines 154-166): duplicate ranks (two `5`, two `6`), **6.3 listed three
  times** (160, 163, 165) and **6.2 twice** (161, 166).
- `COMPLETED.md`: duplicate headings at lines **850** and **880** ("Realtime HTTP fallback…") plus a
  third "Already documented above" stub at 884 — the same event recorded three times.
- `ui/urls.py` re-declares routes: `ui-case-export` **3×** (lines 30, 58, 95),
  `ui-case-procedure(-s)-create` **2×** (48-57, 84-93), `ui-case-template-list` **2×** (60, 82).
  Only the first definition of each name is reachable; the rest are dead.

### Routes present in code but absent from `api.md`
- All `/api/v1/…` routes I enumerated (`ingest`, `identity`, `observables`, `alerts`, `cases`,
  `automation`, `compat`, `query`, `core`) **are** documented. The only unregistered *route* is the
  non-`/api/v1/` realtime HTTP fallback `/ws/case/<id>/` (see P7-a). The larger gap is **query
  parameters** (`timelineAfter`/`timelineLimit`), not paths.

### Code changes not reflected in `deviations.md`
1. ADR-003 asset choice (explicitly required by T7.1) — absent.
2. Timeline default cap (50/200) on `case_detail` — an observable behavior change to the T1 wire,
   unrecorded.
3. `django.contrib.postgres` enabled globally (T6.3 said Postgres-only).
4. UI-only login lockout returning **200**, not the AC's 429 (the API is left to a generic 100/min
   throttle) — a mechanism choice the AC says must be "documented".
5. Per-link `tags` schema addition not reflected in `data-model.md`.

---

## Does not meet the plan

**Plan A (T2 endpoints)** — missing *proof* only:
1. **AC6.1-P1-b** — no mutation guard that removes the reference and shows the delete then succeeds.
2. **AC6.1-P1-c** — `describe` is not checked against a pinned TheHive fixture subset.
3. Gate caveat: 1 SQLite failure (`test_zzz_probe::test_z_delete_the_seed_row`) keeps the local
   `make check` from being green (documented as pre-existing; green on Postgres in CI).

**Plan B (Phase 12)** — missing *features*, *tests*, or *docs*:
1. **AC6.12-P1-b** — `css-check` cannot detect staleness; not in `make check`; ADR-003 contradicts code.
2. **AC6.12-P1-c** — no icon template tag, no Font Awesome icons, no a11y test (T1.4 untouched).
3. **AC6.12-P1-a** — no "no external request" smoke; CSP not asserted in `test_prod_settings.py` (T1.5).
4. **AC6.12-P2-a** — no UI-authoring end-to-end test; no test drives the playbook UI routes.
5. **AC6.12-P2-d** — no assertion that a `task.completed`-bound playbook actually fires.
6. **AC6.12-P3-a / -b** — only an N=2 count on the case page; no links, no ≥3, no fan-out-page test,
   no co-occurrence wording/test.
7. **AC6.12-P4-a** — 8 of 9 T2 families have no UI test; `case_tags` and `case_attachments` pages
   **500** (`NoReverseMatch`); views `case_tag_toggle` / `case_attachment_upload` are unrouted.
8. **AC6.12-P5-a** — no 429, no test, no failure logging, no documented window choice.
9. **AC6.12-P5-b** — `SESSION_COOKIE_HTTPONLY` / CSP never asserted.
10. **AC6.12-P6-a** — paging untested, undocumented, no client pager.
11. **AC6.12-P6-b** — API behavior tested; UI impact warning not implemented.
12. **AC6.12-P6-c** — no EXPLAIN test; `contrib.postgres` not Postgres-only.
13. **AC6.12-P7-a** — undocumented params, route, and behavior; ADR-003 asset choice missing from
    `deviations.md`; `data-model.md` and `api.md` Evidence stale; plan status not SHIPPED.
14. **T7.2** — `TODO.md` contradictions/duplicates; `COMPLETED.md` duplicate headings; Phase 12 plan
    still `Approved`.

## Blockers
None to *this* verification. The findings above are release-readiness blockers for Plan B, not for
the verifier.

## Recommendations (non-binding)
- Turn AC6.12-P4-a's "reachable UI path" into a parametrized smoke test that just `GET`s every `ui-*`
  list/detail URL — it would have caught the three `NoReverseMatch` names.
- Make `css-check` diff the compiled artifact against the committed one and add it to `check`.
- Add the missing `describe` fixture + an icon/a11y test to close P1-c/P1-c.
- Fix the doc drift in one pass: timeline params + `?force` + `/ws` fallback → `api.md`;
  GIN indexes + per-link tags → `data-model.md`; ADR-003 asset choice → `deviations.md`; retire the
  duplicate `TODO.md` rows; set the Phase 12 plan status.

---

# Wave A remediation log (2026-10-10)

Scope approved by the user: fix the confirmed unambiguous bugs (not the security items, which stay in
Wave B). Fixes applied on top of the reviewed tree:

| Finding | Fix | Files / evidence |
|---|---|---|
| **B1** `case_tags`/`case_attachments` 500 (`NoReverseMatch`) | Added the three missing routes and removed a duplicate export route; made delete a local deletion | `ui/urls.py`, `ui/views.py::case_attachment_delete`; `tests/conformance/test_ui_urls_smoke.py` (new) |
| **B2** prod CSP a no-op | `django-csp==4.0` only reads `CONTENT_SECURITY_POLICY`; added `"csp"` to `INSTALLED_APPS`, replaced legacy `CSP_*`; dropped inert env var | `amalthea/settings/prod.py`, `.env.example`, `tests/conformance/test_prod_settings.py` |
| **B3** `_bulk` bypassed the §6.5 guard | shared cross-case guard + pre-flight **409** (unless `?force=true`) | `cases/views.py`, `tests/conformance/test_observable_bulk_guard.py` |
| **Doc drift** | deduped `TODO.md` §6 / `COMPLETED.md`; refreshed priority table + Evidence; Phase 12 status → *partially shipped*; plan `triggered_by` default corrected `event`→`trigger` | `TODO.md`, `COMPLETED.md`, `docs/spec/{api,data-model}.md`, plan |

**New finding, fixed in Wave A — the mypy gate was already RED.** The verifier did not run mypy; running
the pinned gate (`mypy` with no path args → `pyproject.toml` scope) surfaced **8 errors in 3 files**,
all introduced by the 2026-10-09 Phase 12 commits whose "mypy clean" claim was therefore false:

- `realtime/views.py:13` — `websocket_fallback` `request` param unannotated.
- `identity/ratelimit.py` ×6 — `cache.get()` returns `Any`; `cache.ttl()` is not on `BaseCache`.
- `core/serializers.py:311` — `timeline_events` rebound from `QuerySet` to `list`.

All three fixed (type-only; behaviour unchanged). Gate now: `mypy` → **Success, 100 source files**;
`ruff check`/`ruff format --check` clean; `manage.py check` 0 issues; `makemigrations --check` no changes.
**Not in gate scope (`pyproject.toml` excludes `ui/`):** `ui/views.py:1120` imports `merge_cases` from
`alerts.escalation` (does not exist) inside the orphaned `case_merge` view — flagged, not fixed
(`TODO.md` #5; the view also renders a missing template and is `@require_GET`-blocked on POST).

**Re-verification requested:** re-run the AC table for AC6.12-P2-a, P3-a/b, P4-a, P5-a/b, P6-a/c, P7-a
and T7.2 against the current tree, plus the exemplar recommendations above (the new
`test_ui_urls_smoke.py` covers the parametrized `ui-*` GET smoke).

