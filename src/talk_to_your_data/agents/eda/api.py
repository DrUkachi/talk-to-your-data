"""FastAPI service for the EDA supervisor: a plain POST /ask for our own testing
and (Phase 6) the Slackbot, plus A2A routes (agent card + JSON-RPC) for external
agent-to-agent callers. Both paths are thin adapters around the same
ask_question() -- see ask.py.
"""

import os
from typing import Any

import a2a.types as a2a_types
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .a2a_executor import EdaAgentExecutor
from .ask import ask_question
from .findings_store import get_finding, list_findings
from .followup_agent import answer_followup

app = FastAPI(title="talk-to-your-data EDA agent")

# Must be the externally-reachable address of *this* service -- an A2A client
# uses this URL verbatim to send requests, it does not resolve it relative to
# wherever it fetched the agent card from. Confirmed by hitting this with a real
# a2a.client.Client: a relative "/a2a" value 400s with "missing http(s) protocol".
EDA_API_BASE_URL = os.environ.get("EDA_API_BASE_URL", "http://127.0.0.1:8002")

AGENT_CARD = a2a_types.AgentCard(
    name="talk-to-your-data EDA agent",
    description=(
        "Ask a business question about the Olist e-commerce dataset in plain "
        "English; get back an answer grounded in the semantic layer, with the "
        "SQL used and caveats stated explicitly."
    ),
    version="0.1.0",
    supported_interfaces=[
        a2a_types.AgentInterface(
            url=f"{EDA_API_BASE_URL}/a2a", protocol_binding="JSONRPC", protocol_version="0.3"
        )
    ],
    capabilities=a2a_types.AgentCapabilities(streaming=False, push_notifications=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        a2a_types.AgentSkill(
            id="ask_business_question",
            name="Ask a business question",
            description=(
                "Answers a natural-language question about orders, revenue, "
                "reviews, or delivery times."
            ),
            tags=["analytics", "sql", "e-commerce"],
            examples=[
                "What was total revenue from delivered orders?",
                "How did monthly revenue trend in 2017?",
                "Which product categories bring in the most revenue?",
            ],
        )
    ],
)

_request_handler = DefaultRequestHandler(
    agent_executor=EdaAgentExecutor(), task_store=InMemoryTaskStore(), agent_card=AGENT_CARD
)

add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(AGENT_CARD),
    jsonrpc_routes=create_jsonrpc_routes(_request_handler, rpc_url="/a2a", enable_v0_3_compat=True),
)


class AskRequest(BaseModel):
    question: str
    thread_id: str | None = None


@app.post("/ask")
async def ask(req: AskRequest) -> dict[str, Any]:
    try:
        finding = await ask_question(req.question, thread_id=req.thread_id)
    except Exception as e:  # noqa: BLE001 -- surfaced as a clean 502, not a bare 500;
        # matches a2a_executor's "report as text, don't crash the adapter" pattern.
        raise HTTPException(502, f"couldn't answer that: {e}") from e
    return finding.model_dump(mode="json")


class FollowupRequest(BaseModel):
    question: str


@app.get("/findings")
def get_findings(limit: int = 20) -> list[dict[str, Any]]:
    return list_findings(limit=limit)


@app.get("/findings/{finding_id}")
def get_finding_by_id(finding_id: str) -> dict[str, Any]:
    finding = get_finding(finding_id)
    if finding is None:
        raise HTTPException(404, f"no finding with id '{finding_id}'")
    return finding


@app.post("/findings/{finding_id}/followup")
async def followup(finding_id: str, req: FollowupRequest) -> dict[str, Any]:
    try:
        finding = await answer_followup(finding_id, req.question)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001 -- same "clean 502, not a bare 500" fix as /ask
        raise HTTPException(502, f"couldn't answer that: {e}") from e
    return finding.model_dump(mode="json")
