import pytest
from fastapi.testclient import TestClient

from talk_to_your_data.agents.cleaning import llm
from talk_to_your_data.agents.cleaning.api import app
from talk_to_your_data.agents.cleaning.state import FixProposal, FixStrategy

pytestmark = pytest.mark.integration


def _fake_propose_fixes(findings, **kwargs):
    strategy_for_check = {
        "logical_ordering": (FixStrategy.NULL_OUT_IMPOSSIBLE_VALUE, {"null_column": "approved_at"}),
        "out_of_range": (FixStrategy.CLIP_OUTLIER, {"lower": 0, "upper": 999999}),
        "exact_duplicates": (FixStrategy.DROP_EXACT_DUPLICATES, {}),
        "categorical_noise": (FixStrategy.NORMALIZE_CASE, {}),
    }
    return [
        FixProposal(
            finding_id=f.id,
            strategy=strategy_for_check[f.check][0],
            params=strategy_for_check[f.check][1],
            rationale="test stub",
            risk="low",
        )
        for f in findings
    ]


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    monkeypatch.setattr(llm, "propose_fixes", _fake_propose_fixes)


@pytest.fixture
def client():
    return TestClient(app)


def test_unknown_table_returns_400(client):
    r = client.post("/cleaning-runs", json={"table": "not_a_real_table"})
    assert r.status_code == 400


def test_unknown_run_id_returns_404(client):
    assert client.get("/cleaning-runs/does-not-exist").status_code == 404


def test_approving_a_run_that_isnt_awaiting_approval_returns_409(client, dirty_fixture_table):
    from sqlalchemy import text

    from talk_to_your_data.db import app_engine

    with app_engine().begin() as conn:
        conn.execute(text('DELETE FROM "clean"."cleaning_fixture" WHERE id IN (2, 3, 4, 5)'))

    r = client.post("/cleaning-runs", json={"table": dirty_fixture_table})
    run_id = r.json()["run_id"]
    assert r.json()["status"] == "done"

    r2 = client.post(f"/cleaning-runs/{run_id}/approve", json={"approved_finding_ids": []})
    assert r2.status_code == 409


def test_full_lifecycle_start_get_approve(client, dirty_fixture_table):
    start = client.post("/cleaning-runs", json={"table": dirty_fixture_table})
    assert start.status_code == 200
    body = start.json()
    run_id = body["run_id"]
    assert body["status"] == "awaiting_approval"
    assert body["paused"] is True
    assert len(body["findings"]) == 4
    assert len(body["proposals"]) == 4

    fetched = client.get(f"/cleaning-runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["paused"] is True
    assert fetched.json()["proposals"] == body["proposals"]

    approved_ids = [p["finding_id"] for p in body["proposals"]]
    approved = client.post(
        f"/cleaning-runs/{run_id}/approve", json={"approved_finding_ids": approved_ids}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"
    assert approved.json()["validation"]["passed"] is True
    assert len(approved.json()["applied_fixes"]) == 4

    final = client.get(f"/cleaning-runs/{run_id}")
    assert final.json()["status"] == "done"
    assert final.json()["paused"] is False
