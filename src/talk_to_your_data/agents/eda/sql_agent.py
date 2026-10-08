"""A bounded tool-use loop over the Phase 2 MCP server's tools. This is the only
place in the supervisor graph that touches data -- analysis_agent and
narrative_agent only ever see the SqlResult this produces, never a DB connection.

No forced tool_choice (see CLAUDE.md: the Foundry deployment 400s on it) -- the
system prompt instructs tool use explicitly instead, and a turn cap plus a
"never answer without a tool call" check are what keep this grounded.
"""

import os
from typing import Any, cast

import anthropic
from fastmcp import Client
from langfuse import get_client, observe

from talk_to_your_data.tracing import record_generation

from .mcp_tools import call_tool, list_anthropic_tools, mcp_server_url
from .state import SqlResult

MAX_TURNS = 6

SYSTEM_PROMPT = (
    "You answer business questions about an e-commerce dataset using only the "
    "tools provided -- never state a number you didn't get from a tool call. "
    "Prefer query_metric when an existing metric covers the question (call "
    "list_metrics/describe_metric first if you're not sure which one); fall back "
    "to run_sql only for things no metric covers. When you have enough data to "
    "answer, reply in plain text summarizing what you found -- do not call more "
    "tools than necessary."
)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )


@observe(name="sql_agent", as_type="agent")
async def run_sql_agent(
    question: str, *, llm_client: anthropic.Anthropic | None = None, mcp_url: str | None = None
) -> SqlResult:
    llm_client = llm_client or _client()
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    langfuse = get_client()

    async with Client(mcp_url or mcp_server_url()) as mcp_client:
        tools = await list_anthropic_tools(mcp_client)
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        last_result: SqlResult | None = None

        for turn in range(MAX_TURNS):
            with langfuse.start_as_current_observation(
                name=f"sql_agent-turn-{turn}", as_type="generation"
            ):
                response = llm_client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=cast(list[anthropic.types.MessageParam], messages),
                )
                record_generation(response)
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            tool_results = []
            for tool_use in tool_uses:
                with langfuse.start_as_current_observation(
                    name=tool_use.name, as_type="tool", input=tool_use.input
                ) as tool_span:
                    result = await call_tool(mcp_client, tool_use.name, dict(tool_use.input))
                    tool_span.update(output=result)
                if tool_use.name in ("query_metric", "run_sql") and isinstance(result, dict):
                    last_result = SqlResult(
                        sql=result["sql"], columns=result["columns"], rows=result["rows"]
                    )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tool_use.id, "content": str(result)}
                )
            messages.append({"role": "user", "content": tool_results})

    if last_result is None:
        raise RuntimeError(
            f"sql_agent finished without calling query_metric/run_sql for question: {question!r}"
        )
    return last_result
