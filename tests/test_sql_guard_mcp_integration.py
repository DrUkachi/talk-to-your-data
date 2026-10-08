"""Proves the guardrail is actually wired into the live MCP server, not just
tested in isolation -- a data-modifying CTE rejected against the real DB, and
masking actually applied to a real geolocation query.
"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from talk_to_your_data.guardrails.sql_guard import MAX_ROW_LIMIT
from talk_to_your_data.mcp_server.server import mcp

pytestmark = pytest.mark.integration


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


async def test_run_sql_rejects_data_modifying_cte_against_live_db(client):
    with pytest.raises(ToolError):
        await client.call_tool(
            "run_sql",
            {"query": "WITH x AS (DELETE FROM raw.orders RETURNING *) SELECT count(*) FROM x"},
        )


async def test_run_sql_rejects_cross_schema_access(client):
    with pytest.raises(ToolError):
        await client.call_tool("run_sql", {"query": "SELECT * FROM findings.findings"})


async def test_run_sql_masks_geolocation_columns(client):
    result = await client.call_tool(
        "run_sql",
        {
            "query": (
                "SELECT geolocation_lat, geolocation_lng FROM raw.geolocation "
                "WHERE geolocation_lat IS NOT NULL LIMIT 5"
            )
        },
    )
    for row in result.data["rows"]:
        lat_str = str(row["geolocation_lat"])
        decimals = lat_str.split(".")[-1] if "." in lat_str else ""
        assert len(decimals) <= 1, f"expected rounded lat, got {row['geolocation_lat']}"


async def test_run_sql_clamps_row_limit_to_policy_ceiling(client):
    result = await client.call_tool(
        "run_sql", {"query": "SELECT * FROM raw.order_items", "limit": 999_999}
    )
    assert result.data["row_count"] <= MAX_ROW_LIMIT
