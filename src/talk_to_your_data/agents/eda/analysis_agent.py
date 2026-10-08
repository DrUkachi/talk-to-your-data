"""LLM picks a lens (+ params), code computes the stats -- same split as Phase 2's
compiler and Phase 3's fix strategies. Discovered empirically while building this:
the semantic layer's query_metric has no date-range filter (only categorical IN
lists), so a time-series question like "in 2017" comes back with every period the
data has. Rather than extend Phase 2's compiler for this, the classification step
also extracts an optional start/end range when the question names one, and the
trend computation filters to it in pandas -- the same "LLM supplies judgment+params,
code computes deterministically" split, just applied one level downstream.
"""

import json
import os
from datetime import datetime
from typing import Any

import anthropic
from langfuse import observe

from talk_to_your_data.tracing import record_generation

from .state import AnalysisLens, AnalysisResult, SqlResult

CLASSIFY_LENS_TOOL: anthropic.types.ToolParam = {
    "name": "classify_lens",
    "description": (
        "Classify which analytical lens fits this query result, and extract any "
        "parameters it needs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "lens": {"type": "string", "enum": [lens.value for lens in AnalysisLens]},
            "start": {
                "type": "string",
                "description": (
                    "ISO date (time_series_trend only) -- set ONLY if the question "
                    "names a specific start of range (e.g. 'since June'); otherwise omit."
                ),
            },
            "end": {
                "type": "string",
                "description": "ISO date (time_series_trend only), same rule as start.",
            },
            "reasoning": {"type": "string"},
        },
        "required": ["lens", "reasoning"],
    },
}


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )


@observe(name="classify_lens", as_type="generation")
def _classify(
    question: str,
    sql_result: SqlResult,
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    client = client or _client()
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    rows = sql_result.rows
    sample = rows[:5] + rows[-5:] if len(rows) > 10 else rows
    prompt = (
        "A business question was answered with this query result. Classify which "
        "analytical lens fits best:\n"
        "- time_series_trend: there's a 'period' column -- a time series.\n"
        "- category_breakdown: a non-period dimension column plus a metric value, "
        "with more than one row.\n"
        "- none: a single row/value -- nothing to trend or break down.\n"
        "You must call classify_lens to respond.\n\n"
        f"Question: {question}\n"
        f"Columns: {sql_result.columns}\n"
        f"Row count: {len(rows)}\n"
        f"Sample rows: {json.dumps(sample, default=str)}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=512,
        tools=[CLASSIFY_LENS_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )
    record_generation(response)
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("analysis_agent: model did not call classify_lens")
    return dict(tool_use.input)  # type: ignore[arg-type]


def compute_trend_stats(
    rows: list[dict[str, Any]], start: str | None, end: str | None
) -> dict[str, Any]:
    filtered = rows
    if start or end:
        start_dt = datetime.fromisoformat(start) if start else None
        end_dt = datetime.fromisoformat(end) if end else None
        filtered = [
            r
            for r in rows
            if (start_dt is None or datetime.fromisoformat(r["period"]) >= start_dt)
            and (end_dt is None or datetime.fromisoformat(r["period"]) <= end_dt)
        ]
    if not filtered:
        return {"error": "no rows in the requested range"}

    filtered = sorted(filtered, key=lambda r: r["period"])
    metric_col = next(c for c in filtered[0] if c != "period")
    first_value = filtered[0][metric_col]
    last_value = filtered[-1][metric_col]
    pct_change = ((last_value - first_value) / first_value * 100) if first_value else None
    direction = "flat"
    if pct_change is not None:
        direction = "up" if pct_change > 1 else ("down" if pct_change < -1 else "flat")

    return {
        "periods_used": len(filtered),
        "first_period": filtered[0]["period"],
        "last_period": filtered[-1]["period"],
        "first_value": first_value,
        "last_value": last_value,
        "pct_change": pct_change,
        "direction": direction,
    }


def compute_breakdown_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"error": "no rows"}
    metric_col = next(c for c, v in rows[0].items() if isinstance(v, int | float))
    ranked = sorted(rows, key=lambda r: r[metric_col], reverse=True)
    total = sum(r[metric_col] for r in rows) or None

    def _entry(row: dict[str, Any]) -> dict[str, Any]:
        entry = dict(row)
        if total:
            entry["share_pct"] = row[metric_col] / total * 100
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
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> AnalysisResult:
    classification = _classify(question, sql_result, client=client, model=model)
    lens = AnalysisLens(classification["lens"])

    if lens == AnalysisLens.TIME_SERIES_TREND:
        stats = compute_trend_stats(
            sql_result.rows, classification.get("start"), classification.get("end")
        )
    elif lens == AnalysisLens.CATEGORY_BREAKDOWN:
        stats = compute_breakdown_stats(sql_result.rows)
    else:
        stats = {}

    return AnalysisResult(lens=lens, stats=stats, notes=classification.get("reasoning", ""))
