"""Pure unit tests for the eval suite's scoring functions -- no LLM/DB needed.
Real end-to-end scoring against the real model is in test_eval_golden_set.py.
"""

from talk_to_your_data.agents.eda.state import Finding
from talk_to_your_data.eval.scorers import (
    score_execution_accuracy,
    score_faithfulness,
    score_refusal_correctness,
)


def _finding(**overrides) -> Finding:
    base = dict(
        question="q",
        sql="SELECT 1",
        result_summary="the total is 42",
        caveats="none known",
        confidence="high",
        interpretation="it's 42",
    )
    base.update(overrides)
    return Finding(**base)


def test_execution_accuracy_passes_when_a_row_value_matches_within_tolerance():
    passed, _ = score_execution_accuracy(100.0, 0.01, [{"revenue": 100.5}])
    assert passed is True


def test_execution_accuracy_fails_when_outside_tolerance():
    passed, detail = score_execution_accuracy(100.0, 0.01, [{"revenue": 120.0}])
    assert passed is False
    assert "120.0" in detail


def test_execution_accuracy_checks_across_multiple_rows_and_columns():
    rows = [{"category": "a", "revenue": 10.0}, {"category": "b", "revenue": 500.0}]
    passed, _ = score_execution_accuracy(500.0, 0.001, rows)
    assert passed is True


def test_execution_accuracy_ignores_non_numeric_values():
    passed, _ = score_execution_accuracy(42.0, 0.01, [{"label": "not a number", "value": 42.0}])
    assert passed is True


def test_faithfulness_passes_when_narrative_number_matches_a_row_value():
    finding = _finding(
        result_summary="Total revenue was 13,221,498.11.",
        interpretation="Revenue is healthy.",
    )
    passed, _ = score_faithfulness(finding, [{"revenue": 13221498.11}])
    assert passed is True


def test_faithfulness_passes_for_a_percentage_share_derived_from_the_total():
    # 1,233,131.72 / (1,233,131.72 + 11,000,000.0) * 100 ~= 10.08%
    finding = _finding(
        result_summary="health_beauty leads with 1,233,131.72, about 10.1% of total revenue.",
        interpretation="Health and beauty is the top category.",
    )
    rows = [
        {"category": "health_beauty", "revenue": 1233131.72},
        {"category": "watches_gifts", "revenue": 11000000.0},
    ]
    passed, _ = score_faithfulness(finding, rows)
    assert passed is True


def test_faithfulness_fails_for_a_fabricated_number():
    finding = _finding(result_summary="Total revenue was 999,999,999.")
    passed, detail = score_faithfulness(finding, [{"revenue": 13221498.11}])
    assert passed is False
    assert "999999999" in detail.replace(".0", "")


def test_faithfulness_passes_when_narrative_has_no_numbers():
    finding = _finding(
        result_summary="No data matched the filter.", interpretation="Nothing found."
    )
    passed, _ = score_faithfulness(finding, [])
    assert passed is True


def test_refusal_correctness_passes_for_the_exact_refusal_interpretation():
    finding = _finding(
        sql="",
        result_summary="This question is outside what I can answer from the Olist dataset.",
        interpretation="Refused: out of scope for this system.",
    )
    passed, _ = score_refusal_correctness(finding)
    assert passed is True


def test_refusal_correctness_fails_when_the_question_was_actually_answered():
    finding = _finding(interpretation="it's 42")
    passed, detail = score_refusal_correctness(finding)
    assert passed is False
    assert "42" in detail


def test_numeric_strings_from_mcp_decimals_count_as_row_values():
    finding = _finding(result_summary="Average score is 4.09.", interpretation="High.")
    passed, _ = score_faithfulness(finding, [{"avg_review_score": "4.0864206240425703"}])
    assert passed is True
    assert score_execution_accuracy(4.0864, 0.001, [{"v": "4.0864206240425703"}])[0] is True


def test_faithfulness_ignores_numbers_restated_from_the_question():
    finding = _finding(
        question="How many 5-star reviews in 2017?",
        result_summary="There were 12,000 5-star reviews in 2017.",
        interpretation="2017 was strong.",
    )
    passed, _ = score_faithfulness(finding, [{"n": 12000}])
    assert passed is True


# --- chart and follow-up scorers ------------------------------------------------


def _chart_finding(tmp_path, kind, with_file=True, **overrides):
    path = tmp_path / f"c-{kind}-{with_file}.png"
    if with_file:
        path.write_bytes(b"x")
    return _finding(chart_ref=str(path) if kind else None, chart_kind=kind, **overrides)


def test_score_chart_requires_the_expected_kind_and_a_real_file(tmp_path):
    from talk_to_your_data.eval.scorers import score_chart

    assert score_chart(_chart_finding(tmp_path, "line"), "line")[0] is True
    assert score_chart(_chart_finding(tmp_path, "bar"), "line")[0] is False
    assert score_chart(_chart_finding(tmp_path, "line", with_file=False), "line")[0] is False


def test_score_chart_none_means_no_chart_for_a_single_value(tmp_path):
    from talk_to_your_data.eval.scorers import score_chart

    assert score_chart(_chart_finding(tmp_path, None), "none")[0] is True
    assert score_chart(_chart_finding(tmp_path, "bar"), "none")[0] is False


def test_score_chart_requested_needs_a_chart_or_an_explanation(tmp_path):
    from talk_to_your_data.eval.scorers import score_chart

    silent = _chart_finding(tmp_path, None, result_summary="625.", caveats="none known")
    told = _chart_finding(tmp_path, None, caveats="No chart was made: single value.")
    assert score_chart(silent, "requested")[0] is False  # the one real failure
    assert score_chart(told, "requested")[0] is True
    assert score_chart(_chart_finding(tmp_path, "line"), "requested")[0] is True


def test_score_followup_value_and_rows_sum_modes():
    from talk_to_your_data.eval.scorers import score_followup

    finding = _finding(result_summary="The highest was 987,765.37.")
    assert score_followup(987765.37, 0.005, "value", [], finding)[0] is True
    rows = [{"period": "2017-01-01", "n": "10"}, {"period": "2017-02-01", "n": "15"}]
    assert score_followup(25, 0.001, "rows_sum", rows, finding)[0] is True
    assert score_followup(30, 0.001, "rows_sum", rows, finding)[0] is False
    assert score_followup(25, 0.001, "rows_sum", [], finding)[0] is False


def test_faithfulness_accepts_total_minus_one_category():
    rows = [{"status": s, "n": n} for s, n in [("a", 96478), ("b", 625), ("c", 2000), ("d", 338)]]
    finding = _finding(
        result_summary="There are 2,963 orders outside status a.", interpretation="ok"
    )
    assert score_faithfulness(finding, rows)[0] is True
