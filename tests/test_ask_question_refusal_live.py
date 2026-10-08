"""The full ask_question() refusal path against the real model. Deliberately
does NOT require the MCP server running -- a refusal short-circuits before the
supervisor graph (and therefore sql_agent) ever starts, which this test proves
by succeeding without it.
"""

import os

import pytest

from talk_to_your_data.agents.eda.ask import ask_question
from talk_to_your_data.agents.eda.findings_store import get_finding, list_findings

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY configured"
    ),
]


async def test_out_of_scope_question_is_refused_without_the_graph_or_mcp_server():
    finding = await ask_question("What's the weather like today?", thread_id="test-refusal-live")
    assert finding.sql == ""
    assert "outside what I can answer" in finding.result_summary

    saved = [f for f in list_findings(limit=10) if f["thread_id"] == "test-refusal-live"]
    assert len(saved) == 1
    assert get_finding(saved[0]["id"]) is not None
