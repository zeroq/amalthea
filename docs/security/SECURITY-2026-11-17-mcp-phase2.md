# Security Review — Amalthea MCP Phase 2

**Date:** 2026-11-17
**Reviewer:** Security Auditor
**Scope:** Approval integrity, role/tenant boundary, prompt injection, summary safety, outbound/playbook abuse, audit/redaction, supply chain
**Status:** CONDITIONAL PASS

---
## 1. Verdict

**CONDITIONAL PASS** — The implementation meets the Phase 2 design objectives but contains 2 medium-severity findings that must be remediated before final verification.

Finding count: 2 Medium, 0 High, 0 Low (required fixes disclosed below).

---
## 2. Findings by Severity

### Medium Findings

#### [M-1] `transition_allowed()` medium-risk role check incorrectly denies admin/superAdmin
**File:** `mcp_server/services/proposals.py:79-88`
**Severity:** Medium

**Description:** The `transition_allowed()` function has a bug in the medium-risk role check where the `else:` branch at line 86 returns `False` when the user's role IS `"admin"` or `"superAdmin"`. This incorrectly denies admin/superAdmin from approving medium-risk proposals, contradicting the plan requirement (`PLAN-2026-11-17-mcp-phase2.md §5.3`, §7.1) that admin/superAdmin may approve medium-risk proposals.

The UI layer (`ui/views/approvals.py:25-43` `_check_role_decide()`) has the correct logic, but the service-layer state machine (`transition_allowed()`) has the inverse logic. A human reviewer using the UI would be correctly permitted, but the service function would reject their decision.

**Evidence:**
- `mcp_server/services/proposals.py:82-88`: `if role not in ("admin", "superAdmin"): ... else: return False` — the `else` executes when role IS admin/superAdmin, returning False
- `ui/views/approvals.py:40-43`: Correct logic: `if proposal.risk == "low": return is_admin(user)` / `# medium or high: admin or superAdmin; return is_admin(user) or getattr(user, "role", "") == "superAdmin"`

**Remediation:** Restructure the medium-risk branch in `transition_allowed()` to allow admin/superAdmin and only deny the requester for their own non-low proposal. The check should mirror the UI logic: deny if requester deciding own non-low; allow admin/superAdmin otherwise.

**Required fix:** Rewrite the `transition_allowed()` medium-risk section (lines 79-88) to match the UI `_check_role_decide()` enforcement pattern, ensuring admin/superAdmin can approve medium/high-risk proposals while the requester-never-their-own-non-low rule is enforced.

---

#### [M-2] Summary daily rate limit not implemented — only 1-per-5-min limit enforced
**File:** `mcp_server/services/summaries.py:91-93`
**Severity:** Medium

**Description:** The plan (`PLAN-2026-11-17-mcp-phase2.md §5.2`, §6.2) specifies rate limits of "1 summary per case per 5 minutes; 20 per credential per day." The 1-per-case-5-min limit is enforced via a DB query counting `TimelineEvent.objects.filter(kind="ai-summary", date__gte=five_min_ago)`. However, the 20-per-credential-per-day limit is documented only as a comment (`# Rate limit: 20 per credential per day (best-effort check)`) with no actual implementation — the code does not check or enforce a daily credential quota.

**Evidence:**
- `mcp_server/services/summaries.py:91-93`: `# Rate limit: 20 per credential per day (best-effort check) — In production: check Redis/cache for per-credential daily count. For now, we just validate the 1-per-case-5min limit`
- No code path checks per-credential daily count; rate limit effectively is 1 per case per 5 minutes only

**Remediation:** Implement the 20-per-credential-per-day rate limit, either via Redis cache counter or a DB-based daily tally. At minimum, add a guard that raises `ValidationError("rate_limited", "Rate limited: 20 summaries per credential per day.")` when the limit is exceeded.

**Required fix:** Add per-credential daily rate limit enforcement in `add_case_summary()`, e.g., cache-based count of `TimelineEvent.objects.filter(kind="ai-summary", actor=credential.user, date__gte=today_start).count()` or a Redis key `mcp:summary:cred:{credential_id}:day` with TTL reset at midnight.

---

### Low Findings (disclosed, not blocking)

#### [L-1] Viewer/read-only credentials could theoretically be granted `mcp:propose`/`mcp:summary` scopes
**File:** `mcp_server/models.py:130-132`
**Severity:** Low

The credential `clean()` method allows any of `PHASE1_SCOPES | PHASE2_SCOPES = ("mcp:read", "mcp:propose", "mcp:summary")` regardless of user role. The plan states "Viewer/read-only service users never receive `mcp:propose` or `mcp:summary`" (§5.3), but the model validation does not enforce this — it relies on the Settings UI to not grant these scopes to viewer-role users. This is a policy/configuration concern; the code structure is sound if the UI enforces the restriction.

#### [L-2] `_sanitise_markdown()` regex-based sanitisation may have edge cases
**File:** `mcp_server/services/summaries.py:150-174`
**Severity:** Low

The markdown sanitisation uses allowlist regex patterns (removing code blocks, HTML tags, image markup, attributes). This is sufficient for Phase 2 but is not a comprehensive sanitiser (e.g., could miss certain SVG/URL-based vectors). The design assumes the existing UI markdown rules are adequate for the threat model.

---
## 3. Residual Risks (Accepted)

- **Prompt injection via proposal content:** Proposal argument fields (e.g., `title`, `description`, `comment`) are free text, but the decision logic is role/risk/org-based, not content-dependent. Humans reviewing proposals in the UI see the raw text; the system does not derive approval decisions from content. This is an acceptable residual risk since the four-eyes admin gate and role checks prevent autonomous execution.

- **Summary text containing markdown links/citations:** The `post_case_summary` tool validates that `source_event_ids` all belong to the target case, and the markdown is sanitised. Malicious summary text could contain links, but these are rendered in the UI timeline with the `ai-summary` label and are read-only; they cannot change case state or trigger approvals.

- **Playbook run SSRF/abuse:** The `propose_playbook_run` tool and `run_playbook_service` correctly scope playbooks to the case's organisation and delegate to the existing guarded `run_now` executor with no free-form URL/header/script arguments. High-risk labelling and admin-only decision gate provide the required protection.

---
## 4. Required Remediations (Must-Fix Before Verification)

| # | File | Finding | Fix |
|---|------|---------|-----|
| 1 | `mcp_server/services/proposals.py:79-88` | `transition_allowed()` medium-risk role check incorrectly denies admin/superAdmin | Restructure the medium-risk branch to allow admin/superAdmin and deny only requester-deciding-their-own-non-low, matching the UI `_check_role_decide()` logic |
| 2 | `mcp_server/services/summaries.py:91-93` | 20-per-credential-per-day rate limit not implemented | Add per-credential daily rate limit enforcement (cache or DB-based) and raise `rate_limited` error when exceeded |

Both fixes are required before the verifier can record `READY — PASS`. No code changes may be committed/pushed; fixes should be applied to the working tree and retested locally.

---
## 5. Supply Chain

- No new third-party dependencies introduced in Phase 2; all code uses existing Django/models/sdk constructs.
- MCP Python SDK 2.3.0 remains the pinned dependency (unchanged from Phase 1).
- Secret hygiene: the repo is public; `scripts/secret-scan.sh --staged --tracked --history` is enforced via pre-commit hooks. No secrets in new code.

---
**End of Document**
