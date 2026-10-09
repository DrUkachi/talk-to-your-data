"""Pure tests for response normalization, the needs-fresh-data classifier (stubbed
client, same pattern as test_scope_guard.py), and answer_followup's fallback
routing (PandasAI/ask_question both stubbed) -- no LLM/PandasAI call needed. The
real PandasAI+LiteLLM call is exercised for real in test_followup_agent_live.py.
"""

import pandas as pd
import pytest

from talk_to_your_data.agents.eda import followup_agent
from talk_to_your_data.agents.eda.followup_agent import _needs_fresh_data, _normalize_response
from talk_to_your_data.agents.eda.state import Finding
from tests.llm_stubs import StubClient, StubResponse, StubToolUseBlock


def test_normalizes_string_response():
    sql_result, chart_ref = _normalize_response(
        {"value": "health_beauty", "type": "string", "last_code_executed": "code", "error": None}
    )
    assert sql_result.rows == [{"answer": "health_beauty"}]
    assert sql_result.columns == ["answer"]
    assert sql_result.sql == "code"
    assert chart_ref is None


def test_normalizes_number_response():
    sql_result, chart_ref = _normalize_response(
        {"value": 4377743.46, "type": "number", "last_code_executed": "code", "error": None}
    )
    assert sql_result.rows == [{"answer": 4377743.46}]
    assert chart_ref is None


def test_normalizes_dataframe_response():
    df = pd.DataFrame({"category": ["a", "b"], "revenue": [100.0, 200.0]})
    sql_result, chart_ref = _normalize_response(
        {"value": df, "type": "dataframe", "last_code_executed": "code", "error": None}
    )
    assert sql_result.columns == ["category", "revenue"]
    assert sql_result.rows == [
        {"category": "a", "revenue": 100.0},
        {"category": "b", "revenue": 200.0},
    ]
    assert chart_ref is None


def test_normalizes_chart_response_setting_chart_ref():
    sql_result, chart_ref = _normalize_response(
        {
            "value": "exports/charts/temp_chart_abc.png",
            "type": "chart",
            "last_code_executed": "code",
            "error": None,
        }
    )
    assert chart_ref == "exports/charts/temp_chart_abc.png"
    assert sql_result.rows == [{"answer": "exports/charts/temp_chart_abc.png"}]


def test_handles_missing_last_code_executed():
    sql_result, _ = _normalize_response({"value": "x", "type": "string", "error": None})
    assert sql_result.sql == ""


def test_needs_fresh_data_parses_true():
    response = StubResponse(
        content=[StubToolUseBlock({"needs_fresh_data": True, "reasoning": "no category column"})]
    )
    assert _needs_fresh_data("q", "a", client=StubClient(response)) is True


def test_needs_fresh_data_parses_false():
    response = StubResponse(
        content=[StubToolUseBlock({"needs_fresh_data": False, "reasoning": "genuine answer"})]
    )
    assert _needs_fresh_data("q", "a", client=StubClient(response)) is False


def test_needs_fresh_data_raises_when_model_does_not_call_the_tool():
    response = StubResponse(content=[])
    with pytest.raises(RuntimeError, match="did not call assess_followup_answer"):
        _needs_fresh_data("q", "a", client=StubClient(response))


PARENT = {
    "thread_id": "t1",
    "question": "What was total revenue from delivered orders?",
    "result_columns": ["revenue"],
    "result_rows": [{"revenue": 13221498.11}],
}

FRESH_FINDING = Finding(
    question="category breakdown?",
    sql="SELECT category, revenue FROM ...",
    result_summary="breakdown",
    caveats="none",
    confidence="high",
    interpretation="here's the breakdown",
)


class _FakePandasAIResponse:
    def __init__(self, d: dict):
        self._d = d

    def to_dict(self) -> dict:
        return self._d


class _FakePandasAIDataFrame:
    def __init__(self, response_dict: dict):
        self._response_dict = response_dict

    def chat(self, question: str) -> _FakePandasAIResponse:
        return _FakePandasAIResponse(self._response_dict)


def _patch_common(monkeypatch, response_dict: dict):
    monkeypatch.setattr(followup_agent, "_ensure_configured", lambda: None)
    monkeypatch.setattr(followup_agent, "get_finding", lambda finding_id: PARENT)
    monkeypatch.setattr(
        followup_agent.pai,
        "DataFrame",
        lambda *a, **kw: _FakePandasAIDataFrame(response_dict),
    )


async def test_answer_followup_falls_back_to_ask_question_when_data_is_insufficient(monkeypatch):
    _patch_common(
        monkeypatch,
        {
            "value": "can't break this down, no category column",
            "type": "string",
            "last_code_executed": "code",
            "error": None,
        },
    )
    monkeypatch.setattr(followup_agent, "_needs_fresh_data", lambda question, answer: True)

    calls = {}

    async def fake_ask_question(question, thread_id=None, *, display_question=None):
        calls["question"] = question
        calls["thread_id"] = thread_id
        calls["display_question"] = display_question
        return FRESH_FINDING

    monkeypatch.setattr(followup_agent, "ask_question", fake_ask_question)
    monkeypatch.setattr(followup_agent, "write_finding", _unexpected_call)
    monkeypatch.setattr(followup_agent, "save_finding", _unexpected_call)

    result = await followup_agent.answer_followup("parent-id", "break down by category")

    assert result == FRESH_FINDING
    assert calls["thread_id"] == "t1"
    # sql_agent has no memory of the conversation -- the fallback question must
    # carry the parent's original question so "that"/"it" can be resolved.
    assert "break down by category" in calls["question"]
    assert PARENT["question"] in calls["question"]
    # ...but the user-facing label (chart title, stored finding) is their own words.
    assert calls["display_question"] == "break down by category"


async def test_answer_followup_uses_pandasai_result_when_data_is_sufficient(monkeypatch):
    _patch_common(
        monkeypatch,
        {"value": 42, "type": "number", "last_code_executed": "code", "error": None},
    )
    monkeypatch.setattr(followup_agent, "_needs_fresh_data", lambda question, answer: False)
    monkeypatch.setattr(followup_agent, "ask_question", _unexpected_call)
    monkeypatch.setattr(followup_agent, "write_finding", lambda *a, **kw: FRESH_FINDING)

    saved = {}

    def fake_save_finding(thread_id, finding, sql_result, parent_finding_id=None):
        saved["thread_id"] = thread_id
        saved["parent_finding_id"] = parent_finding_id
        return "child-id"

    monkeypatch.setattr(followup_agent, "save_finding", fake_save_finding)

    result = await followup_agent.answer_followup("parent-id", "what's the percentage?")

    assert result.question == FRESH_FINDING.question
    assert saved == {"thread_id": "t1", "parent_finding_id": "parent-id"}


async def test_answer_followup_skips_the_classifier_for_dataframe_responses(monkeypatch):
    df_dict = {
        "value": pd.DataFrame({"category": ["a"], "revenue": [1.0]}),
        "type": "dataframe",
        "last_code_executed": "code",
        "error": None,
    }
    _patch_common(monkeypatch, df_dict)
    monkeypatch.setattr(followup_agent, "_needs_fresh_data", _unexpected_call)
    monkeypatch.setattr(followup_agent, "write_finding", lambda *a, **kw: FRESH_FINDING)
    monkeypatch.setattr(followup_agent, "save_finding", lambda *a, **kw: "child-id")

    result = await followup_agent.answer_followup("parent-id", "show me as a table")

    assert result.question == FRESH_FINDING.question


def _unexpected_call(*args, **kwargs):
    raise AssertionError("should not have been called")
