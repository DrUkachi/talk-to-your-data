"""Analysis = lens from the SHAPE of the result (code), optional date range from the
question (rules first, LLM only as a fallback), stats computed in code. The semantic
layer's query_metric has no date-range filter (only categorical IN lists), so a
time-series question like "in 2017" comes back with every period the data has; the
trend computation filters to the requested range in pandas-free Python instead of
extending Phase 2's compiler.

Latency note: the lens used to be an LLM call (~2s, every question). Shape decides it
reliably -- time + number -> trend, category + number -> breakdown, else none -- so the
model is now consulted only when a trend question names a range the rules can't parse
("since June", "last quarter"). Measured by the eval suite, not eyeballed.
"""

import re
from datetime import datetime
from typing import Any

from langfuse import observe
from openai import OpenAI

from talk_to_your_data.llm import (
    ToolSpec,
    first_tool_call,
    get_client,
    get_model,
    reasoning_config,
    to_openai_tool,
)
from talk_to_your_data.tracing import record_generation

from .charting import classify_columns, to_float
from .state import AnalysisLens, AnalysisResult, SqlResult

EXTRACT_RANGE_TOOL: ToolSpec = {
    "name": "extract_date_range",
    "description": "Extract the date range a question asks about, if it names one.",
    "input_schema": {
        "type": "object",
        "properties": {
            "start": {
                "type": "string",
                "description": "ISO date -- set ONLY if the question names a start of range.",
            },
            "end": {"type": "string", "description": "ISO date, same rule as start."},
        },
        "required": [],
    },
}

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_OPEN_RANGE_RE = re.compile(r"\b(since|from|after|starting)\s+(19\d{2}|20\d{2})\b", re.I)
_RANGE_WORDS_RE = re.compile(
    r"\b(since|from|between|until|before|after|last|past|previous|recent|ytd|"
    r"q[1-4]|quarter|week|"
    r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|"
    r"sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?)\b",
    re.I,
)


def infer_lens(sql_result: SqlResult) -> AnalysisLens:
    rows = sql_result.rows
    if len(rows) < 2:
        return AnalysisLens.NONE
    kinds = classify_columns(sql_result.columns, rows).values()
    if "numeric" not in kinds and "temporal" not in kinds:
        return AnalysisLens.NONE
    if "temporal" in kinds and "numeric" in kinds:
        return AnalysisLens.TIME_SERIES_TREND
    if "category" in kinds and "numeric" in kinds:
        return AnalysisLens.CATEGORY_BREAKDOWN
    return AnalysisLens.NONE


def extract_range_by_rules(question: str) -> tuple[str | None, str | None] | None:
    """Returns (start, end) when the question's range is simple enough to parse
    with certainty, (None, None) when it names no range, and None when it names one
    the rules can't resolve (caller then falls back to the LLM)."""
    years = sorted({int(y) for y in _YEAR_RE.findall(question)})
    open_range = _OPEN_RANGE_RE.search(question)
    if open_range and len(years) == 1 and not re.search(r"\b(to|until|through)\b", question, re.I):
        return f"{years[0]}-01-01", None
    if len(years) == 1 and not _RANGE_WORDS_RE.search(question):
        return f"{years[0]}-01-01", f"{years[0]}-12-31"
    if len(years) == 2 and re.search(r"\b(between|from)\b", question, re.I):
        return f"{years[0]}-01-01", f"{years[1]}-12-31"
    if years or _RANGE_WORDS_RE.search(question):
        return None
    return None, None


@observe(name="extract_date_range", as_type="generation")
def _extract_range_llm(
    question: str, *, client: OpenAI | None = None, model: str | None = None
) -> tuple[str | None, str | None]:
    client = client or get_client()
    model = model or get_model()
    prompt = (
        "Extract the date range this question asks about, as ISO dates (an end of "
        "'June 2017' is 2017-06-30). Omit start or end if the question doesn't bound "
        "that side. You must call extract_date_range to respond.\n\n"
        f"Question: {question}"
    )
    response = client.responses.create(
        model=model,
        max_output_tokens=8192,
        reasoning=reasoning_config(),
        tools=[to_openai_tool(EXTRACT_RANGE_TOOL)],
        input=[{"role": "user", "content": prompt}],
    )
    record_generation(response)
    tool_use = first_tool_call(response)
    if tool_use is None:
        return None, None
    return tool_use.arguments.get("start"), tool_use.arguments.get("end")


def compute_trend_stats(
    rows: list[dict[str, Any]], start: str | None, end: str | None
) -> dict[str, Any]:
    if not rows:
        return {"error": "no rows"}
    # query_metric names its time column "period", but run_sql results use whatever
    # the model chose ("month", "order_month", ...) -- detect it rather than assume.
    columns = list(rows[0])
    kinds = classify_columns(columns, rows)
    time_col = (
        "period"
        if "period" in rows[0]
        else next((c for c in columns if kinds[c] == "temporal"), None)
    )
    metric_col = next((c for c in columns if c != time_col and kinds[c] == "numeric"), None)
    if time_col is None or metric_col is None:
        return {"error": f"no time column and numeric column to trend in {columns}"}

    def _dt(row: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(str(row[time_col]))

    filtered = rows
    if start or end:
        start_dt = datetime.fromisoformat(start) if start else None
        end_dt = datetime.fromisoformat(end) if end else None
        filtered = [
            r
            for r in rows
            if (start_dt is None or _dt(r) >= start_dt) and (end_dt is None or _dt(r) <= end_dt)
        ]
    if not filtered:
        return {"error": "no rows in the requested range"}

    filtered = sorted(filtered, key=lambda r: str(r[time_col]))
    first_value = to_float(filtered[0][metric_col]) or 0.0
    last_value = to_float(filtered[-1][metric_col]) or 0.0
    pct_change = ((last_value - first_value) / first_value * 100) if first_value else None
    direction = "flat"
    if pct_change is not None:
        direction = "up" if pct_change > 1 else ("down" if pct_change < -1 else "flat")

    return {
        "periods_used": len(filtered),
        "first_period": str(filtered[0][time_col]),
        "last_period": str(filtered[-1][time_col]),
        "first_value": first_value,
        "last_value": last_value,
        "pct_change": pct_change,
        "direction": direction,
    }


def compute_breakdown_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"error": "no rows"}
    # MCP serializes Postgres numerics as strings, so "numeric" is judged by
    # parseability, not Python type (a bare next() here once raised StopIteration,
    # which surfaces as an opaque TypeError when it escapes asyncio.to_thread).
    kinds = classify_columns(list(rows[0]), rows)
    metric_col = next((c for c, k in kinds.items() if k == "numeric"), None)
    if metric_col is None:
        return {"error": f"no numeric column to break down in {list(rows[0])}"}
    values = {id(r): to_float(r[metric_col]) or 0.0 for r in rows}
    ranked = sorted(rows, key=lambda r: values[id(r)], reverse=True)
    total = sum(values.values()) or None

    def _entry(row: dict[str, Any]) -> dict[str, Any]:
        entry = dict(row)
        if total:
            entry["share_pct"] = values[id(row)] / total * 100
        return entry

    return {
        "metric_column": metric_col,
        "top": [_entry(r) for r in ranked[:3]],
        "bottom": [_entry(r) for r in ranked[-3:]],
    }


def analyze(
    question: str,
    sql_result: SqlResult,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> AnalysisResult:
    lens = infer_lens(sql_result)

    if lens == AnalysisLens.TIME_SERIES_TREND:
        window = extract_range_by_rules(question)
        if window is None:
            window = _extract_range_llm(question, client=client, model=model)
        stats = compute_trend_stats(sql_result.rows, window[0], window[1])
    elif lens == AnalysisLens.CATEGORY_BREAKDOWN:
        stats = compute_breakdown_stats(sql_result.rows)
    else:
        stats = {}

    return AnalysisResult(lens=lens, stats=stats, notes=f"lens inferred from result shape: {lens}")
