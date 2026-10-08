import pytest
from sqlalchemy import text

from talk_to_your_data.agents.eda.findings_store import (
    ensure_findings_table,
    get_finding,
    get_latest_finding_for_thread,
    list_findings,
    save_finding,
)
from talk_to_your_data.agents.eda.state import Finding, SqlResult
from talk_to_your_data.db import app_engine

pytestmark = pytest.mark.integration

SQL_RESULT = SqlResult(sql="SELECT 1 AS value", columns=["value"], rows=[{"value": 42}])


@pytest.fixture
def clean_findings_table():
    ensure_findings_table()
    yield
    with app_engine().begin() as conn:
        conn.execute(text("DELETE FROM findings.findings WHERE thread_id LIKE 'test-%'"))


def _finding(**overrides) -> Finding:
    base = dict(
        question="q",
        sql="SELECT 1",
        result_summary="42",
        caveats="none",
        confidence="high",
        interpretation="it's 42",
    )
    base.update(overrides)
    return Finding(**base)


def test_save_and_list_round_trip(clean_findings_table):
    finding_id = save_finding("test-thread-abc", _finding(), SQL_RESULT)
    assert finding_id

    rows = list_findings(limit=5)
    saved = next(r for r in rows if r["id"] == finding_id)
    assert saved["question"] == "q"
    assert saved["thread_id"] == "test-thread-abc"
    assert saved["confidence"] == "high"
    assert saved["parent_finding_id"] is None


def test_list_findings_orders_by_created_at_desc(clean_findings_table):
    save_finding("test-thread-order", _finding(question="first"), SQL_RESULT)
    save_finding("test-thread-order", _finding(question="second"), SQL_RESULT)

    rows = list_findings(limit=2)
    assert rows[0]["question"] == "second"


def test_get_finding_round_trips_result_columns_and_rows(clean_findings_table):
    finding_id = save_finding("test-thread-get", _finding(), SQL_RESULT)

    fetched = get_finding(finding_id)
    assert fetched is not None
    assert fetched["result_columns"] == ["value"]
    assert fetched["result_rows"] == [{"value": 42}]


def test_get_finding_returns_none_for_unknown_id(clean_findings_table):
    assert get_finding("00000000-0000-0000-0000-000000000000") is None


def test_get_latest_finding_for_thread_returns_the_most_recent(clean_findings_table):
    save_finding("test-thread-latest", _finding(question="first"), SQL_RESULT)
    second_id = save_finding("test-thread-latest", _finding(question="second"), SQL_RESULT)

    latest = get_latest_finding_for_thread("test-thread-latest")
    assert latest is not None
    assert latest["id"] == second_id
    assert latest["question"] == "second"


def test_get_latest_finding_for_thread_returns_none_for_unknown_thread(clean_findings_table):
    assert get_latest_finding_for_thread("test-thread-never-existed") is None


def test_save_finding_links_parent_finding_id(clean_findings_table):
    parent_id = save_finding("test-thread-parent", _finding(question="parent"), SQL_RESULT)
    child_id = save_finding(
        "test-thread-parent",
        _finding(question="follow-up"),
        SQL_RESULT,
        parent_finding_id=parent_id,
    )

    child = get_finding(child_id)
    assert child is not None
    assert child["parent_finding_id"] == parent_id
