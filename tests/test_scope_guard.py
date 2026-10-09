"""Unit test for the tool-use parsing itself, stubbed client -- no real
credentials. Scenario coverage against the real model is in
test_scope_guard_live.py.
"""

import pytest

from talk_to_your_data.guardrails.scope_guard import check_scope
from tests.llm_stubs import StubClient, StubResponse, StubToolUseBlock


def test_parses_in_scope_response():
    response = StubResponse(
        content=[StubToolUseBlock({"in_scope": True, "reasoning": "about revenue"})]
    )
    in_scope, reasoning = check_scope("What was total revenue?", client=StubClient(response))
    assert in_scope is True
    assert reasoning == "about revenue"


def test_parses_out_of_scope_response():
    response = StubResponse(
        content=[StubToolUseBlock({"in_scope": False, "reasoning": "asks about weather"})]
    )
    in_scope, _ = check_scope("What's the weather?", client=StubClient(response))
    assert in_scope is False


def test_raises_when_model_does_not_call_the_tool():
    response = StubResponse(content=[])
    with pytest.raises(RuntimeError, match="did not call check_scope"):
        check_scope("anything", client=StubClient(response))
