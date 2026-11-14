"""Minimal CrewAI client for the Amalthea MCP server.

Run this in a Python 3.13 virtual environment; CrewAI 1.15.x does not support
Python 3.14. The Amalthea server can run in its normal Python 3.14 environment.

    python3.13 -m venv /tmp/amalthea-crewai
    /tmp/amalthea-crewai/bin/pip install 'crewai[mcp]==1.15.41'
    AMALTHEA_MCP_TOKEN=... /tmp/amalthea-crewai/bin/python examples/mcp/crewai_agent.py

The token is read from the environment and is never committed.
"""

import os

from crewai import Agent, Crew, Task
from crewai.mcp import MCPServerHTTP

token = os.getenv("AMALTHEA_MCP_TOKEN")
if not token:
    raise SystemExit("AMALTHEA_MCP_TOKEN is required")

base_url = os.getenv("AMALTHEA_MCP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

mcp_server = MCPServerHTTP(
    url=f"{base_url}/mcp",
    headers={"Authorization": f"Bearer {token}"},
    streamable=True,
    cache_tools_list=True,
)

analyst = Agent(
    role="SOC Analyst",
    goal="Triage security alerts using Amalthea case context",
    backstory=(
        "Read-only incident responder using the Amalthea MCP tools. "
        "Never performs state-changing actions through MCP."
    ),
    mcps=[mcp_server],
)

task = Task(
    description=(
        "Find cases related to destination IP 192.168.1.50 in the last 10 minutes. "
        "Summarize matching cases, severities, and linked observables. "
        "If nothing matches, say so."
    ),
    expected_output="A short summary of matching cases with ids, severities, and observables.",
    agent=analyst,
)

crew = Crew(agents=[analyst], tasks=[task], verbose=2)

if __name__ == "__main__":
    print(crew.kickoff())
