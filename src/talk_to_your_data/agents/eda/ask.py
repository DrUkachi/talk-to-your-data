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
import uuid

from langgraph.types import RunnableConfig

from talk_to_your_data.guardrails.scope_guard import check_scope

from .findings_store import ensure_findings_table, save_finding
from .state import Finding, SqlResult
from .supervisor import eda_graph

MAX_TURNS = 8

_findings_table_ready = False


async def ask_question(question: str, thread_id: str | None = None) -> Finding:
    global _findings_table_ready
    if not _findings_table_ready:  # DDL once per process -- see checkpointer.py
        ensure_findings_table()
        _findings_table_ready = True
    thread_id = thread_id or str(uuid.uuid4())

    in_scope, reasoning = await asyncio.to_thread(check_scope, question)
    if not in_scope:
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

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    initial = {
        "thread_id": thread_id,
        "question": question,
        "sql_result": None,
        "analysis_result": None,
        "finding": None,
        "turn": 0,
        "max_turns": MAX_TURNS,
    }
    async with eda_graph() as g:
        result = await g.ainvoke(initial, config=config)

    if result.get("status") != "done" or not result.get("finding"):
        raise RuntimeError(
            f"ask_question failed for thread {thread_id!r}: status={result.get('status')}"
        )
    return Finding.model_validate(result["finding"])
