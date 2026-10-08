"""FastMCP server: the only way agents are allowed to touch Postgres.

4 tools: list_metrics, describe_metric, query_metric (semantic layer), and run_sql
(escape hatch, deliberately minimal -- see its docstring). Runs over HTTP, not
stdio, since later phases' agents and the FastAPI/Slack services are separate
long-running processes that need to reach this one over the network.

    uv run python -m talk_to_your_data.mcp_server.server
"""

import os
import re
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import text

from talk_to_your_data.db import readonly_engine
from talk_to_your_data.semantic_layer.compiler import ALLOWED_GRAINS, compile_metric
from talk_to_your_data.semantic_layer.registry import DIMENSIONS, METRICS, MODELS_REGISTRY

mcp = FastMCP(name="talk-to-your-data")

_SELECT_OR_WITH = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def _run(stmt: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with readonly_engine().connect() as conn:
        result = conn.execute(stmt, params or {})
        columns = list(result.keys())
        rows = [dict(row._mapping) for row in result]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


@mcp.tool
def list_metrics() -> list[dict[str, Any]]:
    """List every available metric with its model, grain, and description."""
    return [
        {
            "name": m.name,
            "description": m.description,
            "model": m.model,
            "grain": MODELS_REGISTRY[m.model].grain,
        }
        for m in METRICS.values()
    ]


@mcp.tool
def describe_metric(metric: str) -> dict[str, Any]:
    """Describe a metric: aggregation, dimensions, default filters, supported time grains."""
    if metric not in METRICS:
        raise ValueError(f"Unknown metric '{metric}'. Call list_metrics() for available metrics.")
    m = METRICS[metric]
    model = MODELS_REGISTRY[m.model]
    return {
        "name": m.name,
        "description": m.description,
        "model": m.model,
        "grain": model.grain,
        "aggregation": f"{m.agg}({m.measure})",
        "available_dimensions": {name: DIMENSIONS[name].description for name in model.dimensions},
        "default_filters": m.default_filters,
        "supported_time_grains": list(ALLOWED_GRAINS),
    }


@mcp.tool
def query_metric(
    metric: str,
    dimensions: list[str] | None = None,
    filters: dict[str, list[str]] | None = None,
    time_grain: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Compile a metric query from the semantic layer and run it read-only.

    Returns the SQL used alongside the result.
    """
    stmt, sql_text = compile_metric(
        metric, dimensions=dimensions, filters=filters, time_grain=time_grain, limit=limit
    )
    return {"sql": sql_text, **_run(stmt)}


@mcp.tool
def run_sql(query: str, limit: int = 100) -> dict[str, Any]:
    """Run a read-only, single-statement SQL query against the raw schema.

    Only SELECT/WITH is allowed and results are capped at `limit` rows. This is
    deliberately minimal -- no parsing/allow-listing beyond that -- the full
    guardrail suite (row limits as policy, PII masking, out-of-scope refusal) is
    Phase 6 scope. Prefer query_metric when a metric already covers the question.
    """
    stripped = query.strip().rstrip(";")
    if ";" in stripped:
        raise ValueError("Only a single SQL statement is allowed.")
    if not _SELECT_OR_WITH.match(stripped):
        raise ValueError("Only SELECT/WITH statements are allowed.")
    wrapped = text(f"SELECT * FROM ({stripped}) AS run_sql_subquery LIMIT :limit")
    result = _run(wrapped, {"limit": limit})
    return {"sql": f"{stripped} -- wrapped with LIMIT {limit}", **result}


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("MCP_SERVER_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_SERVER_PORT", "8000")),
    )
