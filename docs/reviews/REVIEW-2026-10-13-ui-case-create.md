# Security Review — UI case create (`TODO` §10.2), uncommitted wave

**Date:** 2026-10-13
**Auditor:** security-auditor (read-only on product code; this report is the only artefact written)
**Scope:** the uncommitted "create a case from the UI" surface and the construction logic it shares with
`POST /api/v1/case`:

- `ui/views.py::case_create` + `_render_case_create_form` — the `GET|POST /cases/new` form
  (`@not_viewer @login_required @require_http_methods(["GET","POST"])`).
- `ui/urls.py:21-23` — the new `cases/new` route and its ordering against `cases/<str:case_id>`.
- `cases/create.py::create_case` — the shared service now called by **both** surfaces.
- `cases/update.py::_set_case_fields` — gained the `warnings` out-parameter (still owns the enum
  bounds and the org-scoped assignee lookup).
- `cases/views.py::_create_case` — the refactored API wrapper (400/403/201 shapes).
- `ui/templates/ui/case_create.html`, `case_list.html`, `base.html` (rail + cheat-sheet) and
  `ui/static/ui/keys.js` (`n` shortcut) — escaping, CSP, affordance guards, keyboard safety.

Cross-cut checks against the boundaries this surface leans on: `identity/scoping.py` (`is_viewer`,
`resolve_case`), `identity/permissions.py::ViewerReadOnly`, `core/enums.py` (severity/TLP/PAP/stage
bounds), `alerts/escalation.py::resolve_case_status` (ADR-002 §D11 tolerance),
`observables/extractor.py::extract_into_case` (org scoping), `cases/tagging.py`, `ui/markdown.py` +
`ui_tags.markdown` (ADR-005), `compat/errors.py` (unhandled-exception envelope),
`amalthea/settings/{base,prod}.py` (CSP, secure cookies, fail-closed prod).

**Plan / ADRs:** `docs/planning/PLAN-2026-10-13-ui-case-create.md` (§0 deviations), `docs/spec/deviations.md`
→ Wave 10.2 (`W102-1…W102-5`), ADR-002 §D11, ADR-003, ADR-004, ADR-005, ADR-006.
**Baseline:** working tree vs `a3478d0`; `cases/create.py`, `ui/templates/ui/case_create.html` and the
Wave-10.2 test suite are untracked; `ui/urls.py`, `ui/views.py`, `cases/update.py`, `cases/views.py`,
`core/enums.py`, `ui/templates/ui/{base,case_list}.html`, `ui/static/ui/keys.js` are modified.
**Method:** read every path above plus the shared boundaries; `git show HEAD:cases/views.py` to recover
the pre-wave `_create_case`; **falsification probes** written to the pre-approved temp dir (no repo file
added or modified) driving the Django test client / DRF `APIClient` through `/cases/new` and
`/api/v1/case` — empty/null/out-of-range enums, mass-assignment keys (`owner_org`/`number`/`id`/
`created_at`/`start_date`/`closed_date`), cross-org assignee (login and UUID), viewer GET/POST (incl. a
raw binary body), org-less GET/POST, unknown status (mint attempt), route shadowing, a monkeypatched
failing `AuditLog` insert, and viewer rail/cheat-sheet rendering.
**Gate observed:** `.venv/bin/python -m pytest tests/conformance/test_ui_case_create.py -q` → **25
passed**; the regression set `test_ui_case_create + test_tenant_scoping + test_viewer_readonly_ui +
test_ui_nav + test_ui_inline_assets + test_unknown_fields + test_mvp_loop_automation` → **306 passed**;
`DJANGO_SETTINGS_MODULE=amalthea.settings.prod … manage.py check --deploy` → **no issues**.

---

## Verdict

**0 Critical / 0 High. One Medium, one Low, four Info.**

The guarantees the plan claims hold under adversarial probing: a viewer is denied with a uniform 404
**before any parsing** (confirmed with a raw binary body); `owner_org` is taken from `request.user.org`
and is **not** over-postable; the assignee lookup is org-scoped on both surfaces (a cross-org
login/UUID resolves to *unassigned*, never a foreign principal, and is not an existence oracle); there
is **no mass assignment** (`number`, `id`, `owner_org`, `closed_date`, `start_date` in the payload are
ignored); CSRF is required; extraction is org-scoped to the created case's org; the `case-created`
`AuditLog` row shares the create's transaction (a failed audit rolls the case back — proven by
monkeypatching the insert); and the new template/JS carry no XSS sink and no inline asset.

The one **Medium** is a **behavioural regression** introduced by moving the API create onto the shared
service: an **empty or `null` `status`** on `POST /api/v1/case` now returns **500** where the pre-wave
wrapper defaulted it to `InProgress`. It is authenticated-only and self-inflicted (no data exposure, no
tenant crossing, the surrounding transaction rolls back, the client gets the generic envelope) — but it
contradicts the wave's own W102-3 claim that empty/null tolerance is preserved, and no shipped test
covers it. The **Low** is the Layer-3 affordance gap the plan §7 thought was closed: a viewer is still
shown a `New case` rail link, the `n` shortcut and a cheat-sheet row that the server correctly 404s.

---

## Findings (severity-ranked)

| ID | Sev | Area | File:line | Introduced by this wave? |
|----|-----|------|-----------|--------------------------|
| M1 | Medium | Unhandled 500 / tolerance regression: empty or `null` `status` on `POST /api/v1/case` | `cases/create.py:83-85`; `cases/update.py:96-100`; `alerts/escalation.py:51-58`; `cases/views.py:297-319` | Yes |
| L1 | Low | Layer-3 affordance: viewer is offered `New case` (rail, `n` shortcut, cheat-sheet row) that 404s | `ui/templates/ui/base.html:55-60,168`; `ui/static/ui/keys.js:22,39` | Yes |
| I1 | Info | `Unknown case status:` text is auto-escaped in the flash, not logged — no CWE-117 sink here; the API 500 traceback is the pre-existing sink | `ui/views.py:520`; `compat/errors.py:21` | Partly (new message) / No (sink) |
| I2 | Info | API create writes no `AuditLog` while the UI create does | `cases/views.py:307-319`; `ui/views.py:539-549` | No (unchanged) |
| I3 | Info | `resolve_case_status` tolerance only mints a status whose value equals a valid *stage*; any other unknown value 500s | `alerts/escalation.py:51-58`; `cases/models.py:45-47` | No (pre-existing) |
| I4 | Info | UI create auto-dispatches automation (unconditional extraction → `observable.created`) with no per-action approval | `cases/create.py:142`; `observables/extractor.py:332-333` | By design (W102-2) |

---

### M1 — MEDIUM: empty/`null` `status` on `POST /api/v1/case` is a 500 (was a 201)

**Where.** `cases/create.py:83-85` forwards `status` verbatim whenever the key is present:

```python
for key in ("status", "assignee"):
    if key in payload:
        scalars[key] = payload[key]
```

`cases/update.py:96-100` then resolves it unconditionally:

```python
if "status" in payload:
    case.status = resolve_case_status(str(payload["status"]), warnings=...)
```

`resolve_case_status` (`alerts/escalation.py:51-58`) does
`CaseStatus.objects.get_or_create(value=value, defaults={"stage": value, "order": 1})`. `CaseStatus.stage`
carries `case_status_stage_valid` = `in_values("stage", CASE_STAGES)` (`cases/models.py:45-47`), so
`value=""` or `str(None) == "None"` attempts to write `stage=""`/`stage="None"`, which violates the CHECK
and raises an unhandled `IntegrityError`.

**Why it is a regression, not pre-existing.** The pre-wave `cases/views.py::_create_case`
(`git show HEAD:cases/views.py`) read
`resolve_case_status(str(payload.get("status") or "InProgress"), …)` — the `or` collapsed `""`/`None`
to `InProgress`. This wave removed that branch (the API now delegates to `create_case`, whose comment
at `cases/create.py:18-20` explicitly promises the empty-enum tolerance) and the empty-skip guard in
`_create_scalars` covers only `_CASE_RANGE_FIELDS` (`severity`/`tlp`/`pap`, `cases/create.py:74-77`),
so `status` slipped through.

**Impact.** Any authenticated non-viewer org member (analyst/admin/superAdmin; a viewer is denied
earlier by `ViewerReadOnly`) can POST `{"title":"x","status":""}` or `{"title":"x","status":null}` and
receive `500 {"type":"GenericError","message":"Internal error"}`. No stack trace reaches the client
(`compat/errors.py:21-25`), the transaction rolls back (no partial `Case`/`CaseStatus`/ledger row), and
there is no tenant or privilege consequence. The harm is a broken public-API contract on a field clients
routinely leave empty, plus a contradiction of the recorded W102-3 guarantee. Rated **Medium** — a fresh
regression of working behaviour (the closest precedent, the P2 out-of-range-enum 500, was rated Low
because it was pre-existing).

**Probe (temp dir, nothing added to the repo), test settings/DB:**

```
POST /api/v1/case {"title":"empty status","status":""}   -> 500 {"type":"GenericError"}
POST /api/v1/case {"title":"null status","status":null}  -> 500 {"type":"GenericError"}
POST /api/v1/case {"title":"bare"}                       -> 201
POST /api/v1/case {"title":"sev zero","severity":0}      -> 400 (W102-3, correct)
POST /api/v1/case (owner_org/number/id/created_at/closed_date/start_date injected) -> 201 in caller's org, allocated number
```

**Recommended fix.** Mirror the range-field guard for the status key in `_create_scalars`, e.g.
`if key == "status" and value in (None, ""): continue`, so a missing/empty status leaves the
`InProgress` default `create_case` already set at `cases/create.py:120`. (Fixing it in
`_set_case_fields` instead — treating an empty/`None` status as "no change" — would also repair the same
latent 500 on the PATCH path.) Add a test asserting `{"title":"x","status":""}` and `{"status":null}` →
201 `InProgress`.

---

### L1 — LOW: a viewer is still offered `New case` in the rail, the `n` shortcut, and the cheat-sheet

**Where.** The plan (§7, G4) and ADR-006 Layer 3 promise "a viewer is never *offered* a control the
server will refuse". The new rail entry added for Q2 is unconditional:

```html
<!-- ui/templates/ui/base.html:55-60 -->
<li>
  <a href="{% url 'ui-case-create' %}" …><i class="fa-solid fa-folder-plus" aria-hidden="true"></i>
    <span class="k">n</span> New case</a>
</li>
```

while the equivalent case-list button *is* gated (`ui/templates/ui/case_list.html:13-15`,
`{% if can_mutate %}`). `ui/static/ui/keys.js:22,39` binds `n → /cases/new` for every user, and
`ui/templates/ui/base.html:168` renders the `n` cheat-sheet row for everyone (the `,` settings row, by
contrast, is `{% if request.user.role == "superAdmin" %}`).

**Impact.** A viewer sees the rail link, presses `n`, and lands on the uniform 404. The server boundary
is intact (`not_viewer`, `ui/views.py:463-466` → 404; probe confirms viewer GET and POST are both 404
with no case created), so this is a **dead control / UX inconsistency, not a hole** — the same class the
viewer wave already accepted at Low. Probe: viewer dashboard contains `href="/cases/new"` and
`<kbd>n</kbd>`, and `GET /cases/new` → 404; the case-list *button* is correctly absent.
`tests/conformance/test_viewer_affordances.py` covers case-detail/list controls but not the rail or the
shortcut registry, so this is currently untested.

**Recommended fix.** Wrap the rail `<li>` in `{% if can_mutate %}` (the context value already exists on
every page), drop `n`/the cheat-sheet row for a viewer, and extend `test_viewer_affordances.py` to assert
the rail and `keys.js` are viewer-free — or, if the dead control is deliberately kept, record it as an
accepted-Low next to the viewer wave's `,` disposition.

---

### I1 — Info: the `Unknown case status:` message is rendered, never logged

`ui/views.py:517-521` puts attacker-influenced text into a flash:
`messages.error(request, f"Unknown case status: {status_val}")`. This does **not** reach a log sink:
there is no `LOGGING` configuration under `amalthea/settings/`, and the message is only stored in the
session and rendered by `ui/templates/ui/base.html:139-143` as `{{ message }}` with Django
auto-escaping. A probe posting a status containing a raw newline confirmed the text is escaped and no
log line is emitted. So **no CWE-117 here** — unlike the original `not_viewer` bug the viewer wave
fixed (now `ui/views.py:128-134`, logging only server-controlled `method`/`url_name`/`user_id`).
Related but **pre-existing**: an invalid *non-empty* status sent to the **API** raises an
`IntegrityError` whose traceback is logged with `logger.exception(...)` (`compat/errors.py:21`); on
PostgreSQL a CHECK-violation `DETAIL` can embed the row value, the CWE-117 class the project already
tracks for `django.request` (`TODO §11.5`). Fixing M1 removes the empty case; the non-empty-invalid
case remains a follow-up candidate (log filter / `%r` formatting), not a blocker.

### I2 — Info: the API create is unaudited while the UI create is audited

`ui/views.py:539-549` writes `AuditLog(action="case.created")` inside the create transaction, but
`cases/views.py::_create_case` (`:297-319`) writes no audit row. This matches the pre-wave behaviour
(the old API wrapper also did not audit), so it is **not a regression**; it is noted because the same
service now backs both surfaces, and the asymmetry is visible. Recommend a follow-up to audit API
creates (or to document why UI-only).

### I3 — Info: ADR-002 §D11 status tolerance is narrower than it reads

`resolve_case_status` can only auto-create a `CaseStatus` whose value equals a valid *stage*
(`New`/`InProgress`/`Closed`); the `defaults={"stage": value}` means any other value hits
`case_status_stage_valid` and 500s (pre-existing; probe: `status:"ZzzCustom"` → 500 on the API). W102-3
correctly tightened `severity`/`tlp`/`pap` to a clean 400, but `status` was neither tightened nor
preserved for the empty case (M1). If a fully tolerant status is intended, the resolver should map an
unknown value onto a default stage rather than the value itself.

### I4 — Info: UI create auto-dispatches automation, by design

`cases/create.py:142` unconditionally runs `extract_into_case` over the title and description, and
`observables/extractor.py:332-333` fires `observable.created` for every newly-created link, so playbooks
(which may perform outbound requests) run as a side effect of creating a case, with no per-action
approval. This is the recorded W102-2 behaviour (and the pre-wave API already extracted
unconditionally), the actor is an authenticated write-capable analyst, the dispatch happens inside the
create transaction so rollback/audit integrity is preserved, and automation execution safety
(timeouts, egress controls, action registry) is owned by the automation module, not this surface. No
action required; recorded for the "automation blast radius" check.

---

## What I checked and found sound

- **Viewer denial is first and uniform.** `ui/views.py:463-466` applies `@not_viewer` **outermost**, so a
  viewer is 404'd before `login_required`, before the method check and before any `request.POST` access.
  Probe: viewer `GET /cases/new` → 404, viewer `POST` with a raw binary body → 404, `Case.objects.count()
  == 0`. The API side is covered by the global `ViewerReadOnly`, and `create_case` is never reached by a
  viewer.
- **`owner_org` is never caller-controlled.** `ui/views.py:480` uses `request.user.org`; the service takes
  `org` as an argument and raises `CaseUpdateError("AuthorizationError")` when it is `None`
  (`cases/create.py:111-112`). Probing `POST /api/v1/case` with `owner_org=<other org>` created the case
  in the caller's own org; `owner_org` is not in `CASE_SCALAR_FIELDS` and is never read from the payload.
- **No mass assignment.** Payload keys outside `CASE_SCALAR_FIELDS`
  (`title`,`description`,`summary`,`severity`,`tlp`,`pap`,`flag`) + `status`/`assignee` +
  `tags`/`addTags`/`removeTags` + `extract`/`observables` are ignored. Probe sent `owner_org`, `number`,
  `id`, `closed_date`, `start_date`, `created_at` → 201 with `owner_org == actor.org`, an allocated
  `number`, and none of the injected columns applied. `_apply_case_tags` only reads
  `tags`/`addTags`/`removeTags` (`cases/tagging.py:23-31`).
- **Org-scoped assignee, no cross-tenant binding or oracle.** The UI resolves via
  `_resolve_login(value, org=org)` (`ui/views.py:526`) and re-resolves the resulting UUID in the service
  (`cases/update.py:101-103`); `_resolve_login` filters `org=org` for both the login and UUID lookups
  (`cases/views.py:539-546`). Probe: cross-org login and cross-org UUID both left `assignee=None` —
  unassigned, never bound, and indistinguishable from "unknown login".
- **CSRF.** The form carries `{% csrf_token %}` (`ui/templates/ui/case_create.html:17`), the route is
  `@require_http_methods(["GET","POST"])`, `CsrfViewMiddleware` is active and there is no
  `HttpMethodOverrideMiddleware`. The shipped `test_ui_case_create_post_requires_csrf` asserts a 403 with
  `enforce_csrf_checks=True`.
- **Enum bounds (W102-3), no fail-open.** `_set_case_fields` range-checks `severity`/`tlp`/`pap` against
  `core/enums` before any write; non-integer and out-of-range raise `CaseUpdateError` → 400 / re-render.
  Probe: `severity:0` → 400; shipped tests cover `severity:9`, `tlp:9`. Unknown keys still 201
  (`test_unknown_fields` green). The one gap is `status` (M1/I3).
- **No status minting from the UI.** `ui/views.py:517-522` validates the posted status against the
  existing `CaseStatus.value` vocabulary before calling the tolerant service. Probe: `status:"ZzzCustom"`
  → 200 re-render, no case, `CaseStatus` count unchanged.
- **Transaction integrity.** The UI wraps `create_case` (itself `@transaction.atomic`) and the
  `AuditLog.objects.create` in one `transaction.atomic()` (`ui/views.py:539-549`). Probe: monkeypatching
  `AuditLog.objects.create` to raise left `Case.objects.count() == 0` — a failed audit rolls the create
  back.
- **Extraction is org-scoped.** `extract_into_case` (`observables/extractor.py:298-335`) keys every
  `Observable.get_or_create` on `owner_org=case.owner_org`, so a case created from free text can never
  read or write another org's artifact rows; the `CaseObservable` link is per-case. Probe: extracted
  observables all carried the creating org's id.
- **Route ordering.** `cases/new` is declared above `cases/<str:case_id>` (`ui/urls.py:21-23`). The shipped
  P2-j test and a probe confirm `/cases/new` → `ui-case-create`, `/cases/7` → `ui-case-detail`.
- **XSS / escaping / CSP.** `case_create.html` interpolates `form_values.*` with Django auto-escaping
  (`{{ … }}`), has no inline `<script>`/`style`/`on*` attribute, and the case body is rendered through the
  ADR-005 sanitizer (`case.description|markdown` → `ui/markdown.py`, nh3 allow-list, `mark_safe` only on
  sanitized output). The `case-created` ledger entry takes the non-comment `<pre>{{ event.description }}</pre>`
  branch (`ui/templates/ui/_timeline_entry.html:4-9`), i.e. escaped. CSP is
  `default/script/style/font/connect-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`,
  `form-action 'self'` (`amalthea/settings/base.py:65-78`); `test_ui_inline_assets.py` is green. No
  `innerHTML` sink was added (`keys.js` only assigns `window.location` and calls `showModal()`).
- **Icon-only controls / a11y.** All new rail/actions glyphs are `<i … aria-hidden="true">` beside visible
  text; the new form has a `<label for>` on every control and a visible submit label.
- **Keyboard-shortcut safety.** `keys.js::isTyping` suppresses bindings while a text/textarea/select/
  contenteditable has focus, and modifiers are ignored (`keys.js:50-75`); `n` is a plain single key, so it
  cannot fire mid-typing (P3-b).
- **SQL injection.** The reviewed paths use the ORM exclusively
  (`User.objects.filter(login=str(value))`, `CaseStatus.objects.filter(value=…)`), no raw SQL, no string
  interpolation into queries.
- **Prod fail-closed posture untouched.** `manage.py check --deploy` is clean; `prod.py` still refuses a
  weak `DJANGO_SECRET_KEY`/empty `DJANGO_ALLOWED_HOSTS` and forces secure cookies, HSTS, SSL redirect and
  `nosniff`. No secret was introduced by this wave.
- **Regression suites green.** 306 passed across the create + tenant-scoping + viewer + nav + inline-asset
  + unknown-fields + MVP-loop suites; the create suite alone is 25 passed.

---

## Guarantees I could not fully confirm

- **PostgreSQL-specific failure modes.** All probes ran on the test settings (SQLite), where CHECK
  constraints *are* enforced, so M1's 500 is real and reproduced. The precise PostgreSQL `IntegrityError`
  message and whether its `DETAIL` embeds the crafted `status` value (the I1 CWE-117 amplification) were
  **not** reproduced on a live Postgres instance — that part is inferred from the constraint definition
  and `logger.exception` in `compat/errors.py`.
- **Full CI gate.** I ran the targeted pytest suites and `check --deploy`, not the complete `make check`
  (ruff/format/mypy/migration/css-check), which was out of scope for a security review of this surface.

---

## Sign-off

**0 Critical / 0 High.** The multi-tenant, authorization, over-posting, CSRF, transaction-integrity and
XSS/CSP guarantees the plan claims are confirmed by inspection and adversarial probing. Block on nothing;
fix **M1** (empty/`null` status 500) with or before the next commit, since it regresses a documented API
tolerance and is a trivial guard; decide **L1** (viewer rail/shortcut affordance) as either a one-line
`{% if can_mutate %}` fix or an accepted-Low recorded next to the viewer wave's disposition;
**I1–I4** are notes for the record. No application code or test was modified by this review.
