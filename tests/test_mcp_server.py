"""In-process FastMCP client -- no network/port needed. Requires Postgres up and
data loaded (query_metric/run_sql actually execute), plus the readonly role
(scripts/setup_db_roles.py).
"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from talk_to_your_data.mcp_server.server import mcp

pytestmark = pytest.mark.integration


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


async def test_lists_all_four_tools(client):
    tools = await client.list_tools()
    assert {t.name for t in tools} == {"list_metrics", "describe_metric", "query_metric", "run_sql"}


async def test_list_metrics_returns_nine_metrics(client):
    result = await client.call_tool("list_metrics", {})
    assert len(result.data) == 9


async def test_describe_metric_includes_default_filters(client):
    result = await client.call_tool("describe_metric", {"metric": "revenue"})
    assert result.data["default_filters"] == {"order_status": ["delivered"]}
    assert "product_category" in result.data["available_dimensions"]


async def test_describe_unknown_metric_raises(client):
    with pytest.raises(ToolError):
        await client.call_tool("describe_metric", {"metric": "no_such_metric"})


async def test_query_metric_returns_sql_and_result(client):
    result = await client.call_tool("query_metric", {"metric": "order_count"})
    assert result.data["row_count"] == 1
    assert "order_count" in result.data["sql"]
    assert result.data["rows"][0]["order_count"] == 99441


async def test_run_sql_executes_a_select(client):
    result = await client.call_tool("run_sql", {"query": "SELECT COUNT(*) AS n FROM raw.orders"})
    assert result.data["rows"][0]["n"] == 99441


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM raw.orders",
        "UPDATE raw.orders SET order_status = 'x'",
        "SELECT 1; DROP TABLE raw.orders",
    ],
)
async def test_run_sql_rejects_non_select(client, query):
    with pytest.raises(ToolError):
        await client.call_tool("run_sql", {"query": query})
