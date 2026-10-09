"""Stub Responses-API client/response for unit tests -- no credentials or
network. Mirrors only what talk_to_your_data.llm and tracing.record_generation
read: output items of type "function_call" (call_id/name/arguments), model,
usage.input_tokens/output_tokens.
"""

import json
from types import SimpleNamespace
from typing import Any


class StubToolUseBlock:
    """One tool call; `input_` is the (already-parsed) arguments dict."""

    def __init__(self, input_: dict[str, Any], name: str = "tool"):
        self.input = input_
        self.name = name


class StubResponse:
    def __init__(self, content: list[StubToolUseBlock], model: str = "gpt-6.1-sol"):
        self.output = [
            SimpleNamespace(
                type="function_call",
                call_id=f"call_{i}",
                name=b.name,
                arguments=json.dumps(b.input),
            )
            for i, b in enumerate(content)
        ]
        self.status = "completed"
        self.model = model
        self.usage = SimpleNamespace(input_tokens=10, output_tokens=10)


class _StubResponses:
    def __init__(self, response: StubResponse):
        self._response = response

    def create(self, **kwargs: Any) -> StubResponse:
        return self._response


class StubClient:
    def __init__(self, response: StubResponse):
        self.responses = _StubResponses(response)
