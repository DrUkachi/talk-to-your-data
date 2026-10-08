"""Graph wiring/routing tests with the LLM boundary stubbed -- same reasoning as
Phase 3's test_cleaning_graph_integration.py: these prove supervisor -> worker ->
supervisor -> ... -> END wiring, not model quality. The real model is exercised
separately in test_supervisor_golden_questions.py (llm-marked).
"""

import uuid

import pytest
from sqlalchemy import text

from talk_to_your_data.agents.eda import supervisor
from talk_to_your_data.agents.eda.state import AnalysisResult, Finding, SqlResult
from talk_to_your_data.db import app_engine

pytestmark = pytest.mark.integration

FAKE_SQL_RESULT = SqlResult(sql="SELECT 1 AS value", columns=["value"], rows=[{"value": 42}])
FAKE_ANALYSIS_RESULT = AnalysisResult(lens="none", stats={}, notes="stubbed")
FAKE_FINDING = Finding(
    question="q",
    sql="SELECT 1",
    result_summary="42",
    caveats="none",
    confidence="high",
    interpretation="it's 42",
)


async def _fake_run_sql_agent(question, **kwargs):
    return FAKE_SQL_RESULT


def _fake_analyze(question, sql_result, **kwargs):
    return FAKE_ANALYSIS_RESULT


def _fake_write_finding(question, sql_result, analysis_result, **kwargs):
    return FAKE_FINDING


def _fake_save_finding(thread_id, finding):
    return "fake-id"


@pytest.fixture(autouse=True)
def _stub_workers(monkeypatch):
    monkeypatch.setattr(supervisor, "run_sql_agent", _fake_run_sql_agent)
    monkeypatch.setattr(supervisor, "analyze", _fake_analyze)
    monkeypatch.setattr(supervisor, "write_finding", _fake_write_finding)
    monkeypatch.setattr(supervisor, "save_finding", _fake_save_finding)


@pytest.fixture(autouse=True)
def _clean_checkpoints():
    yield
    with app_engine().begin() as conn:
        conn.execute(text("DELETE FROM langgraph.checkpoints WHERE thread_id LIKE 'test-%'"))
        conn.execute(text("DELETE FROM langgraph.checkpoint_writes WHERE thread_id LIKE 'test-%'"))
        conn.execute(text("DELETE FROM langgraph.checkpoint_blobs WHERE thread_id LIKE 'test-%'"))


def _initial_state(thread_id: str, question: str = "q", max_turns: int = 8) -> dict:
    return {
        "thread_id": thread_id,
        "question": question,
        "sql_result": None,
        "analysis_result": None,
        "finding": None,
        "turn": 0,
        "max_turns": max_turns,
    }


def _route_skip_analysis(state):
    if state.get("sql_result") is None:
        return {"next": "sql_agent", "reasoning": "need data"}
    if state.get("finding") is None:
        return {"next": "narrative_agent", "reasoning": "simple value, skip analysis"}
    return {"next": "FINISH", "reasoning": "done"}


def _route_use_analysis(state):
    if state.get("sql_result") is None:
        return {"next": "sql_agent", "reasoning": "need data"}
    if state.get("analysis_result") is None:
        return {"next": "analysis_agent", "reasoning": "needs analysis"}
    if state.get("finding") is None:
        return {"next": "narrative_agent", "reasoning": "write finding"}
    return {"next": "FINISH", "reasoning": "done"}


def _route_never_finishes(state):
    return {"next": "sql_agent", "reasoning": "loop forever"}


async def test_simple_question_skips_analysis(monkeypatch):
    monkeypatch.setattr(supervisor, "_route", _route_skip_analysis)
    thread_id = f"test-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    async with supervisor.eda_graph() as g:
        result = await g.ainvoke(_initial_state(thread_id), config=config)

    assert result["status"] == "done"
    assert result["analysis_result"] is None
    assert result["finding"] == FAKE_FINDING.model_dump(mode="json")


async def test_question_routes_through_analysis_when_directed(monkeypatch):
    monkeypatch.setattr(supervisor, "_route", _route_use_analysis)
    thread_id = f"test-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    async with supervisor.eda_graph() as g:
        result = await g.ainvoke(_initial_state(thread_id), config=config)

    assert result["status"] == "done"
    assert result["analysis_result"] == FAKE_ANALYSIS_RESULT.model_dump(mode="json")


async def test_exceeding_max_turns_marks_failed(monkeypatch):
    monkeypatch.setattr(supervisor, "_route", _route_never_finishes)
    thread_id = f"test-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    async with supervisor.eda_graph() as g:
        result = await g.ainvoke(_initial_state(thread_id, max_turns=2), config=config)

    assert result["status"] == "failed"


async def test_supervisor_status_not_overwritten_after_narrative_completes(monkeypatch):
    """Regression test: supervisor_node used to unconditionally set status="routing"
    on its final pass-through after narrative_agent, stomping narrative's "done"."""
    monkeypatch.setattr(supervisor, "_route", _route_skip_analysis)
    thread_id = f"test-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    async with supervisor.eda_graph() as g:
        result = await g.ainvoke(_initial_state(thread_id), config=config)

    assert result["status"] == "done"
