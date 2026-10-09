"""propose_fixes: the only place an LLM touches this pipeline. Tool-use gets
structured output instead of parsing free text. The `client` param exists so unit
tests can inject a stub with no real OpenAI/Foundry credentials -- only the
explicitly `llm`-marked tests call the real API.

Note: forced tool_choice (`{"type": "tool", ...}` / `"any"`) returns a 400 on this
Foundry deployment ("not supported for this model") -- confirmed directly against
the previous Claude deployments, not assumed; not re-tested against GPT-6.1-Sol, so
the default is kept. Falls back to the default
`"auto"` tool_choice plus an explicit prompt instruction, which empirically still
calls the tool reliably; `propose_fixes` raises a clear error if the model responds
with no tool call instead of silently returning nothing.
"""

import json
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

from .state import FixProposal, FixStrategy, ProfileFinding

PROPOSE_FIXES_TOOL: ToolSpec = {
    "name": "propose_fixes",
    "description": (
        "Propose a fix for each data-quality finding that warrants one. Not every "
        "finding needs a fix -- omit any you judge should be left alone or need "
        "human investigation instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "strategy": {"type": "string", "enum": [s.value for s in FixStrategy]},
                        "params": {
                            "type": "object",
                            "description": (
                                "Strategy-specific. null_out_impossible_value: "
                                "{'null_column': <column to null>}. clip_outlier: "
                                "{'lower': <num>, 'upper': <num>}. Others: {}."
                            ),
                        },
                        "rationale": {"type": "string"},
                        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["finding_id", "strategy", "rationale", "risk"],
                },
            }
        },
        "required": ["proposals"],
    },
}


def _build_prompt(findings: list[ProfileFinding]) -> str:
    findings_json = json.dumps([f.model_dump() for f in findings], indent=2, default=str)
    return (
        "You are reviewing data-quality findings from an automated profiler of an "
        "e-commerce database. For each finding that warrants a fix, propose exactly "
        "one strategy with the parameters it needs. Use the finding's `details` to "
        "decide parameters (e.g. for an ordering violation, `details.predicate` "
        "already identifies the bad rows; for null_out_impossible_value you still "
        "need to choose which of the two columns in the violation is more likely "
        "wrong). Skip findings where a fix isn't safe to apply automatically -- "
        "it's fine to propose fewer fixes than findings. You must call the "
        "propose_fixes tool to respond -- do not reply in plain text.\n\n"
        f"Findings:\n{findings_json}"
    )


@observe(name="propose_fixes", as_type="generation")
def propose_fixes(
    findings: list[ProfileFinding],
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> list[FixProposal]:
    if not findings:
        return []
    client = client or get_client()
    model = model or get_model()

    response = client.responses.create(
        model=model,
        max_output_tokens=8192,
        reasoning=reasoning_config(),
        tools=[to_openai_tool(PROPOSE_FIXES_TOOL)],
        input=[{"role": "user", "content": _build_prompt(findings)}],
    )
    record_generation(response)
    tool_use = first_tool_call(response)
    if tool_use is None:
        raise RuntimeError(
            "propose_fixes: model did not call the propose_fixes tool. "
            f"Finish reason: {response.status}"
        )
    raw_proposals: list[dict[str, Any]] = tool_use.arguments["proposals"]
    return [FixProposal.model_validate(p) for p in raw_proposals]
