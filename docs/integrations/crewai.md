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

The server currently accepts `mcp:read` only; Phase 1 tools are read-only.

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
3. **Bearer scope:** the static token carries only the scopes stored on its `McpCredential`. In
   Phase 1 that is `mcp:read`.
4. **No server-side LLM:** the model runs in CrewAI. Amalthea stores no model keys and makes no LLM
   calls.
5. **DNS-rebinding protection:** the Phase 0 MCP app currently allows only localhost hosts by
   default. Phase 1 must configure the production host/origin allowlist before exposure.

## Verification status

The Phase 0 ASGI/protocol smoke is covered by `tests/conformance/test_mcp_phase0.py`. The live
CrewAI smoke discovered 13 tools and called `list_cases(limit=5)` successfully over Streamable
HTTP; the test credential was revoked. It remains an external check because of the Python version
constraint above.
