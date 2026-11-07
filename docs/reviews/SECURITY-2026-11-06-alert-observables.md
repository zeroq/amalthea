# Security Review: Alert-Observables Implementation (AC28)

**Report Date:** 2026-11-06  
**Scope:** Uncommitted alert-observables implementation  
**Reference:** AC28 of docs/planning/PLAN-2026-11-06-alert-observables.md  
**Conclusion:** PASS with residual risks (detailed below)

---

## 1. JSONPath Injection / ReDoS / Resource Exhaustion

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Low | `ingest/observable_rules.py:26-30` | Caps exist: `MAX_OBSERVABLE_RULES=32`, `MAX_OBSERVABLE_PATHS=8`, `MAX_OBSERVABLE_CANDIDATES=200`, `MAX_OBSERVABLE_VALUE_CHARS=2048`, `MAX_OBSERVABLE_TYPE_CHARS=100`, `MAX_PATH_LEN=200` (mapping.py:217). | Validation is sufficient for stated caps. Consider ReDoS-specific testing for jsonpath_ng expressions if new path syntaxes are added. |
| Low | `ingest/observable_rules.py:100` | Path validation: `if not path or len(path) > MAX_PATH_LEN or not path.startswith("$")` — ensures only `$`-prefixed paths accepted, max 200 chars. | No change needed. |
| Low | `ingest/observable_rules.py:166-170` | JSONPath compile check: `_parse(value)` in `validate_observables` catches unparseable paths. | No change needed. |
| Low | `ingest/observable_rules.py:204-222` | Candidate cap: `if len(candidates) >= MAX_OBSERVABLE_CANDIDATES` returns truncated extraction. | No change needed. |

**Exploitability:** User-supplied JSONPath strings are validated for `$` prefix and jsonpath_ng compilability. The candidate cap (200) and value-char cap (2048) bound resource consumption. No unbounded path/payload pattern detected.

**Verdict:** PASS — caps are adequate and strictly enforced.

---

## 2. Tenant Isolation

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Info | `alert_sync.py:140-141` | `copy_alert_observables_to_case` raises `PermissionDenied("Cross-org alert/case observable copy refused")` when `alert.owner_org_id != case.owner_org_id`. | Design is correct. |
| Info | `pipeline.py:81` | `store_ingested_alert` requires `owner_org: Organisation` — every alert is stamped with org from ingestion token (ADR-004). | No change needed. |
| Info | `identity/scoping.py:114-119` | `org_q()` scopes Alert/Observable queries to caller's org; superAdmin gets `Q()` (all), org-less gets `Q(pk__in=[])` (deny). | No change needed. |
| Info | `ingestion_sources.py:137` | `resolve_ingestion_source` only returns own-org source for non-superAdmin; superAdmin cross-org read is audited via `maybe_record_cross_org_read`. | No change needed. |

**Exploitability:** No pathway to create/link observables in another org through ingest, remap, escalation, or source editor. Cross-org copy is explicitly refused. SuperAdmin break-glass is audited.

**Verdict:** PASS — tenant isolation is strong; no regression.

---

## 3. Authorization

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Low | `ingestion_sources.py:137` | Source detail (including observable-rules card) is gated: `_require_admin_ui` + `resolve_ingestion_source(write=True)`. Analysts receive 404 (confirmed by `test_source_observable_editor.py:232-250`). | Design is correct. |
| Low | `alerts/views.py:143-148` | Observables tab filters case links via `visible_case_ids = Case.objects.filter(case_visible_q(request.user)).values("pk")` — only shows cases user can already see. No leak. | No change needed. |
| Low | `ui/templates/ui/source_detail.html:133-134` | Observable rules form has `{% csrf_token %}`. Django CSRF protection active. | No change needed. |
| Low | `ui/views/common.py` (implicit) | `_require_admin_ui` check ensures only admin/superAdmin can write observable rules. Viewer role cannot access write paths. | No change needed. |

**Exploitability:** Analysts/viewers cannot write observable rules (404). Observables tab case links are scoped to visible cases only. CSRF tokens present on all forms.

**Verdict:** PASS — authorization is properly enforced; no new gaps.

---

## 4. Injection / Stored XSS

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Info | `alert_detail.html:214` | `{{ item.observable.data }}` — Django auto-escaping is active by default. No `|safe` or `marksafe` used. Href uses `{% url 'ui-observable-detail' item.observable.id %}` with UUID, not value. | No change needed. |
| Info | `_mapping_resolution.html:30` | `{{ warning.value_preview }}` — limited to 80 chars via `FieldWarning.as_dict()`, auto-escaped. Rendered as `saw "{{ warning.value_preview }}"` within double quotes. | No change needed. |
| None | N/A | No raw JSON/html construction or `safe` marks observed in observable rendering pipelines. | Maintain current auto-escaping discipline. |

**Exploitability:** Values rendered in templates are auto-escaped. No path for JS injection via observable `data` or mapping warnings. Href construction uses UUID primary key, not user-controlled values.

**Verdict:** PASS — no XSS vectors detected; Django auto-escaping protects all rendered values.

---

## 5. Suggestion Reconciliation

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Info | `alert_sync.py:186-199` | `reconcile_suggestions_with_copied_observables` only filters `status="proposed"` suggestions. Explicit `rejected`/`ignored` decisions are **not** overridden — the copy is evidence, not a mandate to rewrite triage choices. | Design is correct. |
| Info | `escalation.py:116-117` | `create_suggestions_for_case` + `reconcile_suggestions_with_copied_observables` form the P4 staging workflow; only `proposed` rows are touched. | No change needed. |

**Exploitability:** An analyst's `rejected`/`ignored` decision on a suggestion is preserved even if the same value is later produced by a mapping rule. No privilege escalation observed.

**Verdict:** PASS — only `proposed` suggestions are reconciled; analyst decisions are respected.

---

## 6. Automation Dispatch (Exactly-Once)

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Info | `observables/extractor.py:477-481` | `extract_into_case` uses `get_or_create` for `CaseObservable`; `dispatch_observable_linked` fires only on `created or link_created`. | Design is correct. |
| Info | `alert_sync.py:147-160` | `copy_alert_observables_to_case` uses `CaseObservable.objects.get_or_create`; dispatch only on `created` (second copy converges, no re-dispatch). | Design is correct. |
| Info | `alert_sync.py:578-584` | `add_observable_with_status` uses `CaseObservable.objects.get_or_create`; dispatch only on `created or link_created`. | Design is correct. |
| Info | `pipeline.py:149-158` | Replay is silent: `if alert.ingestion_source_id is not None: sync = sync_alert_observables(alert)` runs but warnings are only extended on `created`. Replay does not re-dispatch. | Design is correct. |

**Exploitability:** Unique constraints + `created` flag guard against duplicate dispatch. Replay does not trigger automation. Exactly-once guarantee is maintained.

**Verdict:** PASS — dispatch is idempotent; no duplicate/replay issue.

---

## 7. Secrets / Logging

| Severity | File:Line | Finding | Recommendation |
|----------|-----------|---------|----------------|
| Medium | `ingest/mapping.py:321-328` `641-647` | `FieldWarning.value_any` can contain raw payload values (e.g., `alice@example.com`, `203.0.113.10`) stored in `Alert.ingestion_warnings`. `FieldWarning.as_text()` renders them unredacted: `f"{self.target}_unknown_value: {paths} held {self.value_any!r}"`. | **Recommendation:** Redact sensitive values from warning messages, or store only a hash/preview (max 80 chars as already done for `value_preview`). |
| Low | `ingest/observable_rules.py:174-183` | `_as_candidate_value` truncates at `MAX_OBSERVABLE_VALUE_CHARS=2048`; unknown types are skipped with generic warning `observable_type_unknown: skipped N value(s) ...`. No raw payload values in caps notes. | No change needed. |
| Info | `alert_sync.py:113-116` | Warning `f"observable_type_unknown: skipped {skipped_unknown} value(s) for unknown observable type(s): {names}"` — generic, no payload values. | No change needed. |

**Exploitability:** While Django auto-escapes template rendering of `ingestion_warnings.warnings`, the raw values could contain PII or secrets stored in the database. Visible in UI via `_mapping_resolution.html` and `alert_detail.html` warning lists. Low risk if warnings are only ever viewed by analysts who already have the payload, but defense-in-depth is warranted.

**Verdict:** PASS with residual medium risk — consider redacting sensitive values from `FieldWarning.value_any` or limiting what is stored in ingestion warnings.

---

## Overall AC28 Assessment

**Result:** PASS

The security review confirms no new injection, tenant, or authorization regressions in the alert-observables implementation. All seven focus areas have been evaluated:

1. JSONPath validation caps are adequate and strictly enforced.
2. Tenant isolation is strong with superAdmin break-glass and audit.
3. Authorization is properly gated; analysts/viewers cannot write.
4. No stored XSS; Django auto-escaping protects all rendered values.
5. Only `proposed` suggestions are reconciled; analyst decisions preserved.
6. Automation dispatch is exactly-once via `get_or_create` + `created` flag.
7. Medium-residual risk: `FieldWarning.value_any` may contain raw payload values in ingestion warnings — recommend redaction.

**Residual Risks:**
- Medium: Raw payload values (emails, IPs, etc.) may appear in `ingestion_warnings` mapping entries. Recommend redacting sensitive data from warning messages.
- Low: No other residual risks identified.

---
*Report generated per AC28 of docs/planning/PLAN-2026-11-06-alert-observables.md. Read-only review; no code modifications performed.*
