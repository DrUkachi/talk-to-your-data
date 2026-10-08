"""Protocol-agnostic entry point: ask a question, get a Finding. The plain
POST /ask endpoint, the A2A executor, and (Phase 6) the Slackbot all call this --
none of them reimplement the supervisor loop, they just adapt its input/output
to their own protocol.
"""

import uuid

from langgraph.types import RunnableConfig

from .findings_store import ensure_findings_table
from .state import Finding
from .supervisor import eda_graph

MAX_TURNS = 8


async def ask_question(question: str, thread_id: str | None = None) -> Finding:
    ensure_findings_table()
    thread_id = thread_id or str(uuid.uuid4())
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
