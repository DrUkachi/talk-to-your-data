"""sql_agent's loop with the LLM and MCP client stubbed: no credentials, no server."""

import json
from types import SimpleNamespace

from talk_to_your_data.agents.eda import sql_agent


class _Item(SimpleNamespace):
    def model_dump(self, **kwargs):
        return dict(self.__dict__)


def _response(*calls):
    output = [
        _Item(type="function_call", call_id=f"c{i}", name=n, arguments=json.dumps(a))
        for i, (n, a) in enumerate(calls)
    ]
    return SimpleNamespace(
        output=output, model="m", usage=SimpleNamespace(input_tokens=1, output_tokens=1)
    )


class _LLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.inputs = []
        self.responses = self

    def create(self, **kwargs):
        self.inputs.append(list(kwargs["input"]))
        return self._responses.pop(0)


class _FakeMCP:
    def __init__(self, url):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _run(monkeypatch, responses):
    async def fake_tools(client):
        return []

    async def fake_call(client, name, args):
        return {"sql": "SELECT 1", "columns": ["n"], "rows": [{"n": 1}]}

    monkeypatch.setattr(sql_agent, "Client", _FakeMCP)
    monkeypatch.setattr(sql_agent, "list_openai_tools", fake_tools)
    monkeypatch.setattr(sql_agent, "call_tool", fake_call)
    monkeypatch.setattr(sql_agent, "record_generation", lambda r: None)
    llm = _LLM(responses)
    result = await sql_agent.run_sql_agent("q", llm_client=llm, mcp_url="http://x")
    return result, llm


async def test_replying_before_fetching_any_data_is_nudged_not_failed(monkeypatch):
    result, llm = await _run(
        monkeypatch,
        [_response(), _response(("query_metric", {"metric": "order_count"})), _response()],
    )
    assert result.rows == [{"n": 1}]
    nudged = llm.inputs[1][-1]
    assert nudged["role"] == "user" and "not fetched any data" in nudged["content"]


async def test_normal_flow_needs_no_nudge(monkeypatch):
    result, llm = await _run(
        monkeypatch, [_response(("query_metric", {"metric": "order_count"})), _response()]
    )
    assert result.rows == [{"n": 1}]
    assert all(m.get("role") != "user" or m["content"] == "q" for m in llm.inputs[1])
