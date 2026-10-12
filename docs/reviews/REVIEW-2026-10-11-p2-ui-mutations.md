# Security Review — Wave 6.16-P2 UI mutations (alert triage, case edit, observable edit/delete)

**Date:** 2026-10-11
**Auditor:** security-auditor (read-only on product code; this report is the only artefact written)
**Scope:** the uncommitted P2 state-changing UI surfaces — `ui/views.py` (`alert_status`, `case_edit`, `observable_edit`, `observable_delete`), the shared applier `cases/update.py`, `identity/scoping.py` (`resolve_alert`/`resolve_observable`), `ui/urls.py`, and the templates `ui/case_edit.html`, `ui/observable_detail.html`, `ui/alert_detail.html` (plus the `ui/case_detail.html` edit link).
**Plan / deviations:** `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P2 + §13c; `docs/spec/deviations.md` “Wave 6.16-P2” (W616P2-1…6).
**Baseline:** working tree vs `cc9e569` (everything reviewed is uncommitted on `main`).
**Method:** git-diff source review of every changed/added file; traced the shared applier and the tenant-scoping helpers; manual read of the settings (CSRF/CSP/middleware); targeted dynamic verification (a temporary throwaway pytest probe, since removed) plus the shipped conformance tests.
**Gate observed:** `pytest tests/conformance/test_ui_p2_wiring.py -q` → **24 passed**; `pytest tests/conformance/test_tenant_scoping.py::TestUIRouteEnumerationConformance ::TestWriteRouteEnumerationConformance -q` → **6 passed**.

## Verdict

**CLEAR FOR SIGN-OFF WITH ONE MEDIUM ITEM TO REMEDIATE.**

No Critical/High issue. The four views are correctly org-scoped with a uniform 404, CSRF-protected, autoescape/CSP-safe, and the cross-case `force` guard is server-enforced on both edit and delete. Two real weaknesses remain: an **unscoped `assignee` resolver reachable from the new case-edit form (M1, cross-tenant reference + user-existence oracle)** and **missing enum range validation that turns a crafted edit into an unhandled 500 (L1)**. One low audit-atomicity gap (L2). `data` immutability, the bounded-vocabulary checks, and the `delete()` audit-pk capture are solid.

---

## Findings (severity-ranked)

| ID | Sev | Area | File:line | Status |
|----|-----|------|-----------|--------|
| M1 | **MEDIUM** | Cross-tenant assignee injection + global user-existence oracle | `ui/views.py:1435-1439`, `cases/update.py:70-72`, `cases/views.py:539-555` | Open |
| L1 | LOW | Unhandled `IntegrityError` (HTTP 500) on out-of-range `severity`/`tlp`/`pap` | `ui/views.py:1410-1420`, `cases/update.py:62-65`, `cases/models.py:136-140` | Open |
| L2 | LOW | Audit row not in the same transaction as the edit mutation | `ui/views.py:1446-1462`, `ui/views.py:1603-1614` | Open |
| N1 | Info | `resolve_*` catch `TypeError` wider than needed; `dict(post_data)` re-render artefact | `identity/scoping.py:156,165,176,185`; `ui/views.py:1476,1624` | Accepted / note |

---

### M1 — MEDIUM: crafted POST can assign a case to a user in another organisation (and probe logins across tenants)

**Where:** the UI only *renders* owner-org assignees (`ui/views.py:1368-1372`), but the POST takes the raw form value and forwards it to the shared applier without any org check (`ui/views.py:1435-1439` → `cases/update.py:70-72`), which calls `_resolve_login` (`cases/views.py:539-555`). `_resolve_login` looks the value up with `User.objects.filter(login=str(value)).first()` — **no org filter** (the UUID fallback is likewise unscoped).

**PoC (confirmed):** as an analyst in `ProbeOrg`, POST to `/ui/cases/<n>/edit`:

```
title=x&assignee=probe_out
```

`probe_out` is an active user of `ProbeOther`. Observed response `302` and after `refresh_from_db()` `case.assignee.login == "probe_out"`, `case.assignee.org.name == "ProbeOther"`. A UUID from another org works the same way.

**Impact:**
- Cross-tenant referential integrity break: an Org A case is bound to an Org B principal (ADR-004 tenant boundary). This is reachable by any user with write on the case — including a write-share holder from a third org.
- Cross-tenant **user-existence oracle**: the case detail/assignee field reflects whether the guessed login exists, so a caller can enumerate valid global logins. It is also a mild information-disclosure primitive.
- No data-disclosure of the case body was found (access is still governed by `owner_org`/`shares`, and there is no “my assigned cases” query that would surface the Org A case to the Org B user), so it is **not** an IDOR into Org A content — hence MEDIUM, not HIGH.

**Why it is a regression in spirit:** the codebase deliberately scopes assignee resolution elsewhere — `ui/views.py:456` (`User.objects.filter(org_q("org", request.user), login=login)`) and `ui/views.py:357`. The new UI path bypasses that pattern.

**Fix:** resolve the assignee against the **case’s owner org** before it reaches the applier, e.g. in `case_edit`:
```python
assignee_val = (request.POST.get("assignee") or "").strip()
if assignee_val:
    person = User.objects.filter(org=case.owner_org, login=assignee_val).first()
    if person is None:
        person = User.objects.filter(org=case.owner_org, pk=_as_uuid(assignee_val)).first()
    if person is None:
        messages.error(request, "Assignee is not a member of this case's organisation.")
        return _render_case_edit_form(...)
    payload["assignee"] = person.pk
elif "assignee" in request.POST:
    payload["assignee"] = None
```
(or thread an `org`/`case` parameter into `_resolve_login` so the API path is fixed too). Add a regression test: org-A analyst posting an org-B login must get a re-render and leave `case.assignee` unchanged.

---

### L1 — LOW: out-of-range `severity`/`tlp`/`pap` raises an unhandled `IntegrityError` (500)

**Where:** `ui/views.py:1410-1420` only checks that the three fields parse as `int`; `cases/update.py:62-65` then `setattr`s them with no range check; the DB enforces the range via `CheckConstraint`s (`cases/models.py:136-140`, `core/enums.py:40-60`). `case_edit` catches only `CaseUpdateError` (`ui/views.py:1446-1451`), so the `IntegrityError` propagates.

**PoC (confirmed):** POST `/ui/cases/<n>/edit` with `title=x&severity=99` (or `tlp=99`) → HTTP **500**; the row is unchanged (the surrounding `transaction.atomic()` rolls back). The same gap exists on the API PATCH (pre-existing, shared applier).

**Impact:** an authenticated writer can reliably produce 500s (log/alert noise, minor availability/UX degradation; no state corruption, no internals leaked with `DEBUG=False`). Not a confidentiality/integrity breach.

**Fix:** range-check in the shared applier so both surfaces return a 400/form error:
```python
for key, lo, hi in (
    ("severity", SEVERITY_MIN, SEVERITY_MAX),
    ("tlp", TLP_MIN, TLP_MAX),
    ("pap", PAP_MIN, PAP_MAX),
):
    if key in payload:
        try:
            v = int(payload[key])
        except (TypeError, ValueError):
            v = -1
        if not lo <= v <= hi:
            raise CaseUpdateError(
                "BadRequest",
                f"{key} must be between {lo} and {hi}",
                {key: [f"must be between {lo} and {hi}"]},
            )
```
`APPLY` this in `_set_case_fields` so the UI’s explicit `int()` loop can stay a parse-only guard.

---

### L2 — LOW: the audit row is written outside the edit transaction

**Where:** `case_edit` commits the mutation inside `with transaction.atomic(): apply_case_update(...)` (`ui/views.py:1446-1448`) and only then creates the `AuditLog` row on the next statement (`ui/views.py:1454-1462`). `observable_edit` is not wrapped in a transaction at all: `observable.save()` (`ui/views.py:1603`) is followed by `AuditLog.objects.create(...)` (`1606-1614`). If the audit insert fails (DB hiccup, oversized `ip`/`action`, etc.) the mutation is already persisted and the request 500s — the change is recorded **without** its audit event, which weakens the “security-relevant action is always audited” guarantee.

**Contrast:** `observable_delete` gets this right — pk/org captured before `delete()` and `delete()`+audit inside one `transaction.atomic()` (`ui/views.py:1669-1681`). That is the pattern to copy.

**Fix:**
```python
with transaction.atomic():
    apply_case_update(case, payload, actor=request.user)
    AuditLog.objects.create(...)
```
and likewise wrap `observable.save(...)` + `AuditLog.objects.create(...)`. Then a failed audit rolls back the change (fail-closed) instead of leaving an unlogged mutation.

---

### N1 — Notes / accepted

- **`identity/scoping.py:156,165,176,185`** — catching `TypeError` (in addition to `DoesNotExist`/`ValidationError`/`ValueError`) is slightly broader than needed, but it is confined to the single `.get()` call, does not blanket-suppress, and buys the uniform-404 contract. It cannot mask a genuine bug outside the pk lookup. Acceptable.
- **`ui/views.py:1476,1624`** — `dict(post_data)` on a `QueryDict` yields *lists* as values, so an error re-render displays e.g. `['x']` rather than `x`. Cosmetic only; Django autoescaping keeps it inert. Optional cleanup: `post_data.get(key)`.
- **`case_edit` context omits `is_cross_org`** though `ui/case_edit.html:7` tests it — the cross-org banner never shows. Cosmetic (no security effect).
- **No rate limiting** on the four mutation views. They are session-auth (+CSRF), so the residual is authenticated audit/timeline spam. Acceptable for MVP; consider a shared throttle later.

---

## Point-by-point assessment

### 1. IDOR / tenant isolation — PASS (with M1 caveat)
- `alert_status` (`ui/views.py:1301-1311`): route is `<uuid:alert_id>`; `resolve_alert` filters `owner_org_id` (`identity/scoping.py:163-166`). Non-UUID never reaches the view; a missing/cross-org id renders `ui/404.html` **404**.
- `case_edit` (`ui/views.py:1362-1364`): `resolve_case(..., write=True)` — owner org full access, shared org only with `permissions.write` (`identity/scoping.py:133-141`). Non-existent and cross-org-without-share both → **404**. New route is covered by the write/read enumeration conformance tests.
- `observable_edit` / `observable_delete` (`ui/views.py:1504-1506`, `1656-1658`): `resolve_observable` filters `owner_org_id`. Uniform **404**.
- Non-UUID `<str:>` ids are now caught (`W616P2-6`): `/ui/observables/not-a-uuid/delete` → **404**, not 500; `/ui/cases/99999999/edit` → 404 (verified). No 403-vs-404 existence oracle.
- **Caveat:** M1 is a cross-tenant *object reference* via `assignee`; it does not disclose Org A content but violates the boundary.

### 2. CSRF — PASS
`CsrfViewMiddleware` is enabled (`amalthea/settings/base.py:49`), none of the four views is `csrf_exempt` (only `compat/views.py` and the `ingest` webhook are, both out of scope). Every form carries `{% csrf_token %}`: `case_edit.html:24`, `observable_detail.html:37` (edit) and `:239` (delete), `alert_detail.html:59`. The shipped test posts without a token → **403**.

### 3. Input validation / mass assignment — PASS on the whitelist; L1/M1 gaps
- **No `owner_org`/`number`/`id` assignment:** `case_edit` builds an explicit payload (`ui/views.py:1404-1444`) and `apply_case_update` only applies `CASE_SCALAR_FIELDS` + `status` + `assignee` (`cases/update.py:22,54-73`). Verified: posting `owner_org`, `number`, `id` leaves all three unchanged.
- **No vocabulary minting:**
  - Case `status` is checked to exist before it reaches the tolerant resolver (`ui/views.py:1427-1432`); a made-up value re-renders and creates no `CaseStatus`.
  - Alert `status` is filtered to existing `AlertStatus` rows with `stage__in ALERT_STAGES` and `exclude(stage="Imported")` (`ui/views.py:1313-1323`); garbage is rejected, no row minted.
  - Observable `dataType` goes through `ObservableType.objects.filter(name=...).first()` (`cases/views.py:969-976`); an unknown type returns the applier’s 400 → form error, no mint.
- **`data` is immutable** on both surfaces; posting `data=changed` is ignored (`ui/views.py:1555-1559`; test asserts it).
- `enrichment_data` must parse as JSON **and** be an object, else re-render (`ui/views.py:1576-1591`).
- Gaps: enum ranges (L1) and unscoped assignee (M1).

### 4. XSS / CSP — PASS
- No `|safe`, `autoescape off`, `<script>`, `style=`, `on*=` handler or `javascript:` in any changed template (grep of `case_edit.html`, `observable_detail.html`, `alert_detail.html`, `case_detail.html`). The only `<script>` is the existing `<script src="{% static 'ui/live.js' %}" defer>`.
- CSP is strict and self-only, with no `'unsafe-inline'`/`'unsafe-eval'` (`amalthea/settings/base.py:65-78`; `script-src 'self'`), asserted end-to-end by `tests/conformance/test_prod_settings.py`.
- All echoed values (`form_values.*`, `observable.data`, `observable.message`, the `enrichment_data` textarea, error messages containing attacker-supplied `status`) are autoescaped.

### 5. Cross-case `force` guard — PASS
- Server-side for **both** paths: `observable_edit` (`ui/views.py:1549-1553`) and `observable_delete` (`ui/views.py:1660-1665`) call `_observable_cross_case_guard` and refuse unless `force` is truthy (`cases/views.py:891-907`). Omitting `force` on a multi-case observable is refused — the `required` attribute is only a progressive-enhancement nicety.
- The acknowledgement checkbox lives **inside** the posting `<form>` (`observable_detail.html` edit form and delete-modal form), so it both gates and sets `force`; the conformance test forbids a hidden `force` input. The earlier frontend bug (checkbox outside the form / mismatch with the `== "on"` test) is fixed.
- Bypass attempt: none found. Supplying `force=on` is the intended acknowledgement, not a bypass — the caller already needs write on the observable’s org.

### 6. Audit integrity — PASS except L2
- Acting user is `request.user`; `target_type`/`target_id` are correct (`"Alert"`, `"Case"`, `"Observable"`; `str(obj.id)`), and `target_org` is the resource’s owner org.
- `observable_delete` captures `str(observable.id)` and the owner org **before** `delete()` (`ui/views.py:1669-1680`) — no `"None"` pk, no detached-FK re-query (W616P2-6). Test asserts the real pk.
- Gap: L2 — the edit audit is not committed atomically with the mutation.

### 7. Error handling — PASS
- No broad `except Exception`/bare `except` in `ui/views.py`, `cases/update.py`, or `identity/scoping.py`; every handler is a specific exception (`ValueError`, `json.JSONDecodeError`, `CaseUpdateError`, etc.).
- The new `scoping.py` catches are localised to the `.get()` and preserve a genuine query failure as a 404 only for id-parse errors; they do not swallow unrelated exceptions.
- L1 is an unhandled constraint violation, not an over-broad catch; with `DEBUG=False` it yields the generic 500 page (no internals). No SQL construction anywhere — all access is ORM parameter binding.

---

## What is solid

- **Uniform 404** across all four views, including the newly hardened non-UUID `<str:>` paths — no 403 existence oracle.
- **Bounded vocabularies, no minting** of `CaseStatus`/`AlertStatus`/`ObservableType`; `data` immutable; `enrichment_data` type-checked.
- **Explicit allow-list applier** (`CASE_SCALAR_FIELDS` + `status`/`assignee`) shared by API and UI, so the two surfaces cannot drift (W616P2-2); `owner_org`/`number` are unreachable.
- **CSRF** on every form with the middleware globally enabled.
- **CSP** strict `'self'` and **autoescaping**; no inline script/style/handlers and no `|safe`.
- **Cross-case `force` guard** enforced server-side on edit and delete, with the acknowledgement inside the form.
- **Audit pk capture before `delete()`** and delete+audit in one transaction.
- **No broad exception handlers**; specific, localised catches.

## Residual / accepted risks

- L2 (unlogged edit on audit failure) and N1 (wide-but-local `TypeError`, cosmetic re-render values, absent rate limiting) are low-severity and accepted pending the M1/L1 fixes. The pre-existing API parity gaps (assignee scoping, enum 500s) are fixed for free if M1/L1 are addressed in the shared applier.

## Recommended follow-ups (in priority order)

1. **M1** — scope assignee resolution to the case’s owner org (UI + shared applier) and add the cross-org regression test.
2. **L1** — range-validate `severity`/`tlp`/`pap` in `_set_case_fields` → `CaseUpdateError`.
3. **L2** — wrap the edit mutation and its audit row in one `transaction.atomic()` (copy the delete pattern).
4. Optional: rate-limit the mutation views; clean up the `dict(QueryDict)` re-render values; pass `is_cross_org` into the `case_edit` context.

---

## Re-audit 2026-10-11

**Auditor:** security-auditor (read-only on product code; this section is the only artefact written)
**Scope of this pass:** independently verify the remediation of **M1 / L1 / L2 / N1** on the uncommitted working tree (same baseline `cc9e569`), reproducing the original PoCs rather than trusting the remediation summary.
**Method:** git-diff review of `cases/views.py`, `cases/update.py`, `ui/views.py`, `identity/scoping.py`, `ui/templates/ui/case_edit.html`; a throwaway pytest probe (since deleted) that re-ran the original PoCs 1–3 plus the applier/bulk parity checks; the shipped conformance suite.
**Gates observed:**
- `pytest tests/conformance/test_ui_p2_wiring.py -q` → **28 passed** (was 24).
- `pytest test_ui_p2_wiring.py test_t2_p4_surface.py test_observable_bulk_guard.py + tenant-scoping UI/write enumeration classes -q` → **48 passed**.

### Status summary

| ID | Prior sev | Status | One-line |
|----|-----------|--------|----------|
| M1 | MEDIUM | **FIXED** | assignee resolution now org-scoped in the shared applier, the task path and the UI pre-check; original cross-org PoC closed |
| L1 | LOW | **FIXED** | range validation in `cases/update.py::_set_case_fields` → form error / HTTP 400, never 500 |
| L2 | LOW | **FIXED** (as cited) | `case_edit` and `observable_edit` mutation+audit share one `transaction.atomic()`; but the same class survives in `alert_status` → **NEW-2** |
| N1 | INFO | **FIXED** | re-render reads scalars via `QueryDict.get()`; `is_cross_org` now passed; the `TypeError` catch is unchanged (still accepted) |
| NEW-1 | LOW | **OPEN** | `case_edit` never preselects the current assignee ⇒ an *untouched* browser save silently unassigns the case (integrity/UX, no authz bypass) |
| NEW-2 | LOW | **OPEN** | `alert_status` writes the audit **outside** the mutation transaction ⇒ an audit failure leaves a status change unlogged (L2 class) |

### M1 — FIXED

**Verification (code):**
- `cases/views.py::_resolve_login(value, *, org=None)` now filters a single queryset by `org` for **both** the login and the UUID fallback (`cases/views.py:539-563`): `users = users.filter(org=org)` before either lookup.
- `cases/views.py::_set_task_fields` threads the owning case's org: `_resolve_login(payload["assignee"], org=task.case.owner_org)` (`cases/views.py:522`).
- `cases/update.py::_set_case_fields` calls `_resolve_assignee(payload["assignee"], case.owner_org)` (`cases/update.py:95`), and `_resolve_assignee` delegates to `_resolve_login(value, org=org)` (`cases/update.py:52-63`). Because this is the single shared applier, the **API PATCH (single and `_bulk`) and the UI are both scoped**.
- `ui/views.py::case_edit` pre-validates the posted assignee against `case.owner_org` — login first, then `_as_uuid` → UUID — and re-renders with `"Assignee is not a member of this case's organisation."` (`ui/views.py:1440-1448`, `_render_case_edit_form` at `1480`).
- No remaining unscoped `_resolve_login(..., org=None)` call site: the only two callers (`cases/views.py:522` and `cases/update.py:63`) both pass an org (grep-verified).

**Verification (dynamic PoC re-run):**
- **PoC 1:** as the ProbeOrg analyst, POST `/ui/cases/<n>/edit` with `assignee=<org-B login>` **and** `assignee=<org-B UUID>` → **HTTP 200** (error re-render both times) and `case.assignee_id` unchanged (still the in-org user). The original result (302 + `case.assignee` switched to the org-B user + existence oracle) no longer reproduces.
- **API parity:** `apply_case_update(case, {"assignee": outsider.login})` → `case.assignee_id is None` (and same for the outsider UUID). `PATCH /api/v1/case/{n}` with a cross-org `assignee` returns 200 and never binds the outsider.
- The shipped regression tests `::test_case_edit_cross_org_assignee_is_refused` and `::test_shared_applier_scopes_assignee_to_owner_org` pass.

### L1 — FIXED

**Verification (code):** `cases/update.py` defines `_CASE_RANGE_FIELDS` from `core.enums` bounds and `_set_case_fields` int-parses and range-checks each of `severity`/`tlp`/`pap`, raising `CaseUpdateError("BadRequest", "<key> must be between <lo> and <hi>", ...)` (`cases/update.py:28-32,78-87`). Both surfaces consume the shared applier.

**Verification (dynamic PoC re-run):**
- **PoC 2 (UI):** POST `/ui/cases/<n>/edit` with `severity=99`, `tlp=99`, `pap=99` → **HTTP 200** form error; the stored value is unchanged. No 500.
- **PoC 2 (API):** `PATCH /api/v1/case/{n}` with `severity|tlp|pap = 99` → **HTTP 400** with body `{"type":"BadRequest","message":"severity must be between 1 and 4","fields":{"severity":["must be between 1 and 4"]}}`.
- **Bulk parity (not a regression):** `PATCH /api/v1/case/_bulk` with `severity=99` → HTTP 200 with per-item `status: 400` and message `severity must be between 1 and 4` (the removed `Response` branch is covered because `CaseUpdateError` subclasses `ValueError`, which `compat/bulk.py:78` catches). A valid bulk item still commits (`status: 200`).
- Tests `::test_case_edit_out_of_range_severity_rerenders_not_500` and `::test_shared_applier_rejects_out_of_range_enums` pass.

### L2 — FIXED (as cited); same class remains in `alert_status` (see NEW-2)

**Verification (code):** `case_edit` now runs `apply_case_update(...)` and `AuditLog.objects.create(...)` inside one `with transaction.atomic():` (`ui/views.py:1461-1471`); `observable_edit` does the same around `observable.save(...)` + `AuditLog.objects.create(...)` (`ui/views.py:1632-1642`). `observable_delete` was already correct (`ui/views.py:1702`).

**Verification (dynamic):** patching `ui.views.AuditLog` so `.objects.create` raises, then submitting each form:
- `case_edit` → HTTP 500 and `case.title` rolled back to its prior value.
- `observable_edit` → HTTP 500 and `observable.message` rolled back.
So a failed audit now rolls the mutation back (fail-closed), as required.

**Residual:** the same wave's `alert_status` does not have this property — reported as **NEW-2**. Note the original L2 text cited only `case_edit`/`observable_edit`, so this is a gap in the original finding rather than an incomplete remediation of the cited lines.

### N1 — FIXED

- `ui/views.py::_render_case_edit_form` and `::_render_observable_edit_form` now read scalars with `post_data.get(key, "")` instead of `dict(post_data)` (`ui/views.py:1501`, `1655`). Verified: posting `title=typedtitle` with a bad `status` re-renders 200 and the body contains `typedtitle`, not `['typedtitle']`.
- `case_edit` now passes `is_cross_org` on the GET render **and** both error re-renders (`ui/views.py:1394-1402`, and the `_render_case_edit_form` callers), so the cross-org banner in `ui/case_edit.html:7` can render.
- The `identity/scoping.py` `TypeError`/`ValidationError` catches remain (the pre-existing “accepted / note”); they are still localised to the pk lookup. Unchanged, acceptable.

### NEW-1 — LOW (integrity/UX, not a security boundary bypass): `case_edit` does not preselect the current assignee, and an untouched save unassigns the case

**Where:** `ui/views.py:1386` sets `form_values["assignee"] = str(case.assignee_id)` (a UUID), while `ui/templates/ui/case_edit.html:82` preselects with `{% if form_values.assignee|default:case.assignee.login == person.login %}`. A UUID never equals a login, so the `default` filter keeps the truthy UUID, no `<option>` is marked `selected`, and the browser falls back to the first option — `Unassigned` (`value=""`, line 80). On submit the view reads `assignee_val = ""`, which hits the `elif "assignee" in request.POST: payload["assignee"] = None` branch (`ui/views.py:1440,1449-1450`).

**Reproduction (confirmed):** as the case's own assigned org-A writer, `GET /ui/cases/<n>/edit`, then POST the form *unchanged* except for the title (`assignee=""`). Result: **HTTP 302** and `case.assignee_id` becomes `None` — editing a title silently unassigns the case.

**Impact:** data-integrity / UX regression introduced with the new edit form. It does **not** widen authorization (the actor already has write and could unassign deliberately); it is an accidental-destruction footgun on an ordinary workflow, and it also means a user who lacks “who is assigned” context can lose the assignment without noticing.

**Fix:** prefill the login, not the pk — `"assignee": case.assignee.login if case.assignee else ""` — or have the view treat an empty/unchanged select as “no change” and unassign only on an explicit sentinel. Add a regression test that GETs the form and POSTs it back untouched.

### NEW-2 — LOW: `alert_status` mutates then audits outside a transaction (L2 class)

**Where:** `ui/views.py::alert_status` does `alert.save(update_fields=["status","updated_at"])` (`ui/views.py:1326-1327`), then `AuditLog.objects.create(...)` (`1330-1338`), with no `transaction.atomic()` anywhere in the view.

**Reproduction (confirmed):** patch `ui.views.AuditLog` so `.objects.create` raises, then POST a valid status → **HTTP 500** and `alert.status.value == "Triaged"` — the status change is persisted **without** its audit row. Contrast the now-atomic `case_edit`/`observable_edit`/`observable_delete`.

**Impact:** same as the original L2 — a security-relevant state change can be committed unlogged. Low (needs a DB/insert failure to trigger; no authz or data-disclosure effect).

**Fix:** wrap the `alert.save(...)` + `AuditLog.objects.create(...)` (and the optional case `append_timeline_event`) in a single `transaction.atomic()`, copying the delete pattern.

### Regression checks (applier change did not widen anything)

- **Field set unchanged / no mass assignment:** `PATCH /api/v1/case/{n}` with `{"title":"renamed","owner_org":<org-B id>,"number":424242,"id":<other id>}` returns 200 with only `title` changed — `owner_org`, `number` and `id` are untouched. `CASE_SCALAR_FIELDS` + `status` + `assignee` remain the only applied keys (`cases/update.py:23`).
- **No unscoped `_resolve_login` callers:** grep over the tree finds only the two now-scoped call sites.
- **Bulk not regressed:** `_set_case_fields` raising `CaseUpdateError` is caught as `ValueError` by `compat/bulk.py`, so `_bulk` reports a per-item 400 (verified) instead of a 500.
- Full gates above green (28 + 48 passed).

### Recommended follow-ups (re-audit)

1. **NEW-1** — fix the assignee preselect (login, not pk) and add an “untouched save” regression test.
2. **NEW-2** — make `alert_status` audit atomic with the alert mutation.
3. Optional (unchanged from the original): rate-limit the mutation views.
