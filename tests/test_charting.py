"""Chart selection is deterministic (shape of the rows + explicit-request words),
so every branch is testable with no LLM. The narrative-must-explain behavior is
covered with a stub client."""

import pytest

from talk_to_your_data.agents.eda import charting
from talk_to_your_data.agents.eda.charting import build_chart, wants_chart
from talk_to_your_data.agents.eda.narrative_agent import write_finding
from talk_to_your_data.agents.eda.state import SqlResult
from tests.llm_stubs import StubClient, StubResponse, StubToolUseBlock


@pytest.fixture(autouse=True)
def chart_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_DIR", str(tmp_path))
    return tmp_path


def _result(columns, rows):
    return SqlResult(sql="SELECT 1", columns=columns, rows=rows)


TREND = _result(
    ["month", "revenue"],
    [{"month": f"2017-{m:02d}", "revenue": str(1000 + m * 10)} for m in range(1, 13)],
)
BREAKDOWN = _result(
    ["category", "revenue"],
    [{"category": f"c{i}", "revenue": 100 - i} for i in range(30)],
)
SINGLE = _result(["total"], [{"total": 42}])


def test_trend_shape_gets_a_line_chart_without_being_asked(chart_dir):
    chart = build_chart("How did monthly revenue trend in 2017?", TREND)
    assert chart.kind == "line" and chart.path and chart.note is None
    assert (chart_dir / chart.path.split("/")[-1]).stat().st_size > 1000


def test_breakdown_shape_gets_a_bar_chart_capped_at_top_n():
    chart = build_chart("Revenue by category", BREAKDOWN)
    assert chart.kind == "bar" and chart.path


def test_single_value_has_no_chart_and_no_note_when_not_requested():
    chart = build_chart("How many orders were canceled?", SINGLE)
    assert chart.path is None and chart.note is None


def test_single_value_with_explicit_request_explains_why():
    chart = build_chart("Plot the total number of orders", SINGLE)
    assert chart.path is None
    assert chart.note and "single value" in chart.note


def test_no_numeric_column_with_explicit_request_explains_why():
    result = _result(["a", "b"], [{"a": "x", "b": "y"}, {"a": "z", "b": "w"}])
    chart = build_chart("chart this", result)
    assert chart.path is None and "no numeric column" in (chart.note or "")


def test_pie_hint_is_honored_for_a_breakdown():
    assert build_chart("pie chart of revenue by category", BREAKDOWN).kind == "pie"


def test_bar_hint_overrides_line_for_a_trend():
    assert build_chart("bar chart of monthly revenue", TREND).kind == "bar"


def test_scatter_fallback_only_when_explicitly_requested():
    result = _result(["price", "freight"], [{"price": i, "freight": i * 2} for i in range(5)])
    assert build_chart("how many", result).path is None
    assert build_chart("plot price vs freight", result).kind == "scatter"


def test_numeric_year_column_counts_as_temporal():
    result = _result(["year", "orders"], [{"year": 2016, "orders": 3}, {"year": 2017, "orders": 9}])
    assert build_chart("orders per year", result).kind == "line"


def test_wants_chart_detects_explicit_words_only():
    assert wants_chart("Please visualize revenue") and wants_chart("plot it")
    assert not wants_chart("What was revenue by month?")


def test_render_failure_does_not_lose_the_answer(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad")

    monkeypatch.setattr(charting, "_render", boom)
    chart = build_chart("plot revenue", TREND)
    assert chart.path is None and "could not be rendered" in (chart.note or "")


def _finding_response(summary="42 orders.", caveats="none known"):
    return StubResponse(
        content=[
            StubToolUseBlock(
                {
                    "result_summary": summary,
                    "interpretation": "ok",
                    "caveats": caveats,
                    "confidence": "high",
                },
                name="write_finding",
            )
        ]
    )


def test_narrative_is_guaranteed_to_mention_a_missing_chart_even_if_model_does_not():
    note = "No chart was made: the result is a single value, so there is nothing to plot."
    finding = write_finding(
        "plot total orders",
        SINGLE,
        None,
        client=StubClient(_finding_response()),
        chart_note=note,
    )
    assert "No chart was made" in finding.caveats


def test_narrative_does_not_duplicate_the_note_when_model_already_explained():
    finding = write_finding(
        "plot total orders",
        SINGLE,
        None,
        client=StubClient(_finding_response(caveats="A chart isn't possible for one value.")),
        chart_note="No chart was made: single value.",
    )
    assert finding.caveats == "A chart isn't possible for one value."


def test_trend_chart_is_restricted_to_the_analysis_window(monkeypatch):
    seen = {}

    def fake_render(kind, spec, result, title, out):
        seen["n"] = len(result.rows)
        out.write_bytes(b"x")

    monkeypatch.setattr(charting, "_render", fake_render)
    wide = _result(
        ["period", "revenue"],
        [{"period": f"{y}-{m:02d}-01", "revenue": 1} for y in (2016, 2017, 2018) for m in (1, 6)],
    )
    chart = build_chart("trend in 2017", wide, period_window=("2017-01-01", "2017-12-01"))
    assert chart.kind == "line" and seen["n"] == 2
