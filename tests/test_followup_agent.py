"""Pure tests for response normalization -- no LLM/PandasAI call needed. The real
PandasAI+LiteLLM call is exercised for real in test_followup_agent_live.py.
"""

import pandas as pd

from talk_to_your_data.agents.eda.followup_agent import _normalize_response


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
