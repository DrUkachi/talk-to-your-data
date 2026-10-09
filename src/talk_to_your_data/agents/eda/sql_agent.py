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

from talk_to_your_data.guardrails.scope_guard import await_scope_clearance
from talk_to_your_data.llm import get_client, get_model, reasoning_config, tool_calls
from talk_to_your_data.semantic_layer.compiler import ALLOWED_GRAINS
from talk_to_your_data.semantic_layer.registry import METRICS, MODELS_REGISTRY
from talk_to_your_data.tracing import record_generation

from .mcp_tools import call_tool, list_openai_tools, mcp_server_url
from .state import SqlResult

MAX_TURNS = 6
NUDGE = (
    "You have not fetched any data yet. Call query_metric (or run_sql) now; "
    "do not reply without a tool call."
)

_PROMPT_HEAD = (
    "You answer business questions about an e-commerce dataset using only the "
    "tools provided -- never state a number you didn't get from a tool call. "
    "Prefer query_metric when a metric below covers the question; fall back to "
    "run_sql only for things no metric covers.\n\n"
    "The metric catalogue below is complete -- do NOT call list_metrics or "
    "describe_metric. query_metric(metric, dimensions=[...], filters={dim: [values]}, "
    "time_grain=one of {grains}); a time_grain adds a 'period' column.\n\n"
    "run_sql: one read-only SELECT over schema-qualified raw.* tables (orders, "
    "order_items, order_payments, order_reviews, customers, products, sellers, "
    "product_category_name_translation); it has a 15s timeout, so avoid correlated "
    "subqueries -- use JOINs / LEFT JOIN ... IS NULL anti-joins / aggregates instead. "
    "Name any time column 'period'.\n\n"
    "Make every tool call you need in a SINGLE step (parallel calls), not one at a "
    "time. After the last tool result, reply with only the word DONE.\n\n"
    "Metric catalogue:\n"
)


def build_system_prompt() -> str:
    lines = []
    for m in METRICS.values():
        model = MODELS_REGISTRY[m.model]
        filters = f"; default filters {m.default_filters}" if m.default_filters else ""
        lines.append(
            f"- {m.name}: {' '.join(m.description.split())} "
            f"[grain: {model.grain}; dimensions: {', '.join(model.dimensions)}{filters}]"
        )
    return _PROMPT_HEAD.replace("{grains}", ", ".join(ALLOWED_GRAINS)) + "\n".join(lines)


SYSTEM_PROMPT = build_system_prompt()


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
        results: list[SqlResult] = []

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
                if not results and turn < MAX_TURNS - 1:
                    # Replied (e.g. "DONE") before fetching anything -- seen
                    # intermittently under the terse "reply DONE" prompt. Nudge, don't fail.
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                break

            await await_scope_clearance()  # nothing touches data until scope passes
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
                    results.append(
                        SqlResult(sql=result["sql"], columns=result["columns"], rows=result["rows"])
                    )
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.id,
                        "output": json.dumps(result, default=str),
                    }
                )

    if not results:
        raise RuntimeError(
            f"sql_agent finished without calling query_metric/run_sql for question: {question!r}"
        )
    return merge_results(results)


def merge_results(results: list[SqlResult]) -> SqlResult:
    """Parallel tool calls can return several answers (e.g. revenue and order_count for
    "average order value"). When every result is a single row they describe one fact
    set, so merge them into one row -- the narrative must see ALL the numbers it is
    asked to derive from, not just whichever call finished last. Otherwise keep the
    last result (the original behaviour for multi-row / retried queries)."""
    if len(results) == 1 or any(len(r.rows) != 1 for r in results):
        return results[-1]
    columns: list[str] = []
    row: dict[str, Any] = {}
    for r in results:
        for col in r.columns:
            name = col
            while name in row:
                name += "_2"
            columns.append(name)
            row[name] = r.rows[0][col]
    return SqlResult(
        sql="\n-- next query --\n".join(r.sql for r in results), columns=columns, rows=[row]
    )
