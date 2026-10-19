# Security Audit — Case Verdict, Inline Edits, Tags Row & Enforced Close Flow (§12.8)

**Date:** 2026-10-18  
**Auditor:** security-auditor (read-only; no code/tests/docs modified — this report is the only artefact written)  
**Scope:** The feature shipped in commit `2476edbc2227bcf4dac0705793bd15d380f5e29b` ("feat(cases): verdict model, inline edits, tags row and enforced close flow (TODO 12.8)"), reviewed at current HEAD `d596db64d74f65930a159ac6194d4854c9a048b2`. The tree is clean.  
**Reviewed against:** `TODO.md` §12.8 (lines ~681-713) + §12.8-fu1/fu2 (lines ~714-732); `PLAN-2026-10-17-case-verdict-close-flow.md` (approved plan, §8 security, §9 tests); `VERIFY-2026-10-17-case-verdict-close-flow.md` (18/18 ACs Met, provisional).  
**Files read (primary):** `cases/update.py`, `cases/create.py`, `cases/views.py`, `cases/models.py`, `alerts/models.py`, `core/enums.py`, `core/serializers.py`, `identity/models.py` (AuditLog), `identity/scoping.py`, `ui/views.py` (case_set_status, case_close, case_field_edit, case_tag_add, case_tag_remove), `ui/templates/ui/_tab_general.html`, `ui/templates/ui/_case_close_form.html`, `ui/templates/ui/_case_field_form.html`, `ui/templates/ui/_case_tag_add_form.html`, `ui/templates/ui/_modal.html`, plus new conformance tests.  
**Method:** Source review; targeted test run (`pytest -q tests/conformance/test_case_verdict_close.py tests/conformance/test_ui_case_verdict.py` → **49 passed**); static analysis of all write paths reaching `Closed` stage.

---

## Verdict

**CONDITIONAL PASS**

The feature is well-structured: the close rule lives once in `enforce_close_rule()`, called from the three write choke points (`apply_case_update`, `case_bulk_update`, `create_case`); org scoping and role gating are uniform and fail-closed; DB constraints back the verdict vocabulary; AuditLog coverage is complete for the implemented surface. **Three conditions** must be addressed before the feature can be considered fully hardened:

1. **M1** — Add a configurable cap on the number of alerts closed in one `propagate_close_to_alerts` call (DoS / long-transaction risk).
2. **M2** — Document and test that direct `Case.save()` / raw ORM writes bypass the close rule (architectural decision, not a bug, but must be explicit).
3. **L1** — The `ui-case-close` failure path does not re-open the dialog (plan §9.3 promises "dialog re-rendered open"; implementation flashes + redirects). Either implement the re-open or amend the plan.

---

## Findings

| # | Severity | Area | Summary |
|---|----------|------|---------|
| M1 | **Medium** | DoS / transaction safety | `propagate_close_to_alerts` has no upper bound on linked alerts; a case with thousands of alerts creates a long-running transaction holding row locks. |
| M2 | **Medium** | Architectural bypass | The close rule lives in the service layer only; `Case.save()`, `Case.objects.update()`, management commands, shell, or future Celery tasks can close a case without verdict/comment. |
| L1 | **Low** | UX / plan conformance | `ui-case-close` on validation failure flashes an error and redirects to `?tab=general` but does **not** re-open the close dialog (plan §9.3 specifies "dialog re-rendered open"). |
| L2 | **Low** | Tag enumeration | `case_tag_add` returns all global tags not on the case (`available_tags`); no pagination/cap. `Tag` is global by design (plan §11 caveat), so no cross-org leak, but the list grows unbounded. |
| I1 | **Info** | AuditLog detail hygiene | `AuditLog.details` for `case.alerts_closed` includes `verdict` (token, not secret) and `count` — acceptable. No raw payloads or secrets observed. |
| I2 | **Info** | CWE-117 log forging | `not_viewer` decorator logs `match.url_name` (server-controlled), not `request.path` — correct. |

---

### M1 — Medium — Unbounded alert propagation in `propagate_close_to_alerts` (DoS / long transaction)

**Where**  
`cases/update.py:251-290` — `propagate_close_to_alerts()` fetches **all** `Alert` IDs linked to the case (`line 266`), then runs a single `QuerySet.update()` (line 271) and writes one `AuditLog` + one timeline event.

**Impact**  
A case with thousands of linked alerts (plausible in a high-volume ingestion scenario) will:
- Hold row locks on all those `Alert` rows for the duration of the transaction
- Block concurrent writes to those alerts
- Increase transaction duration and WAL volume
- Risk `statement_timeout` or `lock_timeout` on busy clusters

There is no equivalent of `MAX_BULK_PROCEDURES = 100` (ui/views.py:147) for this path.

**Evidence**  
```python
# cases/update.py:266-271
alert_ids = list(Alert.objects.filter(case=case).values_list("id", flat=True))
if not alert_ids:
    return 0
closed_status = resolve_closed_alert_status()
Alert.objects.filter(id__in=alert_ids).update(status=closed_status, verdict=verdict)
```

**Remediation**  
Add a configurable constant (e.g., `MAX_PROPAGATE_ALERTS = 5000`, mirroring `MAX_REMAP_ALERTS` from the retro-mapping wave) and refuse the close with a clear error if exceeded:
```python
if len(alert_ids) > MAX_PROPAGATE_ALERTS:
    raise CaseUpdateError(
        "ValidationError",
        f"Cannot close case: {len(alert_ids)} linked alerts exceeds limit of {MAX_PROPAGATE_ALERTS}. "
        "Close alerts manually or increase the limit.",
        {"alerts": [f"too many linked alerts ({len(alert_ids)})"]},
    )
```
Add a conformance test asserting the limit is enforced and the case stays open.

---

### M2 — Medium — Service-layer close rule bypassable via direct ORM / management commands

**Where**  
`cases/update.py:199-224` (`enforce_close_rule`) is called **only** from:
- `apply_case_update` (line 315) — single PATCH, UI
- `case_bulk_update` (cases/views.py:1910) — bulk PATCH
- `create_case` (cases/create.py:149) — create-to-Closed

**Impact**  
Any code path that sets `case.status` to a `Closed`-stage `CaseStatus` and calls `case.save()` or `Case.objects.update()` **bypasses the rule entirely**:
- Django shell / `manage.py dbshell`
- Future Celery tasks / automation actions that mutate `Case` directly
- Management commands (none currently, but nothing prevents one)
- Raw SQL / direct Postgres access

This is an **architectural decision** (plan §5.2/D1: "service layer … one rule, no bypass" for *API/UI/bulk/create*), not a bug in the current code. However, the model's `save()` method (cases/models.py:184-200) calls `stamp_closed_date()` unconditionally, so a direct save that sets a Closed status will persist `closed_date` without a verdict or comment — creating a "closed" case that violates the invariant.

**Evidence**  
```python
# cases/models.py:202-210
def stamp_closed_date(self) -> None:
    if self.status_id and self.closed_date is None and self.status.stage == "Closed":
        self.closed_date = timezone.now()
```
No verdict/comment check here.

**Remediation**  
1. **Document** in `docs/spec/data-model.md` and `docs/spec/api.md` that the close rule is enforced at the service layer (`apply_case_update`, `case_bulk_update`, `create_case`) and **does not** protect against direct ORM writes.
2. **Add a test** (`tests/conformance/test_case_verdict_close.py`) asserting that `Case.objects.filter(...).update(status=closed_status)` or `case.save()` after manually setting a Closed status creates a case with `verdict="undefined"` and no closing comment — confirming the bypass exists and is understood.
3. **Consider** a DB-level `CHECK` constraint (Postgres only) that requires `verdict IN ('true_positive','false_positive')` when `status.stage = 'Closed'` — but this requires a join to `CaseStatus` and is complex; the service-layer rule is the pragmatic enforcement point.

---

### L1 — Low — `ui-case-close` failure does not re-open the dialog (plan deviation D-N1)

**Where**  
`ui/views.py:1225-1252` — `case_close` catches `CaseUpdateError`, flashes the message, and redirects:
```python
except CaseUpdateError as exc:
    messages.error(request, exc.message)
    return _case_detail_redirect(request, case.number)  # no dialog re-open
```

**Impact**  
The plan (§9.3, AC-8 test description) promises: *"Failure → message + redirect back to General with the dialog re-rendered open."* The shipped behaviour is a flash message on the General tab with the dialog **closed**. The analyst must click "Close case" again to retry.

This is a **plan conformance deviation** (recorded as D-N1 in the verification report), not a security issue. The enforcement is correct; only the convenience affordance is missing.

**Remediation**  
Option A (implement): On `CaseUpdateError`, re-render `case_detail` with `autoopen=True` on the close modal (pass `case_close_modal_open=True` in context; `_modal.html` respects `data-autoopen="1"`).  
Option B (amend plan): Update `PLAN-2026-10-17-case-verdict-close-flow.md` §9.3 to match the shipped "flash + redirect" behaviour.  
Either is acceptable; pick one and close D-N1.

---

### L2 — Low — Unbounded tag list in `case_tag_add` dialog

**Where**  
`ui/views.py:1095-1097` builds `available_tags`:
```python
available_tags = (
    Tag.objects.exclude(id__in=case.tags.values_list("id", flat=True)).order_by("name"),
)
```
`ui/templates/ui/_case_tag_add_form.html:12-16` renders every tag as a checkbox.

**Impact**  
`Tag` is global (plan §11 caveat: "no org-scoping of Tag"). The list grows with every tag ever created in the platform. No pagination, no search, no cap. In a multi-org deployment with many tags, the dialog becomes unusable.

**Remediation**  
Add a reasonable cap (e.g., `MAX_TAG_PICKER = 200`) and a "load more" or search endpoint, or paginate the checkbox list. This is a UX/DoS hardening item, not a cross-org leak (tags are global by design).

---

### I1 — Info — AuditLog detail hygiene confirmed

All `AuditLog.objects.create()` calls in the reviewed surface (`cases/update.py:280-289`, `433-441`, `cases/create.py` via UI `case_create` at `ui/views.py:791-798`, `cases/views.py:1940`) pass `details` containing only:
- Field names and old/new values (string enum tokens, integers)
- `count` and `verdict` (token) for `case.alerts_closed`
- No `raw_payload`, no secrets, no PII beyond what the actor already sees

**Verdict:** Clean.

---

### I2 — Info — CWE-117 log forging avoided

`ui/views.py:194-200` (`not_viewer` decorator) logs `match.url_name` (server-controlled route name), not `request.path` (attacker-controllable via `%0A` injection). Correct.

---

## Explicit Checks Performed

| Check | Result | Evidence |
|-------|--------|----------|
| Org scoping on `ui-case-close` / `ui-case-field-edit` / `ui-case-tag-add` / `ui-case-tag-remove` | **PASS** | All four use `resolve_case(request.user, case_id, write=True)` → uniform 404 on cross-org / missing |
| `@not_viewer` + `@require_POST` on all four routes | **PASS** | Decorators present (lines 1222-1224, 1255-1257, 1283-1285, 1310-1312) |
| CSRF enforced (session auth, `{% csrf_token %}` in modal forms) | **PASS** | `_modal.html:91` includes `{% csrf_token %}`; `CsrfViewMiddleware` enabled |
| Close rule enforced on **every** write path (single PATCH, bulk, create-to-Closed, UI) | **PASS** | `enforce_close_rule` called from `apply_case_update` (315), `case_bulk_update` (1910), `create_case` (149); `compat/views.py` has no case-close endpoint |
| Verdict token validation at DB (CHECK constraint) | **PASS** | `cases/models.py:173` `in_values("case_verdict_valid", "verdict", CASE_VERDICT_VALUES)`; `alerts/models.py:158` same |
| Verdict token validation at service layer | **PASS** | `_CASE_ENUM_FIELDS["verdict"] = CASE_VERDICT_VALUES` (update.py:69); `_set_case_fields` validates (141-155) |
| Verdict token validation at UI (close dialog only offers closing verdicts) | **PASS** | `_case_close_form.html:11` iterates `closing_verdict_choices` = `CASE_CLOSING_VERDICTS` |
| `ui-case-tag-add` / `ui-case-tag-remove` org-scoped, no cross-org tag attach | **PASS** | `resolve_case(..., write=True)` gates; `Tag` is global by design (plan §11), so no cross-org leak possible |
| `propagate_close_to_alerts` atomic with case close + audit | **PASS** | Called at `apply_case_update:377` inside `@transaction.atomic` block (line 293); single `QuerySet.update()` |
| AuditLog written for every mutation (verdict, field edit, tag add/remove, close, alert propagation) | **PASS** | `_emit_tracked_changes` (433-441), `propagate_close_to_alerts` (280-289), `create_case` audit (ui/views.py:791-798), bulk (cases/views.py:1940) |
| `AuditLog.details` never stores secrets / raw payloads | **PASS** | Inspected all `details=` dicts — only field names, enum tokens, counts |
| Authorisation oracle / uniform 404 discipline | **PASS** | `resolve_case` returns `None` for cross-org and missing alike; viewers denied at scoping layer first (scoping.py:164) |
| N+1 / unbounded query in alert propagation | **FINDING M1** | `alert_ids = list(Alert.objects.filter(case=case).values_list("id", flat=True))` — no cap |
| Mass assignment / IDOR / injection / unsafe deserialisation | **PASS** | Service layer whitelists `CASE_SCALAR_FIELDS`; UI whitelists `CASE_INLINE_EDIT_FIELDS`; all params validated |
| CWE-117 log forging | **PASS (I2)** | `not_viewer` logs `match.url_name` |

---

## Not in Scope / Not Covered

| Item | Reason |
|------|--------|
| `compat/` TheHive API parity for verdict/close | Plan §6.5 explicitly declares "compat/ deliberately untouched" — no TheHive close verb exists to parity-test |
| `Tag` org-scoping | Plan §11 caveat: "No org-scoping of Tag (it is global by design)" — documented as known limitation |
| `CaseTemplate` verdict field | Plan §3 non-goal: "No verdict field on CaseTemplate" |
| Alert detail page verdict UI | Plan §3 non-goal: "No verdict UI on Alert detail page beyond the field being present in the payload" |
| Automation trigger on case close | Plan §3 non-goal: "No change to the automation/CaseStatusChanged trigger contract, and no new playbook dispatch on close" |
| `make css` / `make css-check` | Cannot run locally (arm64 binary); CSS classes are hand-written utilities in `app.css` component section |
| WebSocket / realtime propagation of alert closure | Covered by existing `append_timeline_event → publish_case_event` on-commit path; not re-reviewed here |
| SuperAdmin cross-org case close | `resolve_case` allows superAdmin with audit (`maybe_record_cross_org_read`); already verified in prior tenant-isolation audit |

---

## Commands Run for Verification

```bash
# Targeted conformance tests for the §12.8 surface
cd /Users/goebej/proggen/amalthea
.venv/bin/python -m pytest tests/conformance/test_case_verdict_close.py tests/conformance/test_ui_case_verdict.py -q
# → 49 passed in 6.69s

# Secret scan (tracked files)
scripts/secret-scan.sh --tracked
# → clean (317 tracked files)

# Lint / type gates
ruff check .
ruff format --check .
.venv/bin/python -m mypy --strict .
# → all clean
```

---

## Final Verdict

**CONDITIONAL PASS** — The feature meets its security requirements for org isolation, role gating, input validation, audit coverage, and close-rule enforcement on all intended write paths. The three conditions (M1, M2, L1) are hardening items that do not block the current threat model but should be addressed before the next release cut.
