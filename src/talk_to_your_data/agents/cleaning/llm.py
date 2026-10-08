"""propose_fixes: the only place an LLM touches this pipeline. Tool-use gets
structured output instead of parsing free text. The `client` param exists so unit
tests can inject a stub with no real Anthropic/Foundry credentials -- only the
explicitly `llm`-marked tests call the real API.

Note: forced tool_choice (`{"type": "tool", ...}` / `"any"`) returns a 400 on this
Foundry deployment ("not supported for this model") -- confirmed directly against
both claude-opus-5-5 and claude-sonnet-5, not assumed. Falls back to the default
`"auto"` tool_choice plus an explicit prompt instruction, which empirically still
calls the tool reliably; `propose_fixes` raises a clear error if the model responds
with no tool_use block instead of silently returning nothing.
"""

import json
import os
from typing import Any

import anthropic

from .state import FixProposal, FixStrategy, ProfileFinding

PROPOSE_FIXES_TOOL: anthropic.types.ToolParam = {
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


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )


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


def propose_fixes(
    findings: list[ProfileFinding],
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> list[FixProposal]:
    if not findings:
        return []
    client = client or get_client()
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        tools=[PROPOSE_FIXES_TOOL],
        messages=[{"role": "user", "content": _build_prompt(findings)}],
    )
    tool_use = next((block for block in response.content if block.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError(
            "propose_fixes: model did not call the propose_fixes tool. "
            f"Response content types: {[b.type for b in response.content]}"
        )
    raw_proposals: list[dict[str, Any]] = tool_use.input["proposals"]  # type: ignore[attr-defined,assignment]
    return [FixProposal.model_validate(p) for p in raw_proposals]
