# Security Audit Report — Amalthea MCP Phase 2 (Final Re-audit)

**Date:** 2026-11-19
**Scope:** MCP Phase 2 re-audit per conditional findings M-1 and M-2, plus full scope verification
**Previous verdict:** CONDITIONAL PASS (2026-11-17) with M-1 and M-2 as open medium findings
**Current verdict:** PASS — both prior conditional findings are closed; no new findings

---
## 1. Status of Prior Conditional Findings

### [M-1] Role/Approval Bug — **CLOSED**

**Previous description:** The `transition_allowed()` function had a bug in the medium-risk role check where the `else:` branch returned `False` when the user's role WAS `"admin"` or `"superAdmin"`, incorrectly denying admin/superAdmin from approving medium-risk proposals.

**Previous evidence:** `mcp_server/services/proposals.py:82-88` — `if role not in ("admin", "superAdmin"): ... else: return False`

**Verification:** The current `_decision_role_allowed()` function in `mcp_server/services/proposals.py:62-77` has been restructured and correctly implements the four-eyes/role rules:

```python
def _decision_role_allowed(proposal: McpActionProposal, user: User | None) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "role", "") == "viewer":
        return False
    if user.org_id != proposal.organisation_id:
        return False
    if proposal.requested_by_id == user.id and proposal.risk != "low":
        return False  # requester cannot decide own non-low
    if proposal.risk in ("medium", "high"):
        return user.role in ("admin", "superAdmin")  # ✅ admin/superAdmin CAN approve
    if proposal.risk == "low":
        return user.role in ("analyst", "admin", "superAdmin")
    return False
```

**Role rule matrix verified:**
| Risk | Who may decide | Implementation |
|------|---------------|----------------|
| `low` | analyst, admin, superAdmin | `return user.role in ("analyst", "admin", "superAdmin")` |
| `medium` | admin, superAdmin only | `return user.role in ("admin", "superAdmin")` |
| `high` | admin, superAdmin only | `return user.role in ("admin", "superAdmin")` |
| requester own non-low | **always denied** | `proposal.requested_by_id == user.id and proposal.risk != "low"` returns `False` |
| viewer/read-only | **always denied** | `getattr(user, "role", "") == "viewer"` returns `False` |

**UI-layer consistency:** `ui/views/approvals.py:_check_role_decide()` (lines 26-47) has identical logic, confirming server-client parity.

**Verdict:** M-1 is **CLOSED**. The prior bug is fixed; role rules match the plan (§7.1, §5.3) and the UI layer.

---

### [M-2] Summary Daily Quota/Rate Limiting — **CLOSED**

**Previous description:** The 20-per-credential-per-day rate limit was documented only as a comment with no actual enforcement code — only the 1-per-case-5-min limit was implemented.

**Previous evidence:** `mcp_server/services/summaries.py:91-93` — `# Rate limit: 20 per credential per day (best-effort check)` with no code path checking per-credential daily count.

**Verification:** The current `add_case_summary()` function in `mcp_server/services/summaries.py:137-144` implements the daily credential quota:

```python
daily_key = f"mcp:summary:cred:{credential.id}:day"
cache.add(daily_key, 0, timeout=86_400)
daily_count = int(cache.get(daily_key, 0))
if daily_count >= 20:
    raise ValidationError("Rate limited: 20 summaries per credential per day.", code="rate_limited")
cache.incr(daily_key)
```

**Rate limit matrix verified:**
- **1 per case per 5 minutes:** Enforced via `TimelineEvent.objects.filter(case=case, kind=_KIND, date__gte=five_min_ago)` (line 132-135)
- **20 per credential per day:** Enforced via Redis cache key `mcp:summary:cred:{credential.id}:day` with TTL of 24 hours (line 137-144)

**Verdict:** M-2 is **CLOSED**. The 20-per-credential-per-day rate limit is implemented and functional.

---
## 2. Full Scope Verification

### org scoping and no superAdmin cross-org path; uniform not_found behavior
- ✅ All proposal tools check `owner_org_id=principal.organisation_id` (`mcp_server/tools/proposals.py`)
- ✅ `execute_proposal` re-resolves target with `owner_org=org` (`mcp_server/services/proposals.py:_resolve_target`)
- ✅ `/approvals` list filtered by `request.user.org_id` (`ui/views/approvals.py:69-72`)
- ✅ `/approvals` detail returns uniform 404 for cross-org access (`ui/views/approvals.py:116-117`)
- ✅ `get_action_proposal` only returns proposals owned by credential's organisation (`mcp_server/tools/proposals.py:933-936`)
- ✅ Uniform `not_found`/404 for cross-tenant or invisible targets (no superAdmin cross-org path)

### role rules for low/medium/high proposals; requester cannot approve own non-low proposal
- ✅ `_decision_role_allowed` correctly enforces all role rules (verified above)
- ✅ `_check_role_decide` in UI layer is identical
- ✅ `proposal.requested_by_id == user.id and proposal.risk != "low"` denies requesters
- ✅ `is_viewer(user)` denies viewer/read-only from all decision/execute paths

### proposal immutability, expiry, idempotency, arguments_hash, audit rows, terminal retention
- ✅ `McpActionProposal.save()` enforces arguments immutability after first save and computes `arguments_hash` (lines 401-415)
- ✅ `arguments_hash` validated on both `save()` and `clean()` (lines 443-453)
- ✅ `is_expired` property checks `timezone.now() >= self.expires_at` (line 458-459)
- ✅ `is_terminal` property checks for DECLINED/EXPIRED/EXECUTED/FAILED (lines 462-468)
- ✅ Unique `idempotency_key` constraint prevents duplicate proposal rows (model Meta, line 390-394)
- ✅ Audit rows created on: created, approved, declined, expired, executed, failed (via `_audit()` helper, lines 96-118)
- ✅ Idempotency: duplicate creation returns existing proposal row instead of raising IntegrityError

### executor revalidation of auth/state before calling shared services; no direct model mutation outside allowed summary path
- ✅ `execute_proposal()` re-reads proposal with `select_for_update()` (line 203)
- ✅ Re-checks expiry (`proposal.is_expired`, line 210)
- ✅ Re-validates `arguments_hash` mismatch (line 212)
- ✅ Re-checks role authorization (`_decision_role_allowed`, line 214)
- ✅ Re-resolves target (`_resolve_target`, line 217) and checks org match (`_check_org_target_match`, line 218)
- ✅ Re-checks target state (`_check_target_state`, line 220)
- ✅ All domain mutations go through shared services: `apply_alert_triage()`, `apply_case_update()`, `add_observable_with_status()`, `create_task()`, `import_alert_to_case()`, `run_playbook_service()` (`mcp_server/services/proposals.py:336-418`)
- ✅ `post_case_summary` is the **only** direct write path; it only appends an `ai-summary` timeline event and cannot change case state

### summary only direct write: append-only ai-summary event, sanitisation, source-event validation, provenance, 1/case/5min and 20/credential/day quotas
- ✅ Validates `case_id` belongs to credential org (line 65-69)
- ✅ Validates `summary_markdown` is 1-20,000 chars, rejects raw HTML and images (lines 71-77)
- ✅ Validates `source_event_ids` are 1-100 IDs, all belonging to the target case (lines 79-95)
- ✅ Sanitises Markdown via `render_markdown()` (line 97) — UI markdown rules, allowlist-based
- ✅ Provenance metadata: `generatedBy`, `credentialId`, `credential_name`, `requestId`, `sourceEventIds`, `contentHash` (lines 153-161)
- ✅ Rate limit 1/case/5min enforced (lines 131-135)
- ✅ Rate limit 20/credential/day enforced (lines 137-144)
- ✅ Duplicate content returns existing event (lines 110-129)
- ✅ Returns `{caseId, eventId, createdAt, label: "ai-generated", requiresHumanVerification: true}` (lines 164-170)
- ✅ Cannot set status, severity, owner, tags, tasks, observables, playbooks, or closure (plan §6.2, line 233)

### no raw payload/attachment exposure in tools/results/audit
- ✅ `McpInvocation.arguments_redacted` uses `audit_arguments()` allowlist (limits.py:61-99) — only permitlisted fields are included
- ✅ `_bounded_result()` recursively bounds result data, never including raw payloads or unbounded text (lines 426-445)
- ✅ Audit rows contain no secrets, raw payloads, or unbounded text

### secret hygiene in all changed/untracked files
- ✅ No new secrets, API keys, tokens, or passwords in any code, test, fixture, or doc files
- ✅ Repo is public; `scripts/secret-scan.sh` enforced via pre-commit/hooks on every commit/push/CI
- ✅ Local `.env` is gitignored; `settings/prod.py` fails closed without strong `DJANGO_SECRET_KEY` and non-empty `DJANGO_ALLOWED_HOSTS`

### no native confirm()/alert()/prompt() in approvals UI; custom <dialog> modals present
- ✅ `ui/views/approvals.py` contains no native dialog calls
- ✅ `test_approvals_ui.py::test_no_native_dialogs` checks HTML does NOT contain `window.confirm`, `alert(`, or `prompt(` (lines 325-328)
- ✅ `test_approvals_ui.py::test_modal_uses_native_dialog_element` checks for `data-modal-` or `modal` in content (lines 339-341)
- ✅ UI uses `<dialog>` modal pattern from `_modal.html` across all approval pages

### wire boundary and unsafe-pattern checks are coherent with recorded deviations
- ✅ All checks are coherent with `docs/spec/deviations.md` (MCP2-1 … MCP2-5)
- ✅ No new deviations introduced; prior deviations (F2, F9, etc.) from earlier waves are unrelated and already resolved
- ✅ Prompt injection via proposal content: decision logic is role/risk/org-based, not content-dependent (accepted residual risk)
- ✅ Summary text containing markdown links/citations: rendered with `ai-summary` label, read-only, cannot change case state or trigger approvals

---
## 3. New Findings (Severity: None — all prior findings resolved)

No new security findings were introduced. The two prior conditional findings M-1 and M-2 are both **CLOSED** with fixes verified in the current codebase.

---
## 4. Report Path

`docs/security/SECURITY-2026-11-19-mcp-phase2-final.md`

---
## 5. Verdict

**PASS** — All prior conditional findings (M-1, M-2) are closed. The MCP Phase 2 implementation meets all design objectives and security requirements. No new findings. The verifier can proceed to record `READY — PASS` and close TODO §22.4 (pending full suite run, which the user owns).
