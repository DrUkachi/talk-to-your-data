"""PandasAI answers follow-up questions against an ALREADY-RETURNED finding's
data -- it never originates a query against Postgres itself; the semantic layer /
MCP path (sql_agent) stays the only way the first query ever hits the database.
The DataFrame it operates on is reconstructed from result_columns/result_rows
stored at save time (findings_store.py), not a fresh query -- a follow-up seeing
different data than what the user actually looked at would be confusing.

PandasAI goes through pandasai-litellm; LiteLLM's "openai/" provider accepts a
custom api_base, which is how it reaches the Foundry OpenAI-compatible endpoint.

response.to_dict() shape (also confirmed against the real model, not guessed):
{"value": ..., "type": "string"|"number"|"dataframe"|"chart", "last_code_executed":
str, "error": None}. last_code_executed becomes this Finding's `sql` field -- it's
pandas/SQL-over-DuckDB code PandasAI generated, not hand-written SQL, but it's the
same transparency principle as everywhere else: show the exact thing that produced
the number. A "chart" response's value is a local PNG path -- the first time
Finding.chart_ref is ever populated; Phase 6 still needs to get it somewhere a
Slack client can actually fetch it (see docs/architecture.md's failure-modes table).

Tracing: this is the one LLM call site PandasAI owns internally (df.chat()
never hands back the raw response), so the explicit record_generation() pattern used
everywhere else in this project can't apply -- there's no response object to read
model/usage/cost off of. LiteLLM ships its own Langfuse integration for exactly
this (`litellm.success_callback`/`failure_callback`), verified for real against the
Foundry endpoint before relying on it. It logs independently of this project's
`@observe`-based spans, so the resulting generation lands as its own trace rather
than nested under answer_followup's -- a real gap (no parent/child link), accepted
rather than solved, since giving it one would mean reconfiguring PandasAI's global
LLM singleton per-call with the current trace/observation id, for a follow-up path
that already carries a documented best-effort posture elsewhere in Phase 6a.

Fresh-data fallback (found via real Slack usage, Phase 6c): "already-returned data
only" is correct for a genuine recut of what's already shown (a %, a trend of the
same rows, a filter) but wrong for a follow-up that actually needs a different
query -- e.g. asking for a category breakdown after the parent finding was a single
aggregate scalar. PandasAI itself answers honestly in that case ("the data doesn't
have a category column"), it just can't do anything about it, since by design it
never queries Postgres. `_needs_fresh_data` is a small classifier (same tool-use
pattern as scope_guard/analysis_agent) that reads PandasAI's own answer and decides
whether it's reporting a genuine answer or a data gap; only on a data gap does this
fall back to `ask_question` -- a full sql_agent query against the real semantic
layer, scoped to the same thread so the Slackbot's thread-based follow-up
detection keeps working on whatever comes next.
"""

import asyncio
import os
import threading
from typing import Any

import litellm
import pandas as pd
import pandasai as pai
from langfuse import observe
from openai import OpenAI
from pandasai_litellm import LiteLLM

from talk_to_your_data.llm import (
    ToolSpec,
    first_tool_call,
    get_client,
    get_model,
    reasoning_config,
    to_openai_tool,
)
from talk_to_your_data.tracing import record_generation

from .ask import ask_question
from .findings_store import get_finding, save_finding
from .narrative_agent import write_finding
from .state import Finding, SqlResult

_configure_lock = threading.Lock()
_configured = False


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    with _configure_lock:
        if _configured:
            return
        model = get_model()
        llm = LiteLLM(
            model=f"openai/{model}",
            api_key=os.environ["OPENAI_API_KEY"],
            api_base=os.environ.get("OPENAI_BASE_URL") or None,
        )
        pai.config.set({"llm": llm})
        litellm.success_callback = ["langfuse"]
        litellm.failure_callback = ["langfuse"]
        _configured = True


def _normalize_response(response_dict: dict[str, Any]) -> tuple[SqlResult, str | None]:
    """Pure (no LLM/network) so this is unit-testable without a real PandasAI call."""
    value = response_dict["value"]
    code = response_dict.get("last_code_executed") or ""
    response_type = response_dict["type"]

    if response_type == "chart":
        return SqlResult(sql=code, columns=["answer"], rows=[{"answer": str(value)}]), str(value)

    if response_type == "dataframe":
        df = value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
        return SqlResult(sql=code, columns=list(df.columns), rows=df.to_dict("records")), None

    return SqlResult(sql=code, columns=["answer"], rows=[{"answer": value}]), None


NEEDS_FRESH_DATA_TOOL: ToolSpec = {
    "name": "assess_followup_answer",
    "description": (
        "Decide whether a follow-up answer was limited because the already-shown "
        "data lacks something the question needs (a column, dimension, or grain), "
        "as opposed to a genuine answer computed from the available data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "needs_fresh_data": {
                "type": "boolean",
                "description": (
                    "True if the answer says it can't fully address the question "
                    "because the available data is missing something it needs. "
                    "False if it's a genuine answer computed from the data."
                ),
            },
            "reasoning": {"type": "string"},
        },
        "required": ["needs_fresh_data", "reasoning"],
    },
}


@observe(name="assess_followup_answer", as_type="generation")
def _needs_fresh_data(
    question: str,
    answer: str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> bool:
    client = client or get_client()
    model = model or get_model()
    prompt = (
        "A follow-up question was answered using only data already shown to the "
        "user (not a fresh query). Decide whether the answer indicates that data "
        "was insufficient -- missing a column, dimension, or grain the question "
        "actually needs -- versus a genuine answer computed from it. You must call "
        "assess_followup_answer to respond.\n\n"
        f"Question: {question}\nAnswer: {answer}"
    )
    response = client.responses.create(
        model=model,
        max_output_tokens=8192,
        reasoning=reasoning_config(),
        tools=[to_openai_tool(NEEDS_FRESH_DATA_TOOL)],
        input=[{"role": "user", "content": prompt}],
    )
    record_generation(response)
    tool_use = first_tool_call(response)
    if tool_use is None:
        raise RuntimeError("answer_followup: model did not call assess_followup_answer")
    return bool(tool_use.arguments["needs_fresh_data"])


@observe(name="answer_followup", as_type="agent")
async def answer_followup(finding_id: str, question: str) -> Finding:
    parent = get_finding(finding_id)
    if parent is None:
        raise ValueError(f"no finding with id {finding_id!r}")

    _ensure_configured()
    df = pai.DataFrame(pd.DataFrame(parent["result_rows"], columns=parent["result_columns"]))
    response = await asyncio.to_thread(df.chat, question)
    response_dict = response.to_dict()
    sql_result, chart_ref = _normalize_response(response_dict)

    # Only "string"/"number" answers are free text that could be reporting a data
    # gap -- a "dataframe"/"chart" response inherently means PandasAI already
    # computed real tabular output, so there's nothing to fall back from.
    if response_dict["type"] in ("string", "number") and _needs_fresh_data(
        question, str(sql_result.rows[0]["answer"])
    ):
        # sql_agent has no memory of this conversation -- the bare follow-up text
        # ("break that down by category") is meaningless on its own. Confirmed
        # against the real model, not assumed, and in two stages: a bare
        # follow-up made sql_agent give up with no tool call at all; a
        # parenthetical "(follow-up on: ...)" gave it context but got the
        # *original* question's answer re-run verbatim, the follow-up's actual
        # ask effectively ignored. Leading with the original question as
        # explicit background, then an imperative "now answer" for the follow-up,
        # is what actually produced a real category breakdown instead of either
        # failure mode.
        contextual_question = (
            f'A previous question was: "{parent["question"]}". '
            f"As a follow-up, now answer: {question}"
        )
        return await ask_question(
            contextual_question, thread_id=parent["thread_id"], display_question=question
        )

    finding = write_finding(question, sql_result, analysis_result=None)
    finding.chart_ref = chart_ref
    save_finding(parent["thread_id"], finding, sql_result, parent_finding_id=finding_id)
    return finding
