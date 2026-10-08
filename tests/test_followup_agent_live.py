"""Real PandasAI + LiteLLM + Foundry call -- marked llm, skipped by default.
Confirms the whole path: a parent finding's stored rows get reconstructed into a
DataFrame, PandasAI answers the follow-up against it, and the result gets saved as
a linked finding.
"""

import os

import pytest

from talk_to_your_data.agents.eda.findings_store import (
    ensure_findings_table,
    get_finding,
    list_findings,
    save_finding,
)
from talk_to_your_data.agents.eda.followup_agent import answer_followup
from talk_to_your_data.agents.eda.state import Finding, SqlResult

pytestmark = [
    # llm only, deliberately not also `integration` -- see CLAUDE.md's note on why
    # dual-marking sweeps costly real-model calls into a plain `-m integration` run.
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY configured"
    ),
]


def test_followup_answers_against_parent_rows_and_links_finding():
    ensure_findings_table()
    parent_result = SqlResult(
        sql="SELECT product_category, revenue FROM ...",
        columns=["category", "revenue"],
        rows=[
            {"category": "health_beauty", "revenue": 1233131.72},
            {"category": "watches_gifts", "revenue": 1166176.98},
            {"category": "bed_bath_table", "revenue": 1023434.76},
        ],
    )
    parent_finding = Finding(
        question="Which product categories bring in the most revenue?",
        sql=parent_result.sql,
        result_summary="health_beauty leads",
        caveats="none",
        confidence="high",
        interpretation="health_beauty is the top category",
    )
    parent_id = save_finding("test-followup-live", parent_finding, parent_result)

    child = answer_followup(parent_id, "What is the total revenue across all three categories?")
    assert child.question == "What is the total revenue across all three categories?"

    stored = get_finding(parent_id)
    assert stored is not None  # parent untouched

    children = [f for f in list_findings(limit=10) if f.get("parent_finding_id") == parent_id]
    assert len(children) == 1
    assert children[0]["question"] == child.question

    # the correct sum (1233131.72 + 1166176.98 + 1023434.76), not just "an answer"
    expected_total = 3422743.46
    child_rows = children[0]["result_rows"]
    values = [v for row in child_rows for v in row.values() if isinstance(v, int | float)]
    assert any(abs(v - expected_total) < 1.0 for v in values), (
        f"expected a value near {expected_total} in {child_rows}"
    )
