import pytest
from sqlalchemy import text

from talk_to_your_data.agents.eda.findings_store import (
    ensure_findings_table,
    list_findings,
    save_finding,
)
from talk_to_your_data.agents.eda.state import Finding
from talk_to_your_data.db import app_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def clean_findings_table():
    ensure_findings_table()
    yield
    with app_engine().begin() as conn:
        conn.execute(text("DELETE FROM findings.findings WHERE thread_id LIKE 'test-%'"))


def test_save_and_list_round_trip(clean_findings_table):
    finding = Finding(
        question="q",
        sql="SELECT 1",
        result_summary="42",
        caveats="none",
        confidence="high",
        interpretation="it's 42",
    )
    finding_id = save_finding("test-thread-abc", finding)
    assert finding_id

    rows = list_findings(limit=5)
    saved = next(r for r in rows if r["id"] == finding_id)
    assert saved["question"] == "q"
    assert saved["thread_id"] == "test-thread-abc"
    assert saved["confidence"] == "high"


def test_list_findings_orders_by_created_at_desc(clean_findings_table):
    f1 = Finding(
        question="first",
        sql="SELECT 1",
        result_summary="a",
        caveats="none",
        confidence="low",
        interpretation="x",
    )
    f2 = Finding(
        question="second",
        sql="SELECT 2",
        result_summary="b",
        caveats="none",
        confidence="low",
        interpretation="y",
    )
    save_finding("test-thread-order", f1)
    save_finding("test-thread-order", f2)

    rows = list_findings(limit=2)
    assert rows[0]["question"] == "second"
