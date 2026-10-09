"""Per-question pytest wrappers around the same scorers run_eval.py uses, for
visibility when debugging a single golden-set case locally (`pytest -v -m eval
-k "total revenue"`). The pass/fail *gate* CI actually enforces is
run_eval.py's aggregate thresholds, not these -- a single question missing by a
hair here isn't itself a CI failure, the aggregate rate is.

Needs real OPENAI_API_KEY/OPENAI_BASE_URL credentials AND the MCP server
running, same as test_supervisor_golden_questions.py.
"""

import os
import uuid

import pytest

from talk_to_your_data.agents.eda.ask import ask_question
from talk_to_your_data.agents.eda.findings_store import get_latest_finding_for_thread
from talk_to_your_data.eval.golden_set import ANSWERABLE_CASES, REFUSAL_CASES
from talk_to_your_data.eval.scorers import (
    score_execution_accuracy,
    score_faithfulness,
    score_refusal_correctness,
)

pytestmark = [
    pytest.mark.llm,
    pytest.mark.eval,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OPENAI_API_KEY configured"),
]


@pytest.mark.parametrize("case", ANSWERABLE_CASES, ids=[c["question"] for c in ANSWERABLE_CASES])
async def test_answerable_case(case):
    thread_id = f"eval-{uuid.uuid4()}"
    finding = await ask_question(case["question"], thread_id=thread_id)
    stored = get_latest_finding_for_thread(thread_id)
    result_rows = stored["result_rows"] if stored else []

    acc_pass, acc_detail = score_execution_accuracy(
        case["expected_value"], case["tolerance"], result_rows, finding
    )
    assert acc_pass, acc_detail

    faith_pass, faith_detail = score_faithfulness(finding, result_rows)
    assert faith_pass, faith_detail


@pytest.mark.parametrize("case", REFUSAL_CASES, ids=[c["question"] for c in REFUSAL_CASES])
async def test_refusal_case(case):
    thread_id = f"eval-refusal-{uuid.uuid4()}"
    finding = await ask_question(case["question"], thread_id=thread_id)
    passed, detail = score_refusal_correctness(finding)
    assert passed, detail
