# Security Review — Viewer role enforcement (ADR-006), uncommitted wave

**Date:** 2026-10-12
**Auditor:** security-auditor (read-only on product code; this report is the only artefact written)
**Scope:** the uncommitted ADR-006 "viewer role enforcement" wave — the rename `read-only` →
`viewer` and the three enforcement layers plus its migration:
- Layer 1 — `identity/scoping.py` (`is_viewer`, the `write=True` viewer denial in `resolve_case` /
  `resolve_alert` / `resolve_observable`).
- Layer 2 — `identity/permissions.py::ViewerReadOnly` registered in
  `amalthea/settings/base.py::REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]`.
- Layer 3 — `ui/views.py::not_viewer` applied to mutating UI views, `_base_context` `can_mutate`,
  and the `ui/templates/ui/*.html` affordance guards.
- Rename — `core/enums.py` (`VIEWER_ROLE`, `USER_ROLE_CHOICES`), `identity/models.py` role field +
  CHECK, `identity/migrations/0008_role_viewer_rename.py`, and the `docs/spec/*` updates.

Cross-cut checks against the untouched-and-relevant boundaries: `compat/auth.py`
(`ApiKeyAuthentication` / `ScopePermission`), `compat/views.py` (login/logout `AllowAny`),
`ingest/views.py` (non-DRF webhook), `realtime/consumers.py`, `amalthea/urls.py` (`admin/` mount),
and the share grant/revoke surfaces in `cases/views.py` + `cases/urls.py`.
**Plan / ADRs:** `docs/planning/PLAN-2026-10-12-viewer-role-enforcement.md`,
`docs/decisions/ADR-006-role-semantics.md`.
**Baseline:** working tree vs `a3478d0` (18 tracked files changed, +302/−68; 7 untracked files:
the ADR, the plan, the verification record, the migration, and three conformance suites + one unit
suite, 1168 lines total). Everything reviewed is uncommitted on `main`.
**Method:** `git diff` of every changed/added file; traced `is_viewer` from every entry point
(DRF `@api_view`, plain UI views, Django admin, WebSocket consumers, the ingest webhook); read the
`api_view`/`permission_classes` resolution to confirm the global class really applies; adversarial
reading of the migration ordering and its reverse; catalogued every surviving `read-only` literal to
prove none is a role gate; enumerated the URLconf for mutating routes with `<str:…>` converters to
test the log path; confirmed `MIDDLEWARE` has no `HttpMethodOverrideMiddleware`; confirmed the prod
fail-closed settings, CSP and secret hygiene; ran the shipped suites.
**Gate observed:** `.venv/bin/python -m pytest tests/test_roles.py
tests/conformance/test_viewer_readonly_ui.py tests/conformance/test_viewer_readonly_api.py
tests/conformance/test_viewer_affordances.py tests/conformance/test_playbook_authoring.py
tests/conformance/test_ui_p1_wiring.py tests/conformance/test_playbook_roundtrip.py -q` →
**110 passed**.
`scripts/secret-scan.sh --files` over all 25 changed/untracked paths → **clean (25/25)**.

---

## Verdict

**CLEAR FOR SIGN-OFF WITH THREE LOW-SEVERITY FOLLOW-UPS AND FOUR INFO NOTES.**

No Critical and no High finding. The change does what ADR-006 claims for the threat it targets: a
`viewer` cannot mutate through any DRF endpoint, cannot mutate through any `ui-` write view, and
cannot escalate that by holding a `write`-permission *share* (role beats share). The enforcement is
correctly composed and fail-closed where it matters most — Layer 2 is a global DRF permission class,
so present **and future** API endpoints are covered without being touched, and it is deny-only so it
cannot accidentally widen access. Anonymous callers still get a 401 (not a 403), read-scoped API
keys are still handled by `ScopePermission`, a viewer's own `readwrite` key is still refused
(role ∧ scope), and the non-DRF ingest webhook, health checks and WebSocket consumers are untouched.

The residual risk is small and non-exploitable on its own: one reachable **log-injection**
(CWE-117) in the new denial logger, a **Django-admin** path that sits outside the "every endpoint"
claim, and the fact that the **UI layer is opt-in per view** rather than globally fail-closed like
the API layer. None is cross-tenant, XSS, CSRF, injection, privilege-escalation-by-itself, or secret
exposure.

---

## Findings (severity-ranked)

| ID | Sev | Area | File:line | Introduced by this wave? |
|----|-----|------|-----------|--------------------------|
| L1 | LOW | Log injection (CWE-117): `request.path` logged unescaped on every viewer denial | `ui/views.py:123-125` | Yes |
| L2 | LOW | Django admin sits outside the "viewer is read-only on every endpoint" guarantee | `amalthea/urls.py:14`; `identity/admin.py`; `identity/models.py:44-61` | No — newly *relevant* (the claim is new) |
| L3 | LOW | UI layer is opt-in per view, not a runtime global: drift is caught only by a test sweep | `ui/views.py:106-129, 227`; `tests/conformance/test_viewer_readonly_ui.py` | Yes (design) |
| I1 | Info | Layer-1 `write=True` applied for cases but not at every alert/observable mutation call site | `cases/views.py:938,1297,1379`; `alerts/views.py:69,102,416,450,646` | Partially (cases done, alerts/obs. not) |
| I2 | Info | Denial shape differs: UI 404 vs API 403, contrary to ADR-006's stated uniform 404 | `ui/views.py:126`; `identity/permissions.py:74-97` | Yes |
| I3 | Info | No throttle specific to viewer denials; log-flood amplifier for L1 | `identity/permissions.py:92-97`; `amalthea/settings/base.py:166-173` | Yes |
| I4 | Info | URLconf sweeps cover only `ui-`/`api/`-named routes; non-DRF Django views and `compat` catch-alls are out by design | `tests/conformance/test_viewer_readonly_{ui,api}.py` | Yes (test) |

---

### L1 — LOW: unescaped `request.path` in the viewer-denial log (CWE-117)

**Where.** `ui/views.py:106-129`:

```python
def not_viewer(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_viewer(request.user):
            logger.warning(
                "viewer denied at %s", request.path, extra={"user_id": str(request.user.pk)}
            )
            return render(request, "ui/404.html", status=404)
        return view(request, *args, **kwargs)

    return wrapper
```

**Why it is reachable.** `request.path` is derived from the WSGI/ASGI `PATH_INFO`, which the server
has already **percent-decoded** before Django sees it. A control character in the raw request line
can therefore survive into `request.path`: `GET /ui/cases/x%0AInjected/status` arrives as
`/ui/cases/x\nInjected/status`. That string still matches the `<str:case_id>` segment of the
`ui-case-status` route (`ui/urls.py:22`) — `<str:>` matches any non-`/` character — so the route
resolves and `not_viewer` runs. Because `not_viewer` is the **outermost** decorator it fires before
any method check, so a viewer only needs a `GET`; no valid object, no body, and no CSRF token are
required. I confirmed the route pattern and that `MIDDLEWARE` has no `HttpMethodOverrideMiddleware`
that could otherwise complicate the verb.

**Impact.** The attacker controls a newline inside a line written to the application log. Any
handler that emits the record's message verbatim — syslog, a container log collector, a SIEM
ingestor — can be made to accept a **forged log line** (spoofed timestamp, user, or "success"
message). There is no code execution, no data read, and no authorization bypass; the harm is log
integrity and the false trail it creates. It is rated LOW, but note the irony that a security
platform's own audit trail is the target, and that the venue amplifies trust in the forged line.
The `extra={"user_id": …}` is a UUID and is not attacker-controlled; only `request.path` is.

**Fix.** Do not log the raw path. Either format it with a safe repr, or log something the attacker
cannot forge:

```python
logger.warning(
    "viewer denied at %r",
    request.path,  # repr escapes \r\n
    extra={"user_id": str(request.user.pk)},
)
```

or drop the path entirely and log a stable, non-attacker-controlled identifier
(`resolve(request.path_info).url_name`, plus `user_id`). If the path is wanted for triage, strip
`\r`/`\n` (and other C0 controls) first. `compat.errors` / any other new logger should follow the
same rule.

---

### L2 — LOW: Django admin is outside the "every endpoint" guarantee

**Where.** `/admin/` is mounted at `amalthea/urls.py:14`. The viewer guarantee is enforced in three
places, and **none of them is the admin**: Layer 2 is a DRF permission class (admin is not DRF),
Layer 3 is the `ui-` HTML views (admin is not `ui-`), and Layer 1 is only reached from the
application views that call `resolve_case` / `resolve_alert` / `resolve_observable`. The `role` field
(`identity/models.py:44-61`) is independent of Django's `is_staff` / `is_superuser` / model
permissions.

**Why it matters.** ADR-006's headline claim is that the viewer role is read-only on *every*
endpoint, present and future. A `viewer` who also has `is_staff=True` and any model permission can
still POST/PATCH/DELETE through `/admin/` — including, via `identity/admin.py`, editing users,
roles and org membership. The plan lists admin hardening as a non-goal, and in a correct deployment
a viewer is never staff, so this is a LOW/config-dependent gap rather than a live vulnerability —
but the claim as written is broader than the enforcement, and "viewer is also staff" is exactly the
kind of operator shortcut that a security product should not depend on operators getting right.

**Fix (pick one, in order of preference).**
1. Make it structurally impossible: e.g. give the `User` model (or an admin mixin) a
   `has_module_perms` / `has_perm` that returns `False` for `VIEWER_ROLE`, or refuse to add a viewer
   to `is_staff` in `identity/services.py` (mirroring the existing "cannot demote the last
   superAdmin" guard).
2. Or assert it at startup: an `AppConfig.ready()` check that flips a configured "viewer may not be
   staff" invariant into a hard failure.
3. Or, at minimum, narrow the ADR/spec wording from "every endpoint" to "every application endpoint"
   and document that `is_staff` is an operator-only, out-of-band escape hatch.

---

### L3 — LOW: the UI enforcement layer is opt-in per view, not a runtime global

**Where.** `ui/views.py:106-129` (`not_viewer`, applied per view) and `ui/views.py:227`
(`can_mutate`). Layer 2 is genuinely fail-closed for the API because it is registered once in
`DEFAULT_PERMISSION_CLASSES`; the UI equivalent is not — it depends on each new mutating view
carrying the decorator.

**What currently holds it up.** The invariant test
`tests/conformance/test_viewer_readonly_ui.py` is strong: it enumerates the URLconf, substitutes
converters, and brute-forces POST/PATCH/PUT/DELETE as a viewer over every `ui-`-named route,
requiring a non-2xx/3xx (404/405) response, with a `_MUTATION_ROUTE_SAMPLES` assertion so the
enumeration cannot silently shrink and a vacuity guard proving an undecorated view genuinely 302s.

**The residual gap.** The guarantee lives in the test suite, not at runtime. A future mutating `ui-`
view added **without** `not_viewer` that happens to answer 404/405 to a dummy body (e.g. it 404s on
a missing object before validating the body) would pass the sweep while remaining reachable by a
real viewer POST that names a real object. That is a narrow, drift-dependent hole, and no current
view has it — hence LOW — but it is the one place where the API and UI layers have different
assurance.

**Fix.** Give the UI the same global, fail-closed shape as the API. Cheapest: an
`AppConfig.ready()` assertion (or a small `process_view` middleware) that walks the URLconf and
raises if any view bound to an unsafe HTTP method on a `ui-` route lacks `not_viewer`. That converts
the test-time invariant into a runtime one and makes the two layers symmetric.

---

### I1 — Info: Layer-1 `write=True` is applied for cases, not uniformly for alerts/observables

The UI passes `write=True` consistently (e.g. `ui/views.py:392,502,1092,1468,1697,1855,1893,2015,
2047,2087`), and case mutations go through `_case_access(..., write=True)` / `resolve_case(...,
write=True)`. However, mutating endpoints reached through alerts and observables frequently call
`resolve_alert` / `resolve_observable` **without** `write=True`
(`cases/views.py:938,1297,1379`; `alerts/views.py:69,102,416,450,646` and others). This is **not
exploitable**: every one of those endpoints is a DRF `@api_view`, so the global `ViewerReadOnly`
(Layer 2) denies the unsafe verb first, and the shared `_resolve_observable_scoped` /
`_resolve_alert_scoped` helpers also sit behind it. It is a defence-in-depth/consistency note
against ADR-006's "L1 denies even if L2 is absent" intent: the belt is on for cases and off for
alerts/observables. Consider passing `write=True` at those mutation call sites so Layer 1 is
uniform.

### I2 — Info: UI 404 vs API 403 for a viewer denial

`not_viewer` returns the uniform 404 (`ui/views.py:126`); `ViewerReadOnly` lets DRF return a 403 with
`message = "Your role is read-only."` (`identity/permissions.py:74-97`). ADR-006 describes a uniform
404. This is **not** an existence oracle: a viewer receives 403 for every unsafe verb regardless of
whether the target exists or is owned, and a non-viewer's 404 comes from the independent scoping
rule — the two responses never vary with object existence for the *same* caller. It is a
consistency/documentation item only. If exact uniformity is wanted, have the API return 404 in the
viewer case too, or soften the ADR wording.

### I3 — Info: no throttle dedicated to viewer denials (amplifier for L1)

`not_viewer` writes one log line per denied request and there is no viewer-specific throttle; the
global DRF `user` rate is 1000/min (`amalthea/settings/base.py:170-173`). A viewer can therefore
generate up to ~1000 denied writes/minute, each a potential forged log line for L1. Fixing L1
removes the security consequence; a separate low ceiling would additionally bound the noise.

### I4 — Info: sweep coverage boundaries

`test_viewer_readonly_api.py` enumerates only patterns whose full path starts with `api/`, and
`test_viewer_readonly_ui.py` only names starting with `ui-` (plus the allow-listed auth routes). The
ingest webhook, `healthz`/`readyz`, the `compat/` catch-all and `describe` routes, Django admin, and
any future non-`ui-` HTML route are outside the sweeps — by design for the non-DRF webhook/health
checks, but worth stating explicitly so a future HTML surface is not assumed covered.

---

## What I checked and found sound

- **The global DRF class really applies.** `@api_view` resolves
  `permission_classes` from `APIView.permission_classes`, which is
  `api_settings.DEFAULT_PERMISSION_CLASSES` evaluated at decoration time — after settings load — so
  `ViewerReadOnly` is active on every `@api_view` endpoint. `api_view` also sees attributes set by
  decorators *below* it, and the only endpoints that override `permission_classes` are
  `compat/views.py` login/logout (`AllowAny`), which is intended.
- **Anonymous stays a 401.** DRF's list is `IsAuthenticated, ScopePermission, ViewerReadOnly`.
  `ViewerReadOnly` passes anonymous through, and `IsAuthenticated` yields 401 (an authenticator
  provides a `WWW-Authenticate` header) — it is not turned into a 403 by the new class.
- **API keys.** `ApiKeyAuthentication` returns `(key.user, key)`, so a viewer's own key is a viewer
  and is denied (`test_viewer_readonly_api.py::test_api_key_belonging_to_a_viewer_is_denied_writes`
  pins this: a `readwrite` key on a viewer still 403s). A read-scoped key is denied unsafe verbs by
  `ScopePermission` independently. A non-`User` identity is never a viewer (`is_viewer` is
  deny-only), so machine paths are not broken.
- **Role beats share.** `resolve_case` returns `None` for a viewer when `write=True`
  (`identity/scoping.py:143-144`) **before** the owner-org and share branches (`:146-163`), so a
  viewer who holds a `write`-permission share still cannot mutate. Share **grant/revoke** is closed
  on both surfaces: `ui-case-share-list` / `ui-case-share-revoke` carry `@not_viewer`, and the API
  `case-share-list` / `case-share-detail` are DRF endpoints covered by `ViewerReadOnly` and
  `_case_access(write=True)`.
- **The rename cannot fail open.** Every surviving `read-only` literal was catalogued: they are
  comments/docstrings, the historical migration `0004`, the *share* ACL profileName in
  `core/serializers.py:415` (deliberately untouched — a share ACL, not a role — per the migration
  docstring), and the 403 message string. No code denies by `role == "read-only"` any more; all role
  denials are allow-list (`role not in ("superAdmin","admin","analyst")`) or `role == VIEWER_ROLE`,
  so a missed rename would *not* silently admit a viewer.
- **Enum / model / constraint are in step.** `USER_ROLE_CHOICES` includes `("viewer", "Viewer")`
  (`core/enums.py:117-122`), `USER_ROLE_VALUES` derives from it, and `identity/models.py:61` builds
  the CHECK from `USER_ROLE_VALUES`. `DEFAULT_USER_ROLE` remains `"analyst"`.
- **The migration is correct and reversible.** `RemoveConstraint → RunPython → AlterField →
  AddConstraint` (`identity/migrations/0008_role_viewer_rename.py:31-57`) is atomic on PostgreSQL, so
  rows are only rewritten while the old CHECK is absent. I walked the reverse: it drops the new
  CHECK, restores the old field, rewrites `viewer → read-only`, then re-adds the **old** CHECK that
  permits `read-only` — no window in which a stored value violates the live constraint.
- **No method-override bypass.** `MIDDLEWARE` contains no `HttpMethodOverrideMiddleware`; the
  `permissions.SAFE_METHODS` check in `ViewerReadOnly` and the verb the UI routes dispatch on are the
  real verbs.
- **CSRF posture unchanged.** `CsrfViewMiddleware` is active; no new `csrf_exempt` was added (only
  the pre-existing login/logout in `compat/views.py` and the token-authenticated ingest webhook).
  The viewer gate runs before any parsing, so a viewer cannot reach a form or validation path
  (`test_viewer_readonly_ui.py::test_viewer_is_denied_before_parsing` pins this with a NUL-laden
  body).
- **No new XSS sink.** The denial renders the static `ui/404.html`; the template changes only wrap
  existing affordances in `{% if can_mutate %}` and introduce no new interpolation into attributes
  or script. CSP remains `default-src/script-src/style-src 'self'` with no inline assets.
- **Webhook, health checks, WebSockets untouched.** The ingest webhook is a plain Django view with
  its own token auth, body-size/depth caps and rate limits (`amalthea/settings/base.py:182-189`);
  `ViewerReadOnly` never sees it. `healthz`/`readyz` are read-only. The Channels consumers use the
  read-only sync protocol.
- **Secret hygiene / logging.** `--files` secret scan is clean over all 25 changed/untracked paths.
  No new `LOGGING` config was added and the only new log line records a UUID (`user_id`) plus the
  path — no password, token, or other PII (the path exposure is L1).
- **Prod fail-closed settings intact.** `amalthea/settings/prod.py` still refuses a weak
  `DJANGO_SECRET_KEY` and an empty `DJANGO_ALLOWED_HOSTS`, and keeps HSTS, secure/HttpOnly cookies,
  `SECURE_SSL_REDIRECT` and `nosniff`.
- **Regression suites green.** 110 passed across the new role suites and the modified
  playbook/UI-wiring suites; the affordance guards show/hide buttons for a viewer without acting as
  the security boundary (the server answers 404 first, per the `can_mutate` comment).

---

## Sign-off

Approve the wave. Track L1 (log-injection fix) as the one item I would like fixed before or with the
next commit; L2 and L3 are worth a decision (fix or narrow the claim) but do not block. I1–I4 are
notes for the record.

---

## Round 2 — 2026-10-12

**Re-review scope:** the changes landed in answer to Round 1 — `ui/views.py::not_viewer` (L1),
`cases/views.py::_as_uuid`, the viewer deny in `identity/scoping.py::resolve_{case,alert,observable}`
(V6b), the three new suites (`test_viewer_reads.py`, `test_viewer_precedence.py`,
`test_viewer_role_rename.py`), and the Wave 6.16-P5.1 residual-risk dispositions for L2/L3.
Round-1 text above is retained as history.

**Method:** `git status` + `git diff` of the touched files; read the installed Django 5.2.17
`BaseHandler.resolve_request`, `URLResolver.resolve` and `HttpRequest.__init__` to confirm the
`resolver_match` timing; two throwaway probes run from the pre-approved temp dir (nothing added to
the repo) exercising the live log line and the `_is_superadmin` predicate; re-ran the role/UI suites.
**Gate observed:** the three new suites → **32 passed**;
`test_roles.py + test_viewer_readonly_ui.py + test_viewer_readonly_api.py + test_viewer_affordances.py`
→ **40 passed**.

### R2-1 — L1 (log injection) — FIXED, verified at the wire

`not_viewer` now logs only `request.method`, the resolver's `url_name` (falling back to `"?"`) and
`extra={"user_id": ...}`. All three are server-controlled:

- `request.method` comes from the server's `REQUEST_METHOD` environ and is **not** percent-decoded; an
  HTTP method token cannot carry CR/LF. No `%0A` reaches it.
- `url_name` is a URLconf constant (e.g. `ui-case-status`); a URL segment can never enter it.
- `user_id` is `str(request.user.pk)`, a server-side UUID.

`resolver_match` is **reliably present** inside a URL-resolved view. `HttpRequest.__init__` seeds
`self.resolver_match = None`, and `BaseHandler.resolve_request` sets
`request.resolver_match = resolver.resolve(request.path_info)` *before* it invokes the wrapped view
(`_get_response` calls `resolve_request` first). The view is `not_viewer`-wrapped, so the attribute is
already a real `ResolverMatch`. The `getattr(..., None)`/`"?"` fallback only ever fires for a view
invoked outside URL resolution (e.g. a direct `RequestFactory` call in a test).

Probe (real URL resolution, viewer session): `POST /cases/x%0AInjected/status` →
view ran, logged `viewer denied: POST ui-case-status` with no `\n`/`\r`, `user_id` a UUID, and
`resolver_match.url_name == "ui-case-status"`. The same request also confirmed the original attack path
was real — the decoded newline still resolves through `<str:case_id>` — so the fix removes the sink
without removing the route.

**R2-I1 (Info).** The CWE-117 *class* is not wholly closed: Django's built-in `django.request` logger
still emits `request.path` verbatim for the 4xx response the view returns (`log_response(..., request.path)`,
`django/core/handlers/base.py:143-146`), and the probe captured exactly that. This is pre-existing
framework behaviour affecting every user and every 4xx/5xx, not introduced by this wave; closing it would
need a logging filter/formatter. Record it as a follow-up, not a blocker.

### R2-2 — `cases/views.py::_as_uuid` — SOUND

- **Malformed cannot become success.** The early `isinstance(value, UUID)` return only special-cases a
  value that is already a UUID; every non-UUID string falls to `UUID(value)` and an exception becomes
  `None`. Every call site translates `None` into a deny/404 (`_resolve_task:188`,
  `_resolve_observable_scoped:202`, the UI assignee `... if pk else None`), and the only "fall through"
  (`_resolve_org:211`) is a deliberate id-then-name lookup. No caller uses the result as an unfiltered
  queryset. So a malformed id is a uniform 404 at the path boundary — the stated contract.
- **Catching `AttributeError` is safe here.** `UUID(hex)` raises `AttributeError` only when `hex` lacks
  `.replace` (a non-str, non-UUID object); with the UUID fast-path that branch is effectively
  belt-and-braces. A pure parser turning input-shape errors into `None → 404` is the intended contract,
  and it runs before any ORM access or transaction, so it cannot mask a mutation. Accept.

### R2-3 — V6b viewer deny — CORRECT, but the pinning test is vacuous (R2-L1)

The deny is placed first in all three resolvers and is deny-only (`is_viewer` is false for every role
except `viewer`), so it cannot touch a legitimate non-viewer:

- **WebSocket consumer** (`realtime/consumers.py:94-98`) resolves with `write=False`; the viewer guard
  only fires on `write=True`, so a viewer keeps the case room — pinned end-to-end by
  `test_viewer_reads.py::test_a_viewer_can_subscribe_to_the_case_room_and_sync`.
- **Automation executor** does not call the resolvers; the `POST /playbook/{id}/run` view
  (`automation/views.py:284-292`) is behind `IsAnalystOrAbove` and passes `write=True` only for the
  case, which is unaffected for a non-viewer.

**R2-L1 (Low) — the V6b test and narrative overstate the change.** `_is_superadmin(user)` tests
`getattr(user, "role", "") == "superAdmin"`, while `is_viewer(user)` tests `role == "viewer"`. The two
are mutually exclusive for any single `role` value, so the removed conjunct
`not _is_superadmin(user)` was **always true whenever `is_viewer(user)` was true** — the exception was
dead code and its removal is behaviourally inert. `test_viewer_precedence.py::test_a_viewer_flagged_as_superuser_is_still_denied_write`
sets `is_superuser=True` (Django's flag), which `_is_superadmin` never reads, so it passes for the
wrong reason and exercises nothing about V6b. Probe: for `role=viewer, is_superuser=True`,
`is_viewer(u) is True`, `_is_superadmin(u) is False`, and `resolve_case(u, ..., write=True) is None`
(the superadmin branch is role-based, so `resolve_case` denies too). No security impact — the deny is
correctly in place — but the deviations **V6b** entry ("skipped the viewer denial for a superAdmin")
and the test docstring claim an invariant the code path cannot violate. Either correct the narrative
(removed as logically redundant, not as a fix), or make `_is_superadmin` consult `is_superuser` if that
is the intended semantic — at which point the test becomes meaningful and would catch a re-added guard.

### R2-4 — review of the three new suites

- `test_viewer_reads.py` — sound. The attachment test's `404` is honest (blob absent on disk) and is
  made non-vacuous by the analyst-parity assertion plus the fact that the `<uuid:...>` route exercises
  the `_as_uuid` UUID fast-path; the WebSocket test is genuine. Minor: the module docstring promises a
  content assertion for the page inventory that the parametrised `200` check does not make — a docstring
  mismatch, not a false pass.
- `test_viewer_precedence.py` — sound apart from R2-L1. The controls are real
  (`test_the_same_write_share_lets_a_non_viewer_write`), the API-403/UI-404 split is asserted, and the
  DB is re-read to prove the mutation did not land.
- `test_viewer_role_rename.py` — sound. The CHECK test goes through the ORM (avoiding the SQLite
  dashless-UUID trap the comment names), and the migration test drives `MigrationExecutor` forward and
  back and restores the graph leaves in `finally`.
- `test_viewer_readonly_ui.py` (pre-existing) remains behavioural with a real non-vacuity control;
  the L3 residual it leaves (a future view that 404s on a dummy id before validating the body) is
  unchanged.

### R2-5 — L2/L3 disposition

- **L2 (Django admin) — acceptable as accepted-Low, provided the follow-up is actually tracked.**
  `role` stays orthogonal to `is_staff`/`is_superuser` and `/admin/` is reached by none of the three
  layers, so the "every endpoint" claim is still broader than the enforcement. Deployment-side
  mitigation (never make a viewer staff) is defensible, but the deviations text says a structural fix
  "is tracked as a follow-up" and I could not find a corresponding TODO/§11 entry — the follow-up is
  presently **un-tracked**. Keep it open at **Low** with a real ID, and prefer the cheap structural
  guard (refuse, or warn, on `role=viewer` + `is_staff` in `identity/services.py`, mirroring the
  last-superAdmin guard) over docs alone.
- **L3 (per-view UI gate) — accepted-Low is correct.** Note the P4 note describes *Layer 3
  affordances* being UX, which is a slightly different object from Round-1 L3 (the server `not_viewer`
  gate is opt-in per view; only the affordances are Layer 3). The substance is right: the gate is
  enforced by a behavioural, non-vacuous URLconf sweep, so drift is caught in CI. A runtime
  `AppConfig.ready()` assertion would make the UI layer symmetric with the API, but that is a hardening
  preference, not a blocker.

---

## Round 3 — 2026-10-12 (final)

**Re-review scope:** the remediation of Round-2 **R2-L1** only — the deny-first ordering in
`identity/scoping.py::resolve_case` (it had sat below the `_is_superadmin` branch) and the replacement
ordering test `test_viewer_precedence.py::test_the_viewer_deny_precedes_the_privileged_branch` — plus the
tracking status of everything the review raised (Round-2 L2, R2-I1). Rounds 1–2 above are retained as
history.

**Method:** source read of `identity/scoping.py` (`resolve_case`/`resolve_alert`/`resolve_observable`)
and the new test; a **scratch copy** of the module, written outside the repo in the pre-approved temp dir,
with the deny moved back *below* each privileged branch, then driven through the test's own logic to
prove it would fail (no repo file was modified or reverted); a run of the shipped suite; and a
cross-check of `TODO.md §11.5`, `docs/spec/deviations.md` "Wave 6.16-P5.1" and
`PLAN-2026-10-12-viewer-role-enforcement.md §13`.
**Gate observed:** `tests/conformance/test_viewer_precedence.py` → **8 passed**. No application code or
test was changed by this review.

### R3-1 — deny-first in all three resolvers — CONFIRMED

`if write and is_viewer(user): return None` is now the **first** statement of each resolver:
`resolve_case` (line 142, ahead of the `_is_superadmin` branch at 145), `resolve_alert` (line 183,
ahead of 186) and `resolve_observable` (line 211, ahead of 214). A `write=True` resolve therefore denies
a viewer **before any privileged (superAdmin), owner-org or share branch**, in all three. This is
ordering discipline, not a behaviour change for real accounts: `is_viewer` and `_is_superadmin` are
mutually exclusive on a single `role` value (`"viewer"` vs `"superAdmin"`), and decision 4 makes the
combination unreachable — the reordering exists so the code states positively what ADR-006 decision 2
claims ("viewer" is a cap on the person, not a grant to arbitrate).

### R3-2 — the replacement test is non-vacuous — CONFIRMED

The test monkeypatches `identity.scoping._is_superadmin` to `lambda user: True` (line 188), then asserts
`resolve(..., write=False) is not None` **and** `resolve(..., write=True) is None`, for `case`, `alert`
and `observable`.

- **The privileged branch is genuinely live under the patch.** For `alert` and `observable` the owner
  org differs from the viewer's org, so a read can only return a row through the patched superAdmin
  branch — the read assertion is itself proof the patch took effect. (For `case` the read would also
  pass via the write-share branch, so that parametrisation alone is not the proof; the `alert`/
  `observable` cases carry it, and they are in the same parametrisation.)
- **The test would fail on the old ordering.** The scratch copy — deny moved back below the superAdmin
  branch in all three resolvers — was driven through the identical setup: the read resolved and the
  **write** resolve returned a row for all three (`resolve_case` → `Case(1)`, and likewise for
  `resolve_alert`/`resolve_observable`). The shipped assertion is `... is None`, so on the pre-fix
  ordering the test fails. It cannot pass for an unrelated reason: with `_is_superadmin` forced True,
  the superAdmin branch returns a row before any org/share logic, so **the viewer deny is the only path
  that can turn the write result into `None`** — which is exactly the ordering being pinned.
- The old test's failure mode (setting Django's `is_superuser`, never read by `_is_superadmin`) is gone;
  the new module docstring records the R2-L1 origin in place, and `deviations` **V6b** has been rewritten
  to match ("the ordering is pinned by a monkeypatched test … round-2 review R2-L1").

### R3-3 — no legitimate non-viewer is denied by deny-first — CONFIRMED

- **superAdmin:** `role == "superAdmin"` is not `"viewer"`, so `is_viewer` is `False` and the guard is a
  no-op; the superAdmin branch below still returns any case/alert/observable exactly as before.
- **WebSocket consumer** (`realtime/consumers.py:94-98`): resolves with `write=False`; the guard only
  fires on `write=True`, so a viewer keeps the case room and every non-viewer is untouched.
- **Automation run-now** (`automation/views.py`): the endpoint checks `IsAnalystOrAbove` explicitly
  (lines 265-270) before it resolves; a viewer gets a 403 there and never reaches the resolver, while an
  analyst/superAdmin passes the permission and is not a viewer, so `resolve_case(..., write=True)`
  behaves normally.
- `is_viewer` is also `False` for `None`, anonymous callers and non-`User` identities, so ingestion
  tokens and other machine paths are never caught by the guard.

### R3-4 — tracking of every raised item — CONFIRMED

- **Round-2 L2 (Django admin outside the role model)** — `TODO.md §11.5` records it open at Low with the
  preferred cheap structural fix (refuse/warn on `role=viewer` + `is_staff` in `identity/services.py`,
  plus an admin `has_permission` guard and a service-layer test), and
  `deviations.md` "Wave 6.16-P5.1" residual-risk lists it with the same disposition. They match.
- **Round-2 R2-I1 (`django.request` logs `request.path`)** — `TODO.md §11.5` records it with the logging
  filter/resolver-match fix; `deviations.md` "Wave 6.16-P5.1" matches; plan §13 states both stay open at
  Low/Info in `TODO §11.5`. They match.
- **R2-L1** is resolved by code + test and reflected in `deviations` V6b and plan §13. **Nothing raised
  across rounds 1–3 is untracked.**
- **Residual doc nit (Info, non-blocking).** Two surviving references still cite the *deleted* vacuous
  test name `test_a_viewer_flagged_as_superuser_is_still_denied_write`: plan §13's embedded §12 answer 3b
  and `VERIFY-2026-10-12-viewer-role-enforcement.md` line 169 (the latter describes it as confirming the
  ordering, which was precisely the false comfort R2-L1 identified). Neither affects enforcement; update
  both to the new test name when the docs are next touched.

## Sign-off (final)

R2-L1 is resolved: the deny is the first statement in all three resolvers, and the replacement test is
non-vacuous — a scratch copy with the deny restored below the privileged branch makes the shipped
assertion fail for `case`, `alert` and `observable`, while the patched-branch read proves the guard is
the only thing producing the denial. No legitimate non-viewer (superAdmin, WebSocket read, automation
run-now) is affected, and every item from rounds 1–3 (L1 fixed; L2 and R2-I1 tracked in `TODO §11.5` and
mirrored in `deviations`/plan §13; L3 accepted) is accounted for. **Approve the wave — no open security
blockers**, with L2/R2-I1 as accepted Low/Info follow-ups and one cosmetic doc-reference fix outstanding.
