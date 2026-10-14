# Verification Report: Create a case manually from the UI (TODO §10.2)

Plan: `docs/planning/PLAN-2026-10-13-ui-case-create.md`
Deviations: `docs/spec/deviations.md` → **Wave 10.2** (W102-1…W102-5)
Verified: 2026-10-13 11:38 UTC
Verifier: verification specialist (read-only)
Status: **Partial** — the P1–P4 implementation is complete, correct and green; the plan's
P4b/P5 *deliverables* are incomplete (security review absent, `TODO.md`/`COMPLETED.md`
bookkeeping not done), and several ACs rest on code inspection rather than a passing test.

## Summary

- Criteria evaluated: 37 (P1×6 AC + 5 tasks, P2×11 AC + 5 tasks, P3×2 AC + 1 task, P4×5 AC + 3 tasks, P4b×2, P5×4) + §13 (7 items)
- **Met:** 31
- **Met (by inspection, no dedicated test):** 4 (P2-f button half, P2-g, P3-b, T3.1 cheat-sheet row is tested but see note)
- **Partially met / untested sub-criterion:** 6 (P2-a labels, P2-c echo, P2-d `severity=99`/`tlp="abc"`, T1.2 non-integer, T1.3b warnings, T2.5 warning flash)
- **Not Met:** 4 (T4.5b security review, T5.2 `TODO.md`, T5.3 `COMPLETED.md`, §9 mass-assignment pin test)
- Independent evidence: **1646 passed, 3 skipped** (full suite) · targeted suites **25 passed** /
  **110 passed** / **193 passed** · `manage.py check` clean · ruff + format + mypy (106 files) + css
  + `makemigrations --check` all clean (the whole `make check` gate except the suite already run).

## Command transcript (independently re-run)

```
$ .venv/bin/pytest tests/conformance/test_ui_case_create.py -q
25 passed in 5.95s

$ .venv/bin/pytest tests/conformance/test_ui_nav.py tests/conformance/test_ui_urls_smoke.py \
    tests/conformance/test_tenant_scoping.py tests/conformance/test_viewer_readonly_ui.py -q
110 passed in 16.94s

$ .venv/bin/python manage.py check
System check identified no issues (0 silenced).

$ .venv/bin/python -m pytest -q
1646 passed, 3 skipped in 103.16s (0:01:43)

$ .venv/bin/python manage.py makemigrations --check --dry-run
No changes detected

$ .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
All checks passed! / 267 files already formatted / Success: no issues found in 106 source files

$ make css-check
✓ Tailwind input compiles (compile check only — this does NOT detect drift)
```

The 3 skips are the pre-existing Postgres-only tests (unrelated).

## Deviations (pre-approved — verified against §0, **not** counted as failures)

| ID | Plan text it supersedes | Implemented | Verifier finding |
|---|---|---|---|
| **W102-2** | §4 Extraction row, §7 checkbox row, T1.1 `extract=False`, **P1-f**, **P2-i** | `create_case` has **no** `extract` parameter; extraction runs unconditionally over title+description; payload `extract`/`observables` only *adds* strings. UI shows a hint line, no checkbox. | **Confirmed.** `cases/create.py:137-142` (no gate), `ui/templates/ui/case_create.html:99` (hint, no checkbox), `keys.js`/`case_list.html` unaffected. Matches the pre-change `_create_case` (`git diff HEAD -- cases/views.py`). |
| **W102-3** | ADR-002 §D11 tolerance: `severity:0`/empty silently defaulted, out-of-range → 500 | Non-integer/out-of-range `severity`/`tlp`/`pap` → clean 400; empty/null still model default | **Confirmed.** Range check `cases/update.py:84-93` (raise `CaseUpdateError`) reached from `create_case`; empty/null skipped at `cases/create.py:74-77`. `test_api_case_create_severity_nine_400`, `test_ui_case_create_post_severity_zero_rerenders`, `test_ui_case_create_post_tlp_nine_rerenders`. |
| **W102-1** | API + UI had separate construction logic | Both call `cases.create.create_case` | **Confirmed.** `cases/views.py:309`, `ui/views.py:540`. |
| **W102-4** | No `case.created` automation trigger | Not added | **Confirmed.** `automation/dispatcher.py::TRIGGER_EVENTS` unchanged; extraction still fires `observable.created` via `observables/extractor.py:333` → `registry.dispatch_observable_linked` (`automation/registry.py:155-166`). |
| **W102-5** | `CaseUpdateError` retained | Not renamed | **Confirmed.** `cases/update.py:35`. |

## Summary table — criterion by criterion

| Phase | Criterion | Status | Evidence |
|---|---|---|---|
| P1 | T1.1 service `create_case(payload,*,actor,org)` (no `extract`), atomic, reuses helpers, `case-created` ledger | **Met** | `cases/create.py:89-152`; `@transaction.atomic` `:89`; `append_timeline_event(...kind="case-created")` `:144-150`. |
| P1 | AC1.1 blank/whitespace title raises `CaseUpdateError`, writes nothing | **Met** | `cases/create.py:114-116`; `test_api_case_create_blank_title_400`, `test_ui_case_create_post_empty_title_rerenders`, `test_ui_case_create_post_blank_title_rerenders`. |
| P1 | T1.2 range-validate `severity`/`tlp`/`pap` before any write | **Met** (non-integer branch untested) | `cases/update.py:84-93`; out-of-range tested (severity=9 API; 0/9 UI). The `except (TypeError, ValueError)` non-integer branch has **no test** (`severity="x"` is never posted). |
| P1 | T1.3 assignee via `_resolve_login(value, org=org)`; `""`/None ⇒ unassigned | **Met** | `cases/update.py:101-103`; `cases/create.py:83-85`; `test_api_case_create_accepts_full_field_set` (bound), `test_api_case_create_bare_title_defaults` (None). |
| P1 | T1.3b store `resolve_case_status` warnings on `ingestion_warnings`, return them | **Met** (warning path untested) | `cases/create.py:118-131,152`; only the empty case is asserted (`ingestion_warnings == {}`). No test drives an auto-created status through `create_case`. |
| P1 | T1.4 point API `_create_case` at service; keep 201/400/403 | **Met** | `cases/views.py:297-319`; `test_api_case_create_*`. |
| P1 | AC1.1 `create_case` returns case+warnings; number allocated; `owner_org is org` | **Met** | `cases/create.py:119-134,152`; `test_ui_case_create_post_title_only_redirects_to_detail` (number=1, owner_org). |
| P1 | P1-a full field set → 201, values/tags/assignee applied | **Met** | `test_api_case_create_accepts_full_field_set`. |
| P1 | P1-b blank title → 400 exact `{type,message,fields}` | **Met** | `test_api_case_create_blank_title_400`. |
| P1 | P1-c org=None → 403 unchanged | **Met** | `test_api_case_create_orgless_403` (two-key body, no `fields`). |
| P1 | P1-d `severity: 9` → 400 not 500, no row | **Met** | `test_api_case_create_severity_nine_400`. |
| P1 | P1-e unknown field → 201 | **Met** | `test_api_case_create_unknown_field_still_201`; `test_unknown_fields.py` green. |
| P1 | P1-f (superseded W102-2/3) bare title defaults | **Met** | `test_api_case_create_bare_title_defaults`. |
| P2 | T2.1 `cases/new` above `cases/<str:case_id>`; `@not_viewer @login_required @require_http_methods` | **Met** | `ui/urls.py:21-23`; `ui/views.py:463-466`; `test_ui_case_create_route_order`. |
| P2 | T2.2 `ui/case_create.html` + `_render_case_create_form` (scalar echo, N1) | **Met** | `ui/views.py:561-611` (`.get()`, never `dict(QueryDict)`), `ui/templates/ui/case_create.html`. |
| P2 | T2.3 `New case` button + empty-state CTA under `can_mutate` | **Met** | `ui/templates/ui/case_list.html:13-15,44`; `can_mutate` = `not is_viewer` (`ui/views.py:236`). |
| P2 | T2.4 `AuditLog` `case.created`/`target_type="Case"`/`target_id`/`target_org` **in the same transaction** | **Met** | `ui/views.py:539-549` (`transaction.atomic()` wraps `create_case` + `AuditLog.objects.create`); `test_ui_case_create_post_title_only_redirects_to_detail` asserts `target_org == analyst.org`, `target_id == str(case.id)`. |
| P2 | T2.5 flash service warnings | **Met** (no test) | `ui/views.py:554-555`. No assertion exercises the warning branch. |
| P2 | P2-a analyst GET → 200, controls + labels, title required, CSRF | **Partially met** | `test_ui_case_create_get_returns_form` asserts CSRF + 10 `name="…"` attributes + `required`. It does **not** assert a `<label for>` per control, `maxlength="500"`, or the submit button; the plan says "eleven controls" but extraction's checkbox was dropped (now 10). |
| P2 | P2-b valid POST → 302 detail; case/owner_org/timeline/audit | **Met** | `test_ui_case_create_post_title_only_redirects_to_detail`. |
| P2 | P2-c blank title → 200 re-render, error, no row, typed values echoed | **Partially met** | `test_ui_case_create_post_empty_title_rerenders` / `_blank_title_rerenders` assert 200/error/no row. The "echoed (not `['x']`)" half (the N1 lesson) is **not** asserted. |
| P2 | P2-d unknown status / `severity=99` / `tlp="abc"` → 200, no row, no new `CaseStatus`, no 500 | **Partially met** | `test_ui_case_create_post_unknown_status_rerenders` (asserts no new `CaseStatus`), `..._severity_zero_rerenders`, `..._tlp_nine_rerenders`. `severity=99` and `tlp="abc"` are **not** posted by any test. |
| P2 | P2-e cross-org assignee (login + UUID) → 200, no case, no binding | **Met** | `test_ui_case_create_post_cross_org_assignee_rerenders` (+ `_uuid_`). |
| P2 | P2-f viewer POST → 404; `New case` hidden from case list | **Route: Met; button: Met by inspection, untested** | `test_ui_case_create_viewer_post_404` + the enumeration sweep in `test_viewer_readonly_ui.py` (viewer POST to every `ui-*` route ∈ {404,405}). Button hidden by `{% if can_mutate %}` (`case_list.html:13`); **no test asserts the viewer case-list HTML lacks the button**. |
| P2 | P2-g unauthenticated GET → login redirect | **Met by inspection, untested** | `not_viewer` is outermost; `is_viewer(anonymous) is False` (`identity/scoping.py:is_viewer`, deny-only), so `login_required` redirects. **No test** in `test_ui_case_create.py`. |
| P2 | P2-i (superseded W102-2) extraction on links `Observable` + dispatches `observable.created` | **Met** | `test_ui_case_create_post_with_extractable_artifact_creates_observable` (link); dispatch by code `observables/extractor.py:329-333` → `automation/registry.py:155-166`. |
| P2 | P2-j route does not shadow case detail | **Met** | `test_ui_case_create_route_order` (`resolve("/cases/new").url_name == "ui-case-create"`; `/cases/7` → `ui-case-detail`). |
| P2 | P2-k `test_ui_urls_smoke.py` green | **Met** | Suite green (110 passed includes it); `ui-case-create` is template-referenced so it is both name-checked and GET-rendered. |
| P3 | T3.1 `n` in `CHEATSHEET`, `ROUTES`, rail link, cheat-sheet row | **Met** | `ui/static/ui/keys.js:22,39`; `ui/templates/ui/base.html:55-60,168`. |
| P3 | P3-a `test_ui_nav.py` passes unmodified | **Met** | Suite green; `test_ui_nav.py` is **not** in `git status` (unmodified). |
| P3 | P3-b `n` not bound while typing | **Met by inspection** | `keys.js:50-61` (`isTyping`: TEXTAREA/SELECT/text-ish INPUT → true); no dedicated JS test (plan accepts inspection). |
| P4 | T4.1 `ui-case-create` added to the read-sweep `list_routes` | **Met** | `test_tenant_scoping.py:1551`; not in `_UI_GLOBAL_READ_ROUTES`/`_UI_WRITE_ONLY_ROUTES`. |
| P4 | P4-a/P3-b own-org, cross-org and superAdmin GET all 200 | **Met** | `test_tenant_scoping.py:1533` (list branch → cross-org 200, own-org 200) and `:1603` (superAdmin 200); suite green. |
| P4 | T4.2 `ui-case-create` in `_MUTATION_ROUTE_SAMPLES` | **Met** | `test_viewer_readonly_ui.py:40`. |
| P4 | T4.3 `test_ui_case_create.py` added (P1-a…f, P2-a…k, P4-b…d) | **Met** (25 cases) | File present. |
| P4 | P4-b POST without CSRF → 403, no row | **Met** | `test_ui_case_create_post_requires_csrf`. |
| P4 | P4-c two POSTs → distinct `number`s | **Met** | `test_ui_case_create_two_posts_two_distinct_numbers`. |
| P4 | P4-d 600-char title → truncated to 500, no raise | **Met** | `test_ui_case_create_title_truncated_to_500`; `cases/create.py:125`. |
| P4 | T4.4 `docs/spec/api.md` accepted create keys row | **Met** | `git diff HEAD -- docs/spec/api.md` (accepted-keys paragraph + a new UI route table). |
| P4b | T4.5 verifier report | **In progress** | This document. |
| P4b | T4.5b security review `docs/reviews/REVIEW-2026-10-13-ui-case-create.md` | **Not Met** | File absent (`ls docs/reviews`). |
| P5 | T5.1 `docs/spec/deviations.md` Wave 10.2 | **Met** | `deviations.md:294-305` (W102-1…W102-5). |
| P5 | T5.2 close `TODO.md` §10.2 + point §12.1 at the case list | **Not Met** | `TODO.md:267` still says "there is **no** `ui-case-create` route"; `TODO.md:36` still lists §10.2 as open. No diff to those lines. |
| P5 | T5.3 `COMPLETED.md` dated entry with `make check` counts | **Not Met** | `git diff HEAD -- COMPLETED.md` adds only the 2026-10-12 viewer entry; no 2026-10-13 case-create entry. |
| P5 | T5.4 cross-references updated if the shortcut landed | **Not Met** | Depends on T5.2; not done. |

### §13 — implementation-level summary

1. **`make check` green** — **Met**: 1646 passed / 3 skipped; ruff/format/mypy(106)/css/migrations green.
2. **Analyst creates from `/cases/new` with the full field set and lands on detail with a "Case created" entry** — **Met at the code level; the UI full-field *success* path is untested** (only title-only, tags and description are posted successfully; `severity`/`tlp`/`pap`/`status`/`assignee`/`flag` are exercised through the API P1-a and the UI *error* paths only).
3. **Viewer cannot reach the route or see the affordance** — **Met** (404 GET+POST tested; button hidden by `can_mutate`, untested).
4. **API accepts the same field set via the same service, no regression** — **Met** (`test_thehive_fixtures`, `test_unknown_fields`, P1-a…f green).
5. **Cross-tenant assignee refused; `owner_org` = actor org; audit row in-transaction `case.created`** — **Met**.
6. **`test_tenant_scoping`/`test_viewer_readonly_ui`/`test_ui_nav`/`test_ui_urls_smoke` extended (never loosened), green** — **Met**.
7. **Verification + security review written; every deviation recorded** — **Partially met**: deviations recorded, verification = this doc, **security review absent**.

## Adversarial checks (explicit findings)

1. **`n` reachable and consistent — confirmed.** `ROUTES.n = "/cases/new"` (`keys.js:39`), `CHEATSHEET` row
   (`keys.js:22`), rail `<a href="{% url 'ui-case-create' %}">… <span class="k">n</span> New case</a>`
   (`base.html:56-59`), server cheat-sheet `<kbd>n</kbd>` (`base.html:168`). `test_ui_nav.py` (unmodified)
   asserts rail keys ≡ `ROUTES` keys, cheat-sheet ≡ bound keys, and that the rendered dashboard contains
   `<kbd>n</kbd>`; all green.
2. **Viewer 404 + router order — confirmed.** `cases/new` is declared before `cases/<str:case_id>`
   (`ui/urls.py:22-23`), so `/cases/new` cannot be swallowed. `@not_viewer` gives a uniform 404 for a
   viewer on both GET and POST before parsing (`ui/views.py:108-138, 463-466`), proven by
   `test_ui_case_create_viewer_get_404`/`_post_404` and by the route-enumeration sweep.
3. **Audit in the create transaction — confirmed.** `ui/views.py:539-549`: the `AuditLog` insert shares the
   `transaction.atomic()` that wraps `create_case` (itself atomic, nested), with `target_type="Case"`,
   `target_id=str(case.id)` and `target_org=case.owner_org` (== `org`). **Note:** the *API* `_create_case`
   does **not** write an `AuditLog` row — pre-existing (confirmed by `git diff HEAD -- cases/views.py`,
   where the old inline create also audited nothing) and outside the plan's UI-scoped T2.4, but it means
   §13.5's "audit row exists" holds only for the UI surface.
4. **Tenant-scoping classification correct — confirmed.** `ui-case-create` is in the `list_routes` branch
   (`test_tenant_scoping.py:1547-1556`), so `test_cross_org_ui_read_returns_404` asserts **cross-org GET 200**
   (an org-independent create form) and **own-org GET 200**, and `test_superadmin_cross_org_ui_read_succeeds`
   asserts **superAdmin 200**. It is deliberately not in `_UI_GLOBAL_READ_ROUTES`/`_UI_WRITE_ONLY_ROUTES`.
5. **API contract intact — confirmed.** 400 `{type,message,fields}` (`test_api_case_create_blank_title_400`),
   403 two-key body (`test_api_case_create_orgless_403`), 201 `case_json(detail=True)`
   (`test_api_case_create_accepts_full_field_set` + `test_unknown_fields.py`), and the tolerance probe
   (`nonsense: 1` → 201).
6. **Mass-assignment — safe by construction; the plan's claim of a pinning test is unmet.** The service reads
   only `CASE_SCALAR_FIELDS` + `status`/`assignee`/tags and always sets `owner_org=org`
   (`cases/create.py:119-135`); `number`/`owner_org`/`closed_date`/`end_date` cannot reach the row. BUT plan
   §9 states field injection is "Pinned by a test that posts those keys and asserts they are ignored" — **no
   such test exists** (grep of `test_ui_case_create.py`/`test_unknown_fields.py`). The generic
   `test_unknown_fields.py` probe provides adjacent (not equivalent) coverage.
7. **Extraction dispatch — confirmed.** `extract_into_case` calls `dispatch_observable_linked`
   (`observables/extractor.py:329-333`), which fires `ObservableCreated` (`automation/registry.py:155-166`),
   so the Module D loop fires from a manual create (W102-4 rationale holds).

## Defects / gaps found

None of these is a security or correctness break in shipped behaviour; they are incomplete deliverables and
untested sub-criteria.

- **P4b T4.5b — security review missing.** `docs/reviews/REVIEW-2026-10-13-ui-case-create.md` does not exist,
  though plan §13.7 and T4.5b require it before "done". The plan's own §4b mandates it.
- **P5 T5.2/T5.3/T5.4 — documentation bookkeeping not done.** `TODO.md:267` still asserts there is *no*
  `ui-case-create` route (now false) and §10.2 is still an open checkbox; `COMPLETED.md` has no dated
  entry for this wave. Suggested fix: strike §10.2 with a pointer to this plan/verification, and add the
  `COMPLETED.md` entry with the observed `1646 passed / 3 skipped`.
- **§9 mass-assignment pin test missing.** Suggested fix: one test posting `number`/`owner_org`/`closed_date`/
  `end_date` alongside a valid title to `POST /api/v1/case` and `POST /cases/new`, asserting each is ignored
  (`owner_org == actor.org`, `number` server-allocated).
- **P2-f button half untested.** Suggested fix: add a viewer-vs-analyst assertion on `ui-case-list` HTML (or
  extend `test_viewer_affordances.py`) that the `New case` link is present for an analyst and absent for a
  viewer.
- **P2-g untested.** Suggested fix: an anonymous `Client().get("/cases/new")` asserting a 302 to `login`.
- **P2-a/P2-c partial.** Labels-per-control + `maxlength`/submit, and the scalar-echo-on-re-render (N1)
  are unasserted.
- **T1.2 non-integer branch untested.** `severity="x"`/`tlp="abc"` never posted; the `except (TypeError,
  ValueError)` path in `_set_case_fields` is uncovered.
- **§13.2 UI full-field success path untested.** Only the API drives `severity`/`tlp`/`pap`/`status`/`assignee`/
  `flag` to a successful row; the UI path is exercised for title/tags/description only.
- **`CaseStatus` / assignee selects capped at `PAGE_SIZE` (200)** (`ui/views.py:494-498, 493`) — pre-existing
  pagination gap (§12.1), not introduced here.

## Blockers

None to the functional MVP loop (ingest → create → extract → automate). The following prevent a clean "done":

- **P4b** — security review not written.
- **P5** — `TODO.md` §10.2 not closed; `COMPLETED.md` entry missing (plan status says "Implemented (P1–P5)"
  but P5 is incomplete).

## Recommendations (non-binding)

1. Commission `docs/reviews/REVIEW-2026-10-13-ui-case-create.md` (T4.5b) — focus: the accepted API keys
   (`tlp`/`pap`/`flag`/`tags`/`assignee`) and mass-assignment, the API-vs-UI audit asymmetry (the API write
   is unaudited), viewer/org=None paths, and audit-in-transaction.
2. Close T5.2/T5.3/T5.4: correct `TODO.md:267` (it now contradicts shipped reality), close §10.2, add the
   `COMPLETED.md` entry.
3. Add the four small tests above (mass-assignment pin, viewer button, anonymous redirect, non-integer enum)
   — each is a few lines and closes an AC that currently relies on inspection.
4. Decide whether API `POST /api/v1/case` should also emit an `AuditLog` row for parity with the UI
   (currently only the UI audits) — record the decision either way.
