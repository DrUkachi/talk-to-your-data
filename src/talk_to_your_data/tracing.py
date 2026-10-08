"""Shared helper for attaching real model/usage/cost from an Anthropic response
to the current Langfuse generation observation (the span created by
@observe(as_type="generation") around each of this project's direct Anthropic
call sites: propose_fixes, run_sql_agent's per-turn calls, classify_lens,
write_finding, _route, check_scope).

Explicit, not auto-instrumented. Tested `opentelemetry-instrumentation-anthropic`
(Langfuse's own recommended path) for real against a running Foundry endpoint:
it produces correctly-typed, correctly-nested spans, but querying one back left
model/usage/cost/input/output all unpopulated in default config. This project
has a small, fixed number of Anthropic call sites, all already behind named
wrapper functions -- explicit instrumentation is both necessary (for the data)
and sufficient (no other call sites exist), so this skips the auto-instrumentor
rather than running both and getting duplicate, emptier spans for the same call.

Verified the SDK itself emits the right underlying OTel span attributes
(`langfuse.observation.model.name`, `langfuse.observation.usage_details`) by
inspecting a raw span locally. End-to-end confirmation that Langfuse's own
query-back API exposes these (as opposed to the Cloud UI, which this
environment has no way to check without a browser) could not be confirmed
within a reasonable wait -- likely an async enrichment delay, or a rough edge
in their v2 observations API (shipped alongside this SDK in 2026) -- not
something fixable from this project's side. Check cloud.langfuse.com's UI
directly once this is deployed somewhere with browser access.
"""

import anthropic
from langfuse import get_client

# Current Anthropic pricing, USD per token (see docs/architecture.md's cost
# rubric for the source). Not derived from Langfuse's own model-price registry,
# since claude-opus-5-5/claude-sonnet-5-5 served via Microsoft Foundry aren't
# guaranteed to resolve against whatever provider/model names Langfuse has on
# file for cost auto-calculation.
PRICING_PER_TOKEN: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00 / 1_000_000, 20.00 / 1_000_000),
    "claude-sonnet-5-5": (2.00 / 1_000_000, 10.00 / 1_000_000),
}


def record_generation(response: anthropic.types.Message) -> None:
    """Call right after a client.messages.create(...) inside a function
    decorated with @observe(as_type="generation"), to attach the real
    model/usage/cost to that span."""
    usage_details = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    }
    cost_details = None
    pricing = PRICING_PER_TOKEN.get(response.model)
    if pricing is not None:
        input_price, output_price = pricing
        cost_details = {
            "input": response.usage.input_tokens * input_price,
            "output": response.usage.output_tokens * output_price,
        }
    get_client().update_current_generation(
        model=response.model,
        usage_details=usage_details,
        cost_details=cost_details,
    )
