"""Out-of-scope question refusal -- runs before the supervisor graph ever starts
(see agents/eda/ask.py), so an unanswerable question never reaches sql_agent: no
cost spent on a doomed tool-use loop, and no chance of narrating an answer from
nothing.

This is itself an LLM call, so it's a classifier, not a proof -- a sufficiently
adversarial prompt could in principle manipulate it (acknowledged, not solved,
here). The structural guardrail underneath it is what actually holds: even if
this check were fooled, sql_agent can only reach Postgres through the MCP
server's read-only tools, which can't do anything but read raw.* data. Like
every other tool-use call site in this project, a malformed response raises
rather than silently defaulting open or closed.
"""

import asyncio
from contextvars import ContextVar
from typing import Any

from langfuse import observe
from openai import BadRequestError, OpenAI

from talk_to_your_data.llm import (
    ToolSpec,
    first_tool_call,
    get_client,
    get_model,
    reasoning_config,
    to_openai_tool,
)
from talk_to_your_data.tracing import record_generation

# ask_question runs the scope check concurrently with the supervisor graph (saves the
# ~2s the check used to add before any work started). To keep the original guarantee --
# an out-of-scope question never touches the data layer -- the graph's first data access
# (sql_agent's MCP tool calls) waits on this gate, which ask_question resolves with the
# scope verdict. No gate set (e.g. tests calling run_sql_agent directly) means no wait.
scope_gate: ContextVar["asyncio.Future[bool] | None"] = ContextVar("scope_gate", default=None)


class ScopeRefused(RuntimeError):
    pass


async def await_scope_clearance() -> None:
    gate = scope_gate.get()
    if gate is not None and not await asyncio.shield(gate):
        raise ScopeRefused("question was judged out of scope")


SCOPE_CHECK_TOOL: ToolSpec = {
    "name": "check_scope",
    "description": (
        "Decide whether this question can be answered using the Olist e-commerce dataset."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "in_scope": {"type": "boolean"},
            "reasoning": {"type": "string"},
        },
        "required": ["in_scope", "reasoning"],
    },
}

SCOPE_DESCRIPTION = (
    "This system answers business questions about an e-commerce dataset (Olist): "
    "orders, order items, products, product categories, customers, sellers, "
    "reviews, payments, and delivery timing/logistics -- covering revenue, order "
    "counts, review scores, delivery times, and breakdowns or trends over any of "
    "those. It cannot answer general-knowledge questions, questions about anything "
    "outside this dataset (weather, other companies, current events), requests to "
    "execute arbitrary code or commands, or requests about data this system "
    "doesn't have (individual customer identities, marketing spend, employee data)."
)


@observe(name="check_scope", as_type="generation")
def check_scope(
    question: str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> tuple[bool, str]:
    client = client or get_client()
    model = model or get_model()
    prompt = (
        f"{SCOPE_DESCRIPTION}\n\n"
        f"Question: {question}\n\n"
        "Decide if this question is in scope. When genuinely ambiguous, prefer "
        "in_scope=true and let the downstream agent attempt it, rather than "
        "refusing something that might be answerable. You must call check_scope "
        "to respond."
    )
    try:
        response = client.responses.create(
            model=model,
            max_output_tokens=8192,
            reasoning=reasoning_config(),
            tools=[to_openai_tool(SCOPE_CHECK_TOOL)],
            input=[{"role": "user", "content": prompt}],
        )
    except BadRequestError as e:
        # Azure's prompt filter (jailbreak detection etc.) rejects before the model
        # runs -- for a scope check that is a refusal, not an error.
        if getattr(e, "code", None) == "content_filter":
            return False, "blocked by the platform content filter"
        raise
    record_generation(response)
    tool_use = first_tool_call(response)
    if tool_use is None:
        raise RuntimeError("scope_guard: model did not call check_scope")
    result: dict[str, Any] = dict(tool_use.arguments)
    return bool(result["in_scope"]), str(result.get("reasoning", ""))
