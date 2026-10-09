"""Protocol-agnostic entry point: ask a question, get a Finding. The plain
POST /ask endpoint, the A2A executor, and (Phase 6) the Slackbot all call this --
none of them reimplement the supervisor loop, they just adapt its input/output
to their own protocol.

Out-of-scope questions are refused here, before the graph ever starts -- see
guardrails/scope_guard.py. This never touches AgentState/the supervisor at all,
so a refusal is transparent to every adapter without any of them needing to
know a refusal path exists.
"""

import asyncio
import contextlib
import uuid

from langgraph.types import RunnableConfig

from talk_to_your_data.guardrails.scope_guard import check_scope, scope_gate

from .findings_store import ensure_findings_table, save_finding
from .state import Finding, SqlResult
from .supervisor import eda_graph

MAX_TURNS = 8

_findings_table_ready = False


async def _run_graph(initial: dict, config: RunnableConfig) -> dict:
    async with eda_graph() as g:
        return await g.ainvoke(initial, config=config)


def _refusal(question: str, reasoning: str, thread_id: str) -> Finding:
    finding = Finding(
        question=question,
        sql="",
        result_summary=(
            "This question is outside what I can answer from the Olist e-commerce dataset."
        ),
        caveats=reasoning,
        confidence="high",
        interpretation="Refused: out of scope for this system.",
    )
    save_finding(thread_id, finding, SqlResult(sql="", columns=[], rows=[]))
    return finding


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def ask_question(
    question: str, thread_id: str | None = None, *, display_question: str | None = None
) -> Finding:
    global _findings_table_ready
    if not _findings_table_ready:  # DDL once per process -- see checkpointer.py
        ensure_findings_table()
        _findings_table_ready = True
    thread_id = thread_id or str(uuid.uuid4())

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    initial = {
        "thread_id": thread_id,
        "question": question,
        "display_question": display_question,
        "sql_result": None,
        "analysis_result": None,
        "finding": None,
        "turn": 0,
        "max_turns": MAX_TURNS,
    }

    # The scope check and the graph run concurrently (see scope_guard.scope_gate): the
    # graph's first data access waits for the verdict, so an out-of-scope question
    # still never touches Postgres, but the check's ~2s overlaps sql_agent's first
    # LLM turn instead of preceding it.
    gate: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    token = scope_gate.set(gate)
    try:
        graph_task = asyncio.create_task(_run_graph(initial, config))
    finally:
        scope_gate.reset(token)  # only the graph task (copied context) sees the gate
    try:
        in_scope, reasoning = await asyncio.to_thread(check_scope, question)
    except BaseException:
        gate.cancel()
        await _cancel(graph_task)
        raise
    gate.set_result(in_scope)
    if not in_scope:
        await _cancel(graph_task)
        return _refusal(question, reasoning, thread_id)

    result = await graph_task
    if result.get("status") != "done" or not result.get("finding"):
        raise RuntimeError(
            f"ask_question failed for thread {thread_id!r}: status={result.get('status')}"
        )
    return Finding.model_validate(result["finding"])
