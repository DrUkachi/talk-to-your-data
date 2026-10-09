"""The supervisor graph: every worker reports back to the supervisor, which decides
the next step -- not a fixed SQL -> analysis -> narrative pipeline. Only one of its
three decisions is genuinely non-trivial (whether this question needs analysis_agent
at all, e.g. a trend/breakdown question does, a simple point-value lookup doesn't);
the other two (must start with sql_agent, must end with narrative_agent before
FINISH) are hard constraints stated in the prompt. Kept uniform anyway -- every
worker returns to the supervisor, including the trivial cases -- because that's
what makes this a genuine supervisor topology rather than Python if/else wearing
an LLM costume for the one real decision.
"""

import asyncio
from contextlib import AbstractAsyncContextManager

from langfuse import observe
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from talk_to_your_data.checkpointer import compiled_graph_async
from talk_to_your_data.llm import (
    ToolSpec,
    first_tool_call,
    get_client,
    get_model,
    reasoning_config,
    to_openai_tool,
)
from talk_to_your_data.tracing import record_generation

from .analysis_agent import analyze
from .charting import build_chart
from .findings_store import save_finding
from .narrative_agent import write_finding
from .sql_agent import run_sql_agent
from .state import AgentState, AnalysisResult, SqlResult

MAX_TURNS = 8

ROUTE_TOOL: ToolSpec = {
    "name": "route",
    "description": "Decide which agent handles the next step of answering this question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "next": {
                "type": "string",
                "enum": ["sql_agent", "analysis_agent", "narrative_agent", "FINISH"],
            },
            "reasoning": {"type": "string"},
        },
        "required": ["next", "reasoning"],
    },
}

ROUTING_RULES = (
    "Hard rules, not judgment calls:\n"
    "- No sql_result yet -> next MUST be sql_agent.\n"
    "- A finding already exists -> next MUST be FINISH.\n"
    "- sql_result exists but no finding yet, and analysis wasn't just skipped by you "
    "-> next MUST be narrative_agent, UNLESS this question genuinely needs analysis "
    "first (see below).\n\n"
    "The one real judgment call: once sql_result exists and there's no analysis_result "
    "yet, decide whether analysis_agent adds anything. Use it for trend-over-time, "
    "period comparison, or category-breakdown questions. Skip it (go straight to "
    "narrative_agent) for a simple single-value lookup -- analysis would add nothing."
)


@observe(name="supervisor_route", as_type="generation")
def _route(state: AgentState) -> dict:
    client = get_client()
    model = get_model()
    prompt = (
        f"{ROUTING_RULES}\n\n"
        f"Question: {state['question']}\n"
        f"Has sql_result: {state.get('sql_result') is not None}\n"
        f"Has analysis_result: {state.get('analysis_result') is not None}\n"
        f"Has finding: {state.get('finding') is not None}\n"
        "You must call route to respond."
    )
    response = client.responses.create(
        model=model,
        max_output_tokens=8192,
        reasoning=reasoning_config(),
        tools=[to_openai_tool(ROUTE_TOOL)],
        input=[{"role": "user", "content": prompt}],
    )
    record_generation(response)
    tool_use = first_tool_call(response)
    if tool_use is None:
        raise RuntimeError("supervisor: model did not call route")
    return dict(tool_use.arguments)


async def supervisor_node(state: AgentState) -> dict:
    turn = state.get("turn", 0) + 1
    if turn > state.get("max_turns", MAX_TURNS):
        return {"next": "FINISH", "turn": turn, "status": "failed"}
    decision = await asyncio.to_thread(_route, state)
    update: dict = {"next": decision["next"], "turn": turn}
    # Only set "routing" while there's still work ahead -- otherwise this
    # overwrites narrative_agent's "done" on the final pass back through here.
    if decision["next"] != "FINISH":
        update["status"] = "routing"
    return update


def _route_from_supervisor(state: AgentState) -> str:
    next_step = state["next"]
    return END if next_step == "FINISH" else next_step


async def sql_agent_node(state: AgentState) -> dict:
    result = await run_sql_agent(state["question"])
    return {"sql_result": result.model_dump(mode="json")}


async def analysis_agent_node(state: AgentState) -> dict:
    sql_result = SqlResult.model_validate(state["sql_result"])
    result = await asyncio.to_thread(analyze, state["question"], sql_result)
    return {"analysis_result": result.model_dump(mode="json")}


async def narrative_agent_node(state: AgentState) -> dict:
    sql_result = SqlResult.model_validate(state["sql_result"])
    analysis_result = (
        AnalysisResult.model_validate(state["analysis_result"])
        if state.get("analysis_result")
        else None
    )
    stats = analysis_result.stats if analysis_result else {}
    window = (
        (str(stats["first_period"]), str(stats["last_period"]))
        if stats.get("first_period") and stats.get("last_period")
        else None
    )
    chart = await asyncio.to_thread(
        build_chart, state["question"], sql_result, state["thread_id"], window
    )
    finding = await asyncio.to_thread(
        write_finding, state["question"], sql_result, analysis_result, chart_note=chart.note
    )
    finding.chart_ref = chart.path
    await asyncio.to_thread(save_finding, state["thread_id"], finding, sql_result)
    return {"finding": finding.model_dump(mode="json"), "status": "done"}


def build_graph(checkpointer: AsyncPostgresSaver) -> CompiledStateGraph:
    g: StateGraph = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("sql_agent", sql_agent_node)
    g.add_node("analysis_agent", analysis_agent_node)
    g.add_node("narrative_agent", narrative_agent_node)

    g.set_entry_point("supervisor")
    g.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {
            "sql_agent": "sql_agent",
            "analysis_agent": "analysis_agent",
            "narrative_agent": "narrative_agent",
            END: END,
        },
    )
    g.add_edge("sql_agent", "supervisor")
    g.add_edge("analysis_agent", "supervisor")
    g.add_edge("narrative_agent", "supervisor")

    return g.compile(checkpointer=checkpointer)


def eda_graph() -> AbstractAsyncContextManager[CompiledStateGraph]:
    return compiled_graph_async(build_graph)
