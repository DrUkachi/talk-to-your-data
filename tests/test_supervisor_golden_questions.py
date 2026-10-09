"""Regression check for the supervisor's routing judgment against the real model
(ROADMAP's Phase 4 evaluation note) -- run explicitly with `uv run pytest -m llm`.

Needs BOTH real OPENAI_API_KEY/OPENAI_BASE_URL credentials AND the MCP
server actually running (`uv run python -m talk_to_your_data.mcp_server.server`)
-- sql_agent calls it over HTTP, same as Phase 2 designed it to be reached.
Costs real tokens; not part of the default or `-m integration` suite.
"""

import os
import uuid

import pytest

from talk_to_your_data.agents.eda.supervisor import eda_graph

from .golden_questions_mini import GOLDEN_QUESTIONS

pytestmark = [
    # llm only, deliberately not also `integration` -- so a plain `-m integration`
    # run doesn't sweep in real, costly LLM calls; use `-m llm` explicitly for this.
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OPENAI_API_KEY configured"),
]


@pytest.mark.parametrize("case", GOLDEN_QUESTIONS, ids=[c["question"] for c in GOLDEN_QUESTIONS])
async def test_golden_question_routes_and_answers(case):
    thread_id = f"golden-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    initial = {
        "thread_id": thread_id,
        "question": case["question"],
        "sql_result": None,
        "analysis_result": None,
        "finding": None,
        "turn": 0,
        "max_turns": 8,
    }
    async with eda_graph() as g:
        result = await g.ainvoke(initial, config=config)

    assert result["status"] == "done", f"did not finish: {result.get('status')}"
    assert result["finding"] is not None

    used_analysis = result["analysis_result"] is not None
    assert used_analysis == case["expect_analysis"], (
        f"expected analysis={case['expect_analysis']} but got {used_analysis} "
        f"for: {case['question']!r}"
    )
