# CrewAI + Amalthea MCP Integration

**Target client:** CrewAI 1.15.41
**Server SDK:** MCP Python SDK 2.3.0
**Status:** verified 2026-11-16 with CrewAI 1.15.41 in an isolated Python 3.13 environment against
Daphne 4.2.3. CrewAI requires Python `<3.14`; the Amalthea runtime uses Python 3.14.6, so keep
CrewAI out of the runtime requirements.

## Overview

This integration shows a minimal CrewAI crew connecting to the Amalthea MCP server over
**Streamable HTTP** with a static bearer header. CrewAI's `MCPServerHTTP` discovers tools from
`/mcp`, then exposes them to the agent. The token is read from `AMALTHEA_MCP_TOKEN`; nothing is
committed.

## Runtime requirement

CrewAI 1.15.x declares `requires-python >=3.10,<3.14`. Amalthea's server and test suite run on
Python 3.14.6, so do not add CrewAI to the Amalthea runtime requirements. Run the client smoke in a
separate Python 3.13 virtual environment:

```bash
python3.13 -m venv /tmp/amalthea-crewai
/tmp/amalthea-crewai/bin/pip install 'crewai[mcp]==1.15.41'
```

## Configuration

| Variable | Description |
|---|---|
| `AMALTHEA_MCP_TOKEN` | Bearer token for MCP authentication (shown once at creation) |
| `AMALTHEA_MCP_BASE_URL` | Base URL of the MCP server (default: `http://127.0.0.1:8000`) |

The server accepts `mcp:read`, `mcp:propose` and `mcp:summary`. Phase 1 read tools require
`mcp:read`; Phase 2 write-adjacent tools require the narrower scopes described below.

## Minimal example

```python
from crewai import Agent, Crew, Task
from crewai.mcp import MCPServerHTTP

mcp_server = MCPServerHTTP(
    url="http://127.0.0.1:8000/mcp",
    headers={"Authorization": f"Bearer {token}"},
    streamable=True,
    cache_tools_list=True,
)

analyst = Agent(
    role="SOC Analyst",
    goal="Triage security alerts using Amalthea case context",
    backstory="Read-only incident responder using the Amalthea MCP tools.",
    mcps=[mcp_server],
)

task = Task(
    description="Find cases related to 192.168.1.50 and summarize the evidence.",
    expected_output="A short summary with case ids, severities, and observables.",
    agent=analyst,
)

crew = Crew(agents=[analyst], tasks=[task], verbose=2)
print(crew.kickoff())
```

`mcps=[...]` is the current CrewAI configuration path. The older `crewai_tools.MCPServerAdapter`
API is not used here.

## Caveats

1. **Version drift:** CrewAI's MCP API has changed across releases. Pin `crewai==1.15.41` for the
   smoke and re-check the import path before upgrading.
2. **Tool cache:** `cache_tools_list=True` is intended for stable deployments. Use `False` while
   developing tools or changing scopes.
3. **Bearer scope:** the static token carries only the scopes stored on its `McpCredential`.
   Use separate credentials for read, propose and summary agents. Reserved `mcp:write` and
   `mcp:admin` scopes are rejected.
4. **No server-side LLM:** the model runs in CrewAI except for the explicit `post_case_summary`
   tool, which accepts caller-generated markdown and stores it with provenance; Amalthea stores no
   model keys and makes no LLM calls.
5. **DNS-rebinding protection:** the Phase 0 MCP app currently allows only localhost hosts by
   default. Production must configure the host/origin allowlist before exposure.

## Phase 2 scopes and tools

| Scope | Tools | Mutation model |
|---|---|---|
| `mcp:read` | Phase 1 read tools | None |
| `mcp:propose` | `propose_*`, `get_action_proposal` | Creates a proposal row only. A human approves in **Approvals**, then executes explicitly. |
| `mcp:summary` | `post_case_summary` | Appends one labelled `ai-summary` timeline event with `sourceEventIds`, `credentialId`, `requestId` and `contentHash`. Human verification is required. |

A credential may carry more than one allowed scope, but viewer service users can only receive
`mcp:read`. Every tool is organisation-bound through the credential; there is no cross-organisation
path, including for `superAdmin`.

### Proposal workflow

1. Agent calls e.g. `propose_case_comment({"case_id": "...", "markdown": "..."})`.
2. Amalthea stores the exact validated arguments, SHA-256 hash, risk, expiry (24h) and idempotency
   key. The returned `proposalId` is used with `get_action_proposal`.
3. An eligible human reviews the exact arguments in **Approvals**. Low risk may be decided by
   analyst/admin/superAdmin; medium/high by admin/superAdmin. Requesters cannot decide their own
   non-low proposal.
4. After approval, an eligible human executes the proposal. Execution rechecks expiry, hash,
   target and organisation, then calls the same shared domain service as the UI. Terminal rows
   remain as audit evidence.

External MCP arguments use snake_case (`case_id`, `comment_markdown`). The persisted proposal
arguments and internal service payloads use camelCase (`caseId`, `markdown`); this is an explicit
wire-boundary deviation recorded in the plan.

### Summary workflow

`post_case_summary` accepts `case_id`, `markdown` and 1–100 `source_event_ids`. It is rate-limited
to 1 per case per 5 minutes and 20 per credential per day. The stored event title is
`AI-generated case summary`; its metadata identifies the generating credential and source events.
The agent/user must verify the summary against the cited ledger entries.

## Verification status

The Phase 0 ASGI/protocol smoke is covered by `tests/conformance/test_mcp_phase0.py`. The live
CrewAI smoke discovered 13 tools and called `list_cases(limit=5)` successfully over Streamable
HTTP; the test credential was revoked. It remains an external check because of the Python version
constraint above.

Phase 2 tool/scope behaviour is covered by `tests/conformance/test_mcp_*` and
`tests/conformance/test_approvals_ui.py`. A fresh live CrewAI discovery smoke should be rerun
after a Phase 2 credential is minted with the narrower scope; the token must be revoked afterwards.
