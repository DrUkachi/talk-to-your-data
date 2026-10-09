"""A bounded tool-use loop over the Phase 2 MCP server's tools. This is the only
place in the supervisor graph that touches data -- analysis_agent and
narrative_agent only ever see the SqlResult this produces, never a DB connection.

No forced tool_choice (see CLAUDE.md: the Foundry deployment 400s on it) -- the
system prompt instructs tool use explicitly instead, and a turn cap plus a
"never answer without a tool call" check are what keep this grounded.
"""

import asyncio
import json
from typing import Any

from fastmcp import Client
from langfuse import get_client as get_langfuse
from langfuse import observe
from openai import OpenAI

from talk_to_your_data.llm import get_client, get_model, reasoning_config, tool_calls
from talk_to_your_data.tracing import record_generation

from .mcp_tools import call_tool, list_openai_tools, mcp_server_url
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


@observe(name="sql_agent", as_type="agent")
async def run_sql_agent(
    question: str, *, llm_client: OpenAI | None = None, mcp_url: str | None = None
) -> SqlResult:
    llm_client = llm_client or get_client()
    model = get_model()
    langfuse = get_langfuse()

    async with Client(mcp_url or mcp_server_url()) as mcp_client:
        tools = await list_openai_tools(mcp_client)
        messages: list[Any] = [{"role": "user", "content": question}]
        last_result: SqlResult | None = None

        for turn in range(MAX_TURNS):
            with langfuse.start_as_current_observation(
                name=f"sql_agent-turn-{turn}", as_type="generation"
            ):
                response = await asyncio.to_thread(
                    llm_client.responses.create,
                    model=model,
                    max_output_tokens=8192,
                    reasoning=reasoning_config(),
                    instructions=SYSTEM_PROMPT,
                    tools=tools,
                    input=messages,
                )
                record_generation(response)
            # reasoning items must be echoed back alongside their function calls
            messages.extend(item.model_dump(exclude_none=True) for item in response.output)

            calls = tool_calls(response)
            if not calls:
                break

            for call in calls:
                with langfuse.start_as_current_observation(
                    name=call.name, as_type="tool", input=call.arguments
                ) as tool_span:
                    try:
                        result = await call_tool(mcp_client, call.name, call.arguments)
                    except Exception as e:  # noqa: BLE001 -- tool errors (timeouts, guard
                        # rejections) go back to the model so it can fix its query
                        result = {"error": str(e)}
                    tool_span.update(output=result)
                if (
                    call.name in ("query_metric", "run_sql")
                    and isinstance(result, dict)
                    and "error" not in result
                ):
                    last_result = SqlResult(
                        sql=result["sql"], columns=result["columns"], rows=result["rows"]
                    )
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.id,
                        "output": json.dumps(result, default=str),
                    }
                )

    if last_result is None:
        raise RuntimeError(
            f"sql_agent finished without calling query_metric/run_sql for question: {question!r}"
        )
    return last_result
