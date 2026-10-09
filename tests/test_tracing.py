"""Unit tests for record_generation's own cost/usage math -- no network, stubbed
get_client(). test_tracing_live.py covers the same function against a real
Responses-API response instead of a stub.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from talk_to_your_data import tracing


class _StubGeneration:
    def __init__(self, calls: list[dict[str, Any]]):
        self._calls = calls

    def update_current_generation(self, **kwargs: Any) -> None:
        self._calls.append(kwargs)


@pytest.fixture
def captured_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tracing, "get_client", lambda: _StubGeneration(calls))
    return calls


def _response(model: str, input_tokens: int, output_tokens: int) -> Any:
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_record_generation_computes_cost_for_a_priced_model(captured_calls):
    tracing.record_generation(_response("claude-opus-5-5", input_tokens=1000, output_tokens=500))

    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["usage_details"] == {"input": 1000, "output": 500}
    assert call["cost_details"] == pytest.approx({"input": 0.004, "output": 0.01})


def test_record_generation_uses_sonnet_pricing(captured_calls):
    tracing.record_generation(_response("claude-sonnet-5-5", input_tokens=2000, output_tokens=1000))

    call = captured_calls[0]
    assert call["cost_details"] == pytest.approx({"input": 0.004, "output": 0.01})


def test_record_generation_leaves_cost_none_for_an_unpriced_model(captured_calls):
    tracing.record_generation(_response("some-other-model", input_tokens=10, output_tokens=10))

    call = captured_calls[0]
    assert call["model"] == "some-other-model"
    assert call["usage_details"] == {"input": 10, "output": 10}
    assert call["cost_details"] is None
