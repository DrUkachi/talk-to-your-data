"""The /findings routes, with findings_store/followup_agent stubbed -- no DB or
LLM needed. (The real round-trip is covered by test_findings_store.py and
test_followup_agent_live.py.)
"""

from fastapi.testclient import TestClient

from talk_to_your_data.agents.eda import api
from talk_to_your_data.agents.eda.state import Finding

PARENT = {
    "id": "parent-id",
    "thread_id": "t1",
    "question": "q",
    "sql": "SELECT 1",
    "result_summary": "42",
    "chart_ref": None,
    "caveats": "none",
    "confidence": "high",
    "interpretation": "it's 42",
    "result_columns": ["value"],
    "result_rows": [{"value": 42}],
    "parent_finding_id": None,
}

FOLLOWUP_FINDING = Finding(
    question="and the breakdown?",
    sql="code",
    result_summary="breakdown",
    caveats="none",
    confidence="high",
    interpretation="here's the breakdown",
)


def test_get_findings_list(monkeypatch):
    monkeypatch.setattr(api, "list_findings", lambda limit=20: [PARENT])
    client = TestClient(api.app)
    response = client.get("/findings")
    assert response.status_code == 200
    assert response.json() == [PARENT]


def test_get_finding_by_id_found(monkeypatch):
    monkeypatch.setattr(
        api, "get_finding", lambda finding_id: PARENT if finding_id == "parent-id" else None
    )
    client = TestClient(api.app)
    response = client.get("/findings/parent-id")
    assert response.status_code == 200
    assert response.json()["question"] == "q"


def test_get_finding_by_id_not_found(monkeypatch):
    monkeypatch.setattr(api, "get_finding", lambda finding_id: None)
    client = TestClient(api.app)
    response = client.get("/findings/does-not-exist")
    assert response.status_code == 404


def test_followup_endpoint_returns_finding(monkeypatch):
    def fake_answer_followup(finding_id, question):
        assert finding_id == "parent-id"
        assert question == "and the breakdown?"
        return FOLLOWUP_FINDING

    monkeypatch.setattr(api, "answer_followup", fake_answer_followup)
    client = TestClient(api.app)
    response = client.post("/findings/parent-id/followup", json={"question": "and the breakdown?"})
    assert response.status_code == 200
    assert response.json()["result_summary"] == "breakdown"


def test_followup_endpoint_404s_for_unknown_parent(monkeypatch):
    def fake_answer_followup_raises(finding_id, question):
        raise ValueError(f"no finding with id {finding_id!r}")

    monkeypatch.setattr(api, "answer_followup", fake_answer_followup_raises)
    client = TestClient(api.app)
    response = client.post("/findings/does-not-exist/followup", json={"question": "q"})
    assert response.status_code == 404
