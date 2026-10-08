"""PandasAI answers follow-up questions against an ALREADY-RETURNED finding's
data -- it never originates a query against Postgres itself; the semantic layer /
MCP path (sql_agent) stays the only way the first query ever hits the database.
The DataFrame it operates on is reconstructed from result_columns/result_rows
stored at save time (findings_store.py), not a fresh query -- a follow-up seeing
different data than what the user actually looked at would be confusing.

No official pandasai-anthropic package exists; pandasai-litellm does, and LiteLLM's
"anthropic/" provider accepts a custom api_base -- confirmed against the real
aie-academy-hub Foundry endpoint before building this, not assumed.

response.to_dict() shape (also confirmed against the real model, not guessed):
{"value": ..., "type": "string"|"number"|"dataframe"|"chart", "last_code_executed":
str, "error": None}. last_code_executed becomes this Finding's `sql` field -- it's
pandas/SQL-over-DuckDB code PandasAI generated, not hand-written SQL, but it's the
same transparency principle as everywhere else: show the exact thing that produced
the number. A "chart" response's value is a local PNG path -- the first time
Finding.chart_ref is ever populated; Phase 6 still needs to get it somewhere a
Slack client can actually fetch it (see docs/architecture.md's failure-modes table).
"""

import os
import threading
from typing import Any

import pandas as pd
import pandasai as pai
from pandasai_litellm import LiteLLM

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
        model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
        llm = LiteLLM(
            model=f"anthropic/{model}",
            api_key=os.environ["ANTHROPIC_API_KEY"],
            api_base=os.environ.get("ANTHROPIC_BASE_URL") or None,
        )
        pai.config.set({"llm": llm})
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


def answer_followup(finding_id: str, question: str) -> Finding:
    parent = get_finding(finding_id)
    if parent is None:
        raise ValueError(f"no finding with id {finding_id!r}")

    _ensure_configured()
    df = pai.DataFrame(pd.DataFrame(parent["result_rows"], columns=parent["result_columns"]))
    response = df.chat(question)
    sql_result, chart_ref = _normalize_response(response.to_dict())

    finding = write_finding(question, sql_result, analysis_result=None)
    finding.chart_ref = chart_ref
    save_finding(parent["thread_id"], finding, sql_result, parent_finding_id=finding_id)
    return finding
