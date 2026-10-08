"""record_generation against a REAL Anthropic response object -- confirms it
doesn't raise against the real Message shape (not just the SimpleNamespace stub
in test_tracing.py) and that the model/usage it extracts are sane, by capturing
what it passes to Langfuse rather than trusting a stub's assumed shape.

Does NOT assert that Langfuse's own query-back API later exposes this data.
Manually confirmed (polling `client.api.observations.get_many` for 60+s after a
real traced call) that model/usage_details/cost_details come back None on this
project's Langfuse Cloud org even though the SDK emits the right OTel span
attributes locally (see tracing.py's docstring) -- a backend-side gap, not
something this project's code can fix, and not safe to assert past without
making this test permanently and uninformatively red.
"""

import os
from typing import Any

import anthropic
import pytest

from talk_to_your_data import tracing

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY configured"
    ),
]


class _StubGeneration:
    def __init__(self, calls: list[dict[str, Any]]):
        self._calls = calls

    def update_current_generation(self, **kwargs: Any) -> None:
        self._calls.append(kwargs)


def test_record_generation_against_a_real_response(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tracing, "get_client", lambda: _StubGeneration(calls))

    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    response = client.messages.create(
        model=model, max_tokens=16, messages=[{"role": "user", "content": "Say OK."}]
    )

    tracing.record_generation(response)

    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == model
    assert call["usage_details"]["input"] > 0
    assert call["usage_details"]["output"] > 0
    if model in tracing.PRICING_PER_TOKEN:
        assert call["cost_details"]["input"] > 0
        assert call["cost_details"]["output"] > 0
