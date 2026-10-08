"""Unit test for the tool-use parsing itself, stubbed client -- no real
credentials. Scenario coverage against the real model is in
test_scope_guard_live.py.
"""

import pytest

from talk_to_your_data.guardrails.scope_guard import check_scope


class _StubToolUseBlock:
    type = "tool_use"

    def __init__(self, input_: dict):
        self.input = input_


class _StubUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 10):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _StubResponse:
    def __init__(self, content: list, model: str = "claude-opus-5-5"):
        self.content = content
        self.model = model
        self.usage = _StubUsage()


class _StubMessages:
    def __init__(self, response: _StubResponse):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _StubClient:
    def __init__(self, response: _StubResponse):
        self.messages = _StubMessages(response)


def test_parses_in_scope_response():
    response = _StubResponse(
        content=[_StubToolUseBlock({"in_scope": True, "reasoning": "about revenue"})]
    )
    in_scope, reasoning = check_scope("What was total revenue?", client=_StubClient(response))
    assert in_scope is True
    assert reasoning == "about revenue"


def test_parses_out_of_scope_response():
    response = _StubResponse(
        content=[_StubToolUseBlock({"in_scope": False, "reasoning": "asks about weather"})]
    )
    in_scope, _ = check_scope("What's the weather?", client=_StubClient(response))
    assert in_scope is False


def test_raises_when_model_does_not_call_the_tool():
    response = _StubResponse(content=[])
    with pytest.raises(RuntimeError, match="did not call check_scope"):
        check_scope("anything", client=_StubClient(response))
