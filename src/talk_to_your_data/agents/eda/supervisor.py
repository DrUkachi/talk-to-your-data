"""The supervisor graph: every worker reports back to the supervisor, which decides
the next step. Routing is deterministic code (see `_route`): the LLM router that used
to live here only ever applied hard rules, and each hop cost a ~2s model call. The
topology is unchanged -- every worker still returns to the supervisor -- so adding a
step that genuinely needs judgment later just means making `_route` smarter.
"""

import asyncio
from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from talk_to_your_data.checkpointer import compiled_graph_async

from .analysis_agent import analyze
from .charting import build_chart, restrict_to_window
from .findings_store import save_finding
from .narrative_agent import write_finding
from .sql_agent import run_sql_agent
from .state import AgentState, AnalysisResult, SqlResult

MAX_TURNS = 8


def _route(state: AgentState) -> dict:
    """Deterministic routing -- no LLM. The old LLM router only ever applied these hard
    rules (plus one "skip analysis for single values" call that analysis_agent now
    makes itself via lens "none"), and cost 3-4 sequential calls (5-10s) per question."""
    if state.get("finding") is not None:
        return {"next": "FINISH", "reasoning": "finding exists"}
    if state.get("sql_result") is None:
        return {"next": "sql_agent", "reasoning": "no sql_result yet"}
    if state.get("analysis_result") is None:
        return {"next": "analysis_agent", "reasoning": "sql_result needs analysis"}
    return {"next": "narrative_agent", "reasoning": "write the finding"}


async def supervisor_node(state: AgentState) -> dict:
    turn = state.get("turn", 0) + 1
    if turn > state.get("max_turns", MAX_TURNS):
        return {"next": "FINISH", "turn": turn, "status": "failed"}
    decision = _route(state)
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
    # The semantic layer has no date filter, so sql_result holds every period. Narrow
    # the stored result to the window the answer reports, so the narrative, the chart,
    # the saved rows and any later follow-up ("total across those months") all agree.
    narrowed = restrict_to_window(sql_result, window)
    if window is not None and len(narrowed.rows) < len(sql_result.rows):
        note = f"\n-- rows narrowed after the query to period {window[0][:10]}..{window[1][:10]}"
        sql_result = narrowed.model_copy(update={"sql": sql_result.sql + note})
    chart = await asyncio.to_thread(
        build_chart,
        state["question"],
        sql_result,
        state["thread_id"],
        None,
        state.get("display_question"),
    )
    finding = await asyncio.to_thread(
        write_finding, state["question"], sql_result, analysis_result, chart_note=chart.note
    )
    finding.chart_ref = chart.path
    finding.chart_kind = chart.kind
    if state.get("display_question"):
        finding.question = str(state["display_question"])
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
