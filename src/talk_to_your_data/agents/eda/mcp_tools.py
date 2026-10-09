"""Adapts the Phase 2 MCP server's tools to the LLM's tool-calling format, and
wraps calling one through a fastmcp Client. The sql_agent is the only thing
that imports this -- it's the one place this project's "LLM" meets Postgres,
and it only ever does so through the MCP server's tools (list_metrics,
describe_metric, query_metric, run_sql), never a direct DB connection.
"""

import os
from typing import Any

from fastmcp import Client
from mcp.types import Tool as MCPTool

from talk_to_your_data.llm import ToolParam, ToolSpec, to_openai_tool


def mcp_server_url() -> str:
    host = os.environ.get("MCP_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("MCP_SERVER_PORT", "8000")
    return f"http://{host}:{port}/mcp"


def mcp_tool_to_openai(tool: MCPTool) -> ToolParam:
    spec: ToolSpec = {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema,
    }
    return to_openai_tool(spec)


async def list_openai_tools(client: Client) -> list[ToolParam]:
    tools = await client.list_tools()
    return [mcp_tool_to_openai(t) for t in tools]


async def call_tool(client: Client, name: str, arguments: dict[str, Any]) -> Any:
    result = await client.call_tool(name, arguments)
    return result.data
