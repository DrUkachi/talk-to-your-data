"""Unit tests for the tool-use plumbing, using a stub Responses-API client -- no real
credentials or network call. See test_cleaning_graph_integration.py for how the
graph-level tests stub this at a higher level for the same reason.
"""

from talk_to_your_data.agents.cleaning import llm
from talk_to_your_data.agents.cleaning.state import FixStrategy, ProfileFinding
from tests.llm_stubs import StubClient, StubResponse, StubToolUseBlock


def _finding() -> ProfileFinding:
    return ProfileFinding(
        id="orders-range-price-0",
        table="orders",
        check="out_of_range",
        column="price",
        description="negative price",
        affected_row_count=3,
        details={"predicate": '"price" < 0'},
    )


def test_propose_fixes_parses_tool_use_response_into_fix_proposals():
    response = StubResponse(
        content=[
            StubToolUseBlock(
                {
                    "proposals": [
                        {
                            "finding_id": "orders-range-price-0",
                            "strategy": "clip_outlier",
                            "params": {"lower": 0, "upper": 10000},
                            "rationale": "negative prices look like data entry errors",
                            "risk": "low",
                        }
                    ]
                }
            )
        ]
    )

    proposals = llm.propose_fixes([_finding()], client=StubClient(response))

    assert len(proposals) == 1
    assert proposals[0].finding_id == "orders-range-price-0"
    assert proposals[0].strategy == FixStrategy.CLIP_OUTLIER
    assert proposals[0].params == {"lower": 0, "upper": 10000}
    assert proposals[0].risk == "low"


def test_propose_fixes_handles_multiple_proposals_and_skipped_findings():
    response = StubResponse(
        content=[
            StubToolUseBlock(
                {
                    "proposals": [
                        {
                            "finding_id": "orders-range-price-0",
                            "strategy": "null_out_impossible_value",
                            "params": {"null_column": "price"},
                            "rationale": "unsafe to guess a replacement value",
                            "risk": "medium",
                        }
                    ]
                }
            )
        ]
    )

    proposals = llm.propose_fixes([_finding(), _finding()], client=StubClient(response))
    assert len(proposals) == 1  # the LLM is allowed to propose fewer fixes than findings


def test_propose_fixes_returns_empty_list_without_calling_the_client_for_no_findings():
    assert llm.propose_fixes([], client=StubClient(StubResponse(content=[]))) == []
