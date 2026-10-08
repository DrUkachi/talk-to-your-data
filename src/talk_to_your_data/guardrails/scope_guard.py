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

import os
from typing import Any

import anthropic

SCOPE_CHECK_TOOL: anthropic.types.ToolParam = {
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


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )


def check_scope(
    question: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> tuple[bool, str]:
    client = client or _client()
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    prompt = (
        f"{SCOPE_DESCRIPTION}\n\n"
        f"Question: {question}\n\n"
        "Decide if this question is in scope. When genuinely ambiguous, prefer "
        "in_scope=true and let the downstream agent attempt it, rather than "
        "refusing something that might be answerable. You must call check_scope "
        "to respond."
    )
    response = client.messages.create(
        model=model,
        max_tokens=256,
        tools=[SCOPE_CHECK_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("scope_guard: model did not call check_scope")
    result: dict[str, Any] = dict(tool_use.input)  # type: ignore[arg-type]
    return bool(result["in_scope"]), str(result.get("reasoning", ""))
