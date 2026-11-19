# MCP Approval Workflow

## Human approval inbox (`/approvals`)

The `/approvals` endpoint lists pending `McpActionProposal` rows for the credential's organisation.
The inbox enforces role-based four-eyes rules so that no single user can unilaterally approve or
execute high-risk proposals.

### Role matrix

| Risk level | Who may approve | Who may execute |
|---|---|---|
| `low` | Analyst or Admin | Same as approve (analyst/admin) |
| `medium` | Admin or superAdmin | Same as approve (admin/superAdmin) |
| `high` | Admin or superAdmin only | Same as approve (admin/superAdmin) |

**Requester never decides their own non-low proposal.** Even the proposal requester cannot approve
or execute a `medium` or `high` proposal; only org-level admins/superAdmins may do so.

### Decision flow

1. **List** `GET /approvals` — shows pending/recent proposals for the org with target type/id, risk,
   requester, expiry, and status.
2. **Detail** `GET /approvals/<uuid:proposal_id>` — shows exact intended-change preview: tool name,
   action, arguments, target, risk, requester, expires_at, status, and the proposed change (e.g.
   "close case with verdict X").
3. **Approve/decline** `POST /approvals/<uuid:proposal_id>/decide` — body: `{ "action": "approve" |
   "decline", "note": "optional human note" }`.
   - Transitions `pending -> approved|declined|expired`.
   - `select_for_update()` on the row; stale or expired rows fail closed.
   - A declined proposal is terminal; it cannot be re-approved or re-executed.

4. **Execute** `POST /approvals/<uuid:proposal_id>/execute` — body: `{}` (no arguments; the executor
   re-resolves the target and re-checks all authorisation).
   - Only available after the proposal has been approved.
   - Follows the same role checks as approve.
   - Executes the shared domain service (e.g. `apply_case_update()`, `import_alert_to_case()`),
     writes bounded `result` JSON, appends one `TimelineEvent` (kind=`ai-summary` or
     `proposal-executed`), and publishes one Channels update.

### State summarised

| Proposal status | Visible in inbox | Can be approved | Can be executed |
|---|---|---|---|
| `pending` | Yes | Yes (role-qualified) | No (must approve first) |
| `approved` | Yes | No | Yes (POST execute) |
| `declined` | Yes | No | No (terminal) |
| `expired` | Yes | No | No (terminal) |
| `executed` | Yes | No | No (terminal) |
| `failed` | Yes | No | No (terminal) |

### Idempotency and replay behaviour

- **Idempotency key**: deterministic per credential + tool + target + canonical arguments. Unique
  constraint prevents duplicate proposal rows. Replaying the same proposal returns the original
  terminal result; it never executes twice.
- **Stale or expired rows**: `select_for_update()` on approve/decode/execute; if the row's hash,
  status, or expiry has changed, the operation fails closed with a stable error.
- **Execution result is terminal**: a successful execution appends one ledger event and one Channels
  update. Re-executing the same proposal after success is a no-op (the original result is returned).

### Summary limits (post_case_summary only, not proposal tools)

- **1 per case per 5 minutes**: a second attempt within the window returns the existing `ai-summary`
  event and `200 OK`.
- **20 per credential per day**: a daily counter enforced server-side; excess returns `rate_limited`.
- **1–100 source events**: `source_event_ids` must be a non-empty list of 1–100 timeline event IDs,
  all belonging to the same `case_id`. Cross-case or nonexistent IDs are rejected with `400 invalid_arguments`.

## Approval rules (four-eyes)

- **low risk**: analyst or admin may approve/decline.
- **medium/high risk**: admin or superAdmin only.
- **Requester exclusion**: the user who created the proposal can never approve or execute it if the
  risk is not `low`. This applies even if the requester is an admin of a different organisation unit.
- **Execute follows decide**: the execute endpoint enforces exactly the same role checks as the decide
  endpoint. An approved `low` risk proposal by an analyst can be executed by that same analyst; an
  approved `medium` risk proposal can only be executed by an admin or superAdmin.

## No native dialogs

- Confirmation modals use the project's custom `<dialog>` pattern (Esc to close, Tab trap), not
  browser-native `confirm()`/`alert()`/`prompt()`.
- All actions are reachable via keyboard; HTMX POST fallbacks work without JavaScript.

## Role scoping

- Viewer/read-only service users cannot see or interact with `/approvals`.
- `superAdmin` is scoped to the selected organisation; cross-org superAdmin access is not granted.
- The `/approvals` inbox is org-scoped only.