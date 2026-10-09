"""Behavior added by the latency pass: deterministic routing, shape-based lens + rule
based date range (no LLM), the metric catalogue in sql_agent's prompt, and the scope
gate that lets the scope check overlap the graph without data ever being touched for
an out-of-scope question."""

import asyncio
import uuid

import pytest

from talk_to_your_data.agents.eda import ask, supervisor
from talk_to_your_data.agents.eda.analysis_agent import (
    analyze,
    extract_range_by_rules,
    infer_lens,
)
from talk_to_your_data.agents.eda.sql_agent import SYSTEM_PROMPT
from talk_to_your_data.agents.eda.state import AnalysisLens, SqlResult
from talk_to_your_data.guardrails.scope_guard import (
    ScopeRefused,
    await_scope_clearance,
    scope_gate,
)
from talk_to_your_data.semantic_layer.registry import METRICS


def _result(columns, rows):
    return SqlResult(sql="SELECT 1", columns=columns, rows=rows)


# --- routing ---------------------------------------------------------------


def test_route_runs_sql_then_analysis_then_narrative_then_finishes():
    assert supervisor._route({})["next"] == "sql_agent"
    assert supervisor._route({"sql_result": {"x": 1}})["next"] == "analysis_agent"
    both = {"sql_result": {"x": 1}, "analysis_result": {"y": 2}}
    assert supervisor._route(both)["next"] == "narrative_agent"
    assert supervisor._route({**both, "finding": {"z": 3}})["next"] == "FINISH"


# --- lens from shape -------------------------------------------------------


def test_lens_is_inferred_from_result_shape():
    trend = _result(
        ["period", "r"], [{"period": "2017-01-01", "r": 1}, {"period": "2017-02-01", "r": 2}]
    )
    breakdown = _result(["c", "n"], [{"c": "a", "n": 1}, {"c": "b", "n": 2}])
    assert infer_lens(trend) == AnalysisLens.TIME_SERIES_TREND
    assert infer_lens(breakdown) == AnalysisLens.CATEGORY_BREAKDOWN
    assert infer_lens(_result(["n"], [{"n": 5}])) == AnalysisLens.NONE


# --- date range rules ------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("How did monthly revenue trend in 2017?", ("2017-01-01", "2017-12-31")),
        ("revenue since 2017", ("2017-01-01", None)),
        ("orders between 2017 and 2018", ("2017-01-01", "2018-12-31")),
        ("How did revenue change month over month?", (None, None)),
        ("revenue since June 2017", None),  # rules can't resolve -> LLM fallback
        ("orders last quarter", None),
    ],
)
def test_range_rules(question, expected):
    assert extract_range_by_rules(question) == expected


class _ExplodingClient:
    class responses:  # noqa: N801
        @staticmethod
        def create(**kwargs):
            raise AssertionError("the LLM must not be called for a simple year range")


def test_analyze_uses_no_llm_for_a_simple_trend_question():
    rows = [{"period": f"{y}-{m:02d}-01", "revenue": 100 + m} for y in (2016, 2017) for m in (1, 6)]
    result = analyze(
        "How did monthly revenue trend in 2017?",
        _result(["period", "revenue"], rows),
        client=_ExplodingClient(),
    )
    assert result.lens == AnalysisLens.TIME_SERIES_TREND
    assert result.stats["first_period"].startswith("2017-01")
    assert result.stats["periods_used"] == 2


# --- sql_agent prompt ------------------------------------------------------


def test_system_prompt_lists_every_metric_and_forbids_discovery_calls():
    for name in METRICS:
        assert name in SYSTEM_PROMPT
    assert "do NOT call list_metrics" in SYSTEM_PROMPT
    assert "{grains}" not in SYSTEM_PROMPT and "month" in SYSTEM_PROMPT


# --- scope gate ------------------------------------------------------------


async def test_clearance_is_a_noop_without_a_gate():
    await await_scope_clearance()


async def test_clearance_blocks_until_verdict_and_raises_on_refusal():
    gate = asyncio.get_running_loop().create_future()
    token = scope_gate.set(gate)
    try:
        waiter = asyncio.create_task(await_scope_clearance())
        await asyncio.sleep(0)
        assert not waiter.done()  # data access is held until the verdict
        gate.set_result(False)
        with pytest.raises(ScopeRefused):
            await waiter
    finally:
        scope_gate.reset(token)


@pytest.mark.integration
async def test_out_of_scope_question_cancels_the_graph_before_any_data_access(monkeypatch):
    touched = []

    async def fake_graph(initial, config):
        await await_scope_clearance()  # what sql_agent does before its first tool call
        touched.append("data")
        return {}

    monkeypatch.setattr(ask, "_run_graph", fake_graph)
    monkeypatch.setattr(ask, "check_scope", lambda q: (False, "weather"))
    finding = await ask.ask_question("weather?", thread_id=f"test-{uuid.uuid4()}")

    assert finding.interpretation == "Refused: out of scope for this system."
    assert touched == []


# --- merging parallel single-row results -----------------------------------


def test_parallel_single_row_results_are_merged_so_the_narrative_sees_all_numbers():
    from talk_to_your_data.agents.eda.sql_agent import merge_results

    merged = merge_results(
        [
            SqlResult(sql="q1", columns=["revenue"], rows=[{"revenue": 100.0}]),
            SqlResult(sql="q2", columns=["order_count"], rows=[{"order_count": 4}]),
        ]
    )
    assert merged.columns == ["revenue", "order_count"]
    assert merged.rows == [{"revenue": 100.0, "order_count": 4}]


def test_multi_row_results_keep_the_last_one():
    from talk_to_your_data.agents.eda.sql_agent import merge_results

    first = SqlResult(sql="q1", columns=["a"], rows=[{"a": 1}])
    last = SqlResult(sql="q2", columns=["p", "v"], rows=[{"p": "x", "v": 1}, {"p": "y", "v": 2}])
    assert merge_results([first, last]) is last


def test_scorer_accepts_a_difference_and_a_ratio_of_stored_values():
    from talk_to_your_data.agents.eda.state import Finding
    from talk_to_your_data.eval.scorers import score_faithfulness

    rows = [{"total": 99441, "delivered": 96478, "revenue": 1000.0, "orders": 8}]
    finding = Finding(
        question="q",
        sql="s",
        confidence="high",
        caveats="none",
        result_summary="2,963 non-delivered orders; average order value 125.",
        interpretation="ok",
    )
    assert score_faithfulness(finding, rows)[0] is True


async def test_narrative_node_stores_only_the_rows_in_the_reported_window(monkeypatch):
    """Regression: the stored rows held every period while the answer said "2017", so a
    follow-up like "total across those months" summed all of them (13.2M, not 5.96M)."""
    saved = {}
    rows = [{"period": f"{y}-{m:02d}-01", "revenue": 1} for y in (2016, 2017) for m in (1, 6)]
    sql_result = {"sql": "SELECT 1", "columns": ["period", "revenue"], "rows": rows}
    analysis = {
        "lens": "time_series_trend",
        "stats": {"first_period": "2017-01-01", "last_period": "2017-12-31"},
        "notes": "",
    }
    from talk_to_your_data.agents.eda.state import Finding

    def fake_write(question, sql_result, analysis_result, **kwargs):
        saved["narrative_rows"] = len(sql_result.rows)
        return Finding(
            question=question,
            sql=sql_result.sql,
            result_summary="s",
            caveats="c",
            confidence="high",
            interpretation="i",
        )

    monkeypatch.setattr(supervisor, "write_finding", fake_write)
    monkeypatch.setattr(
        supervisor,
        "save_finding",
        lambda tid, f, sr, parent_finding_id=None: saved.update(rows=len(sr.rows), sql=sr.sql),
    )
    monkeypatch.setattr(supervisor, "build_chart", lambda *a, **k: charting_none())
    await supervisor.narrative_agent_node(
        {
            "question": "revenue in 2017",
            "thread_id": "t",
            "sql_result": sql_result,
            "analysis_result": analysis,
        }
    )
    assert saved["narrative_rows"] == 2 and saved["rows"] == 2
    assert "narrowed" in saved["sql"]


def charting_none():
    from talk_to_your_data.agents.eda.charting import ChartResult

    return ChartResult(path=None)
