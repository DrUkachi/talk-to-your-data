"""Full graph runs against the synthetic dirty fixture. The LLM is stubbed here
(see test_llm_propose_fixes.py for the tool-use plumbing itself) because these
tests are about profile -> propose -> interrupt -> apply -> validate -> loop
wiring, not LLM output quality -- and no real GPT-6.1-Sol/Foundry credentials are
configured in this environment anyway.
"""

import uuid

import pytest
from langgraph.types import Command

from talk_to_your_data.agents.cleaning import graph, llm
from talk_to_your_data.agents.cleaning.state import FixProposal, FixStrategy

pytestmark = pytest.mark.integration


def _fake_propose_fixes(findings, **kwargs):
    strategy_for_check = {
        "logical_ordering": (FixStrategy.NULL_OUT_IMPOSSIBLE_VALUE, {"null_column": "approved_at"}),
        "out_of_range": (FixStrategy.CLIP_OUTLIER, {"lower": 0, "upper": 999999}),
        "exact_duplicates": (FixStrategy.DROP_EXACT_DUPLICATES, {}),
        "categorical_noise": (FixStrategy.NORMALIZE_CASE, {}),
    }
    proposals = []
    for finding in findings:
        strategy, params = strategy_for_check[finding.check]
        proposals.append(
            FixProposal(
                finding_id=finding.id,
                strategy=strategy,
                params=params,
                rationale="test stub",
                risk="low",
            )
        )
    return proposals


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    monkeypatch.setattr(llm, "propose_fixes", _fake_propose_fixes)


def _config(suffix: str) -> dict:
    return {"configurable": {"thread_id": f"test-{uuid.uuid4()}-{suffix}"}}


def test_full_run_resolves_all_defects_in_one_pass(dirty_fixture_table):
    config = _config("full")
    with graph.cleaning_graph() as g:
        result = g.invoke({"table": dirty_fixture_table, "max_attempts": 3}, config=config)
        assert "__interrupt__" in result
        assert result["status"] == "awaiting_approval"

        proposals = g.get_state(config).values["proposals"]
        all_ids = [p["finding_id"] for p in proposals]
        result = g.invoke(Command(resume={"approved_finding_ids": all_ids}), config=config)

    assert result["status"] == "done"
    assert result["validation"]["passed"] is True
    assert result["attempt"] == 1


def test_loops_when_human_approves_only_partial_fixes_first_round(dirty_fixture_table):
    config = _config("loop")
    with graph.cleaning_graph() as g:
        g.invoke({"table": dirty_fixture_table, "max_attempts": 3}, config=config)
        proposals = g.get_state(config).values["proposals"]
        non_dup_ids = [
            p["finding_id"] for p in proposals if p["strategy"] != "drop_exact_duplicates"
        ]
        assert len(non_dup_ids) == 3

        result = g.invoke(Command(resume={"approved_finding_ids": non_dup_ids}), config=config)
        # validate found the still-unapproved duplicate and looped back to profile,
        # which re-ran and found exactly that one finding -- status is
        # "awaiting_approval" again (profile_node's status write is the latest one),
        # but attempt/validation still reflect the failed validation that caused the loop
        assert "__interrupt__" in result
        assert result["status"] == "awaiting_approval"
        assert result["attempt"] == 1
        assert result["validation"]["passed"] is False

        proposals_round_2 = g.get_state(config).values["proposals"]
        assert len(proposals_round_2) == 1
        assert proposals_round_2[0]["strategy"] == "drop_exact_duplicates"

        all_ids_round_2 = [p["finding_id"] for p in proposals_round_2]
        result = g.invoke(Command(resume={"approved_finding_ids": all_ids_round_2}), config=config)

    assert result["status"] == "done"
    assert result["attempt"] == 2


def test_resume_works_from_a_fresh_graph_object(dirty_fixture_table):
    """The acceptance criterion: a brand-new graph + checkpointer connection
    (simulating a process restart) must be able to resume a paused run from just
    its thread_id -- proving the Postgres checkpointer, not an in-memory object,
    is what's doing the persisting.
    """
    config = _config("restart")

    with graph.cleaning_graph() as g1:
        g1.invoke({"table": dirty_fixture_table, "max_attempts": 3}, config=config)
    # g1's connection is closed here; nothing in-process keeps this run alive

    with graph.cleaning_graph() as g2:
        snapshot = g2.get_state(config)
        assert snapshot.next == ("human_approval",)
        proposals = snapshot.values["proposals"]
        all_ids = [p["finding_id"] for p in proposals]
        result = g2.invoke(Command(resume={"approved_finding_ids": all_ids}), config=config)

    assert result["status"] == "done"


def test_run_with_no_findings_completes_without_pausing(dirty_fixture_table):
    from sqlalchemy import text

    from talk_to_your_data.db import app_engine

    with app_engine().begin() as conn:
        conn.execute(text('DELETE FROM "clean"."cleaning_fixture" WHERE id IN (2, 3, 4, 5)'))

    config = _config("no-findings")
    with graph.cleaning_graph() as g:
        result = g.invoke({"table": dirty_fixture_table, "max_attempts": 3}, config=config)

    assert "__interrupt__" not in result
    assert result["status"] == "done"
    assert result["findings"] == []
