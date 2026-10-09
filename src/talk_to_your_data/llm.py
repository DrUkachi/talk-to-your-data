"""The single place this project talks to its main LLM (GPT-6.1-Sol, served from
Azure Foundry's OpenAI-compatible endpoint). Every call site gets its client,
model name, tool definitions and tool-call parsing from here, so swapping the
model/provider again is a change to this file plus env vars -- not ten modules.

Env (same names Codex CLI uses -- see docs/codex.md -- so both share one config):
    OPENAI_API_KEY   Foundry key
    OPENAI_BASE_URL  e.g. https://<resource>.openai.azure.com/openai/v1
    OPENAI_MODEL     deployment name; defaults to gpt-6.1-sol
    OPENAI_REASONING_EFFORT  none|low|medium|high; defaults to low

Uses the Responses API (gpt-6.1-sol rejects function tools on /chat/completions
while reasoning is on) with `tools` and the default `tool_choice="auto"`
(no forced tool_choice -- call sites instruct tool use in the prompt and raise
if no tool call comes back, as before the provider swap).
"""

import json
import os
from dataclasses import dataclass
from typing import Any, TypedDict

from openai import OpenAI
from openai.types.responses import FunctionToolParam, Response
from openai.types.shared_params import Reasoning

DEFAULT_MODEL = "gpt-6.1-sol"

ToolParam = FunctionToolParam


class ToolSpec(TypedDict):
    """Provider-neutral tool definition -- the same name/description/JSON-Schema
    shape MCP tools already have, converted at call time by `to_openai_tool`."""

    name: str
    description: str
    input_schema: dict[str, Any]


def get_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )


def reasoning_config() -> Reasoning:
    """Low effort by default: every call site is a short tool-calling step, and
    default (medium) reasoning pushed p50 latency past the 30s eval threshold."""
    return {"effort": os.environ.get("OPENAI_REASONING_EFFORT") or "low"}  # type: ignore[typeddict-item]


def get_model() -> str:
    return os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


def to_openai_tool(spec: ToolSpec) -> ToolParam:
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec["description"],
        "parameters": spec["input_schema"],
        "strict": False,
    }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


def tool_calls(response: Response) -> list[ToolCall]:
    return [
        ToolCall(id=item.call_id, name=item.name, arguments=json.loads(item.arguments or "{}"))
        for item in response.output
        if item.type == "function_call"
    ]


def first_tool_call(response: Response) -> ToolCall | None:
    calls = tool_calls(response)
    return calls[0] if calls else None
