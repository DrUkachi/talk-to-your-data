from mcp.types import Tool as MCPTool

from talk_to_your_data.agents.eda.mcp_tools import mcp_tool_to_anthropic


def test_adapts_name_description_and_schema():
    tool = MCPTool(
        name="query_metric",
        description="Run a metric query.",
        input_schema={
            "type": "object",
            "properties": {"metric": {"type": "string"}},
            "required": ["metric"],
        },
    )
    adapted = mcp_tool_to_anthropic(tool)
    assert adapted["name"] == "query_metric"
    assert adapted["description"] == "Run a metric query."
    assert adapted["input_schema"]["properties"]["metric"]["type"] == "string"


def test_handles_missing_description():
    tool = MCPTool(name="x", description=None, input_schema={"type": "object", "properties": {}})
    adapted = mcp_tool_to_anthropic(tool)
    assert adapted["description"] == ""
