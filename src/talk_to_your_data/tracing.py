"""Shared helper for attaching real model/usage/cost from a Responses-API response
to the current Langfuse generation observation (the span created by
@observe(as_type="generation") around each of this project's direct LLM
call sites: propose_fixes, run_sql_agent's per-turn calls, classify_lens,
write_finding, _route, check_scope).

Explicit, not auto-instrumented. Tested `opentelemetry-instrumentation-anthropic`
(Langfuse's own recommended path, when this project was on Claude) for real
against a running Foundry endpoint: it produces correctly-typed, correctly-nested
spans, but querying one back left model/usage/cost/input/output all unpopulated in
default config. This project has a small, fixed number of LLM call sites, all already behind named
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

from langfuse import get_client
from openai.types.responses import Response

# USD per token as (input, output). Not derived from Langfuse's own model-price
# registry, since Foundry deployment names aren't guaranteed to resolve against
# whatever model names Langfuse has on file. gpt-6.1-sol is the Global, short-context
# tier ($2 / $10 per 1M); Data Zone ($2.20 / $11) and long-context ($4 / $15) tiers,
# and the cheaper cached-input rate, are not modeled -- cost is a slight over-estimate
# for cached prompts. The Claude entries are kept so older traces/tests still price.
PRICING_PER_TOKEN: dict[str, tuple[float, float]] = {
    "gpt-6.1-sol": (2.00 / 1_000_000, 10.00 / 1_000_000),
    "claude-opus-5-5": (4.00 / 1_000_000, 20.00 / 1_000_000),
    "claude-sonnet-5-5": (2.00 / 1_000_000, 10.00 / 1_000_000),
}


def record_generation(response: Response) -> None:
    """Call right after a client.responses.create(...) inside a function
    decorated with @observe(as_type="generation"), to attach the real
    model/usage/cost to that span."""
    usage = response.usage
    input_tokens = usage.input_tokens if usage else 0
    output_tokens = usage.output_tokens if usage else 0
    usage_details = {"input": input_tokens, "output": output_tokens}
    cost_details = None
    pricing = PRICING_PER_TOKEN.get(response.model)
    if pricing is not None:
        input_price, output_price = pricing
        cost_details = {
            "input": input_tokens * input_price,
            "output": output_tokens * output_price,
        }
    get_client().update_current_generation(
        model=response.model,
        usage_details=usage_details,
        cost_details=cost_details,
    )
