# Architecture

System design for the full 6-phase target, not just what's built so far. Phases
1-5 (foundation through PandasAI follow-ups), Phase 6a (guardrails), Phase 6b
(Langfuse tracing), and Phase 6c (Slackbot) are built and tested; Phase 6d (CI
eval) is marked **planned** below — this doc exists so Phase 6 has a design to
build against, not a blank page. The component diagram below hasn't been updated
to show the guardrails layer explicitly yet since it sits inside
`mcp-server`/`eda-api` rather than as a separate service — see the
failure-modes table for what's actually built there.

## Component diagram

```mermaid
flowchart TB
    subgraph External
        User["Stakeholder (Slack)"]
        A2AClient["External A2A client"]
    end

    subgraph Phase6["Phase 6 - planned, not yet built"]
        CIEval["CI eval suite - 40 questions"]
        Guardrails["Guardrails - SQL allow-list, PII masking, refusal"]
    end

    Langfuse["Langfuse tracing - built, Phase 6b"]

    subgraph SlackbotSvc["slackbot service - built, Phase 6c"]
        Slackbot["Bolt app - Socket Mode"]
    end

    subgraph EdaApi["eda-api service - Phase 4 and 5"]
        Ask["POST /ask"]
        A2ARoutes["A2A agent card and JSON-RPC"]
        FindingsRoutes["GET and POST /findings"]
        Supervisor["supervisor graph"]
        SqlAgent["sql_agent"]
        AnalysisAgent["analysis_agent"]
        NarrativeAgent["narrative_agent"]
        FollowupAgent["followup_agent - PandasAI, Phase 5"]
    end

    subgraph CleaningApi["cleaning-api service - Phase 3"]
        CleaningGraph["profile, propose, approve, apply, validate"]
    end

    subgraph Mcp["mcp-server service - Phase 2"]
        SemanticLayer["semantic layer - metrics.yaml and compiler"]
        McpTools["list_metrics, describe_metric, query_metric, run_sql"]
    end

    subgraph Postgres
        Raw[("raw schema")]
        Clean[("clean schema")]
        Findings[("findings schema")]
        LangGraphSchema[("langgraph schema - checkpoints")]
    end

    User --> Slackbot
    Slackbot --> Supervisor
    Slackbot --> FollowupAgent
    A2AClient --> A2ARoutes
    Ask --> Supervisor
    A2ARoutes --> Supervisor
    Supervisor --> SqlAgent
    Supervisor --> AnalysisAgent
    Supervisor --> NarrativeAgent
    SqlAgent --> McpTools
    NarrativeAgent --> Findings
    FindingsRoutes --> FollowupAgent
    FollowupAgent --> Findings
    FollowupAgent -.-> Supervisor
    McpTools --> SemanticLayer
    SemanticLayer --> Raw
    CleaningGraph --> Clean
    CleaningGraph --> Raw
    Supervisor -.-> LangGraphSchema
    CleaningGraph -.-> LangGraphSchema
    Supervisor -.-> Langfuse
    SqlAgent -.-> Langfuse
    AnalysisAgent -.-> Langfuse
    NarrativeAgent -.-> Langfuse
    FollowupAgent -.-> Langfuse
    CleaningGraph -.-> Langfuse
    CIEval -.-> EdaApi
    Slackbot -.-> Guardrails

    classDef planned stroke-dasharray: 5 5
    class CIEval,Guardrails planned
```

Two design throughlines worth calling out explicitly, since they're the reason
several components look the way they do:

1. **Agents never write SQL from scratch against a live connection.** The
   semantic layer compiles metric requests deterministically; `run_sql` is a
   narrow, read-only escape hatch; the cleaning agent's fix strategies render
   SQL from a fixed menu, never from LLM free text. The one write path
   (`cleaning_graph`) is a separate, internal pipeline — never reachable from
   natural-language input.
2. **Judgment and computation are separate steps, repeated at every layer**:
   the semantic layer compiler (Phase 2), the cleaning agent's fix strategies
   (Phase 3), the analysis agent's lens classification (Phase 4), and
   PandasAI's generated-but-executed-in-a-controlled-DataFrame code (Phase 5)
   all follow "LLM picks from a constrained menu or supplies parameters, code
   computes the result."

## Failure modes

These are drawn from what the build actually hit or deliberately bounded —
not a hypothetical list.

| Failure | Current behavior | Gap / what Phase 6 needs to do |
|---|---|---|
| MCP server unreachable | `sql_agent` raises `RuntimeError` after exhausting its tool-use loop; supervisor eventually hits `max_turns` and marks the run `failed` | **Built (Phase 6c).** All three entry points now catch broadly: REST returns a clean 502 instead of an uncaught 500, A2A and Slack post the error as a message — uniform, not just the A2A executor as before |
| Foundry/Anthropic API down or rate-limited | Same uncaught-exception gap as above at any LLM call site | **Built (Phase 6c).** Same uniform catch-and-report fix as above, applies at any LLM call site |
| Postgres unreachable | Any `app_engine()`/`readonly_engine()` call raises | **Built (Phase 6c).** Same uniform catch-and-report fix as above |
| Supervisor loop never converges | Hard-capped at `max_turns=8`, returns `status: "failed"` rather than looping forever | Working as intended; Phase 6 should decide what the Slackbot says on `failed` (currently `ask_question` just raises) |
| `sql_agent` tool-use loop never converges | Hard-capped at 6 turns, raises `RuntimeError` | Same |
| Cleaning-agent run left `awaiting_approval` forever | No TTL — the checkpoint just sits there | Not addressed; a real deployment needs either a TTL/cleanup job or to accept this as an acceptable operational characteristic for a low-volume internal tool |
| Semantic layer has no date-range filter | Phase 4's `analysis_agent` works around this *only* for `time_series_trend` questions (extracts a start/end and filters in pandas) | A simple point-value question naming a specific date range (not a trend) has no equivalent workaround — `sql_agent` would need to fall back to `run_sql` itself, which is possible but not guaranteed |
| PandasAI follow-up produces a chart | **Built (Phase 6c).** The Slackbot calls `answer_followup` directly in-process (not over HTTP to `eda-api`), so `chart_ref`'s local PNG path is on the Slackbot's own disk — uploaded via `files_upload_v2` in the same thread. Confirmed with a real file upload before relying on it, not just the OAuth scope. | None for Slack. The REST `/findings/{id}/followup` response still just returns the local path as-is — fine for this project's single-consumer-is-Slack scope, would need object storage for a REST client on a different host |
| `answer_followup` asked for data its parent finding doesn't have (e.g. "break that down by category" after a single aggregate) | **Built (Phase 6c), found via real Slack usage, not a written test.** Phase 5's "only ever see already-returned rows" guarantee is correct for a genuine recut but wrong here — there's nothing to recut. `_needs_fresh_data` classifies PandasAI's own answer as a reported data gap vs. a genuine answer; on a gap, falls back to a full `ask_question()` call, restating the parent question as explicit background first (two earlier phrasings were tried and failed against the real model before this one worked — see ROADMAP's Phase 6c summary) | None — covered. The fallback's finding has no `parent_finding_id` (it's not actually derived from the parent's data), by design |
| `run_sql` receives a data-modifying CTE | **Built (Phase 6a).** `sql_guard.validate_sql` walks the full AST, not just the top-level statement type (a data-modifying CTE's outer node parses as a harmless `Select`) — confirmed by parsing one, not assumed. Readonly DB role rejects it too (defense in depth), now redundantly. | None — covered, with tests proving both layers independently |
| A query asks for more rows than it should get | **Built (Phase 6a).** Hard ceiling (`MAX_ROW_LIMIT=1000`), clamped regardless of what's requested | None |
| Geolocation/zip columns leak via `run_sql` | **Built (Phase 6a), with a known, documented gap.** Masking matches OUTPUT column names — `SELECT geolocation_lat AS x` or `SELECT AVG(geolocation_lat)` both evade it entirely (no column provenance tracking through arbitrary SQL). `test_sql_guard.py` has a test *proving* the bypass exists, not just absence-of-bypass tests. | Would need column-provenance tracking or masked Postgres views to close properly — a real scope increase, deliberately not taken this phase |
| Out-of-scope question (weather, general knowledge, injection attempts) | **Built (Phase 6a).** `scope_guard.check_scope` gates `ask_question()` before the supervisor graph starts — verified against real in-scope questions (not falsely refused), real out-of-scope questions (refused), and adversarial/injection-flavored prompts against the classifier itself (held, but not proof against all such attempts — an LLM classifier is manipulable in principle; the structural backstop is that `sql_agent` can only ever reach Postgres through read-only MCP tools regardless of what the classifier decides) | Phase 6d's golden set is where borderline/ambiguous cases get rigorously scored, not just smoke-tested |
| Langfuse query-back doesn't expose model/usage/cost | **Built (Phase 6b).** Every direct Anthropic call site (`propose_fixes`, `run_sql_agent`'s per-turn loop, `classify_lens`, `write_finding`, `_route`, `check_scope`) is wrapped in `@observe(as_type="generation")` plus an explicit `record_generation()` call that forwards the real model/usage/cost onto the current span — not the auto-instrumentor (tested; it left those fields empty by default). Verified the SDK emits the right OTel span attributes locally, and separately confirmed (polling `client.api.observations.get_many` for 60+s after a real traced call) that this Langfuse Cloud org's own query-back API still returns `model`/`usage_details`/`cost_details` as `None` regardless — a backend-side gap, not something fixable from this project's side. `answer_followup` (PandasAI/LiteLLM) is the one call site with no raw `Message` to read usage off of — covered instead by LiteLLM's own native Langfuse callback, which logs to a separate, unlinked trace rather than nesting under `answer_followup`'s span. | Re-check cloud.langfuse.com's UI directly (not just the API) once this is deployed somewhere with browser access — the data may be visible there even if the v2 observations API doesn't surface it; if not, this is an open question for Langfuse support, not this project |

## Latency and cost per question

Based on actual call counts measured while building Phase 4 (not estimated),
and current Anthropic pricing (`claude-opus-5-5`: $4/$20 per MTok input/output,
prompt-cache reads $0.20/MTok ≈ 95% off; `claude-sonnet-5-5`: $2/$10 per MTok,
cache reads $0.20/MTok ≈ 90% off). Figures below use Opus 5.5, the default
model, with no prompt caching yet configured (a Phase 6 cost lever, not built).

| Question type | LLM calls | Rough tokens/call (in/out) | Est. cost/question | Est. latency |
|---|---|---|---|---|
| Simple point value (e.g. "total revenue?") | ~4–7 (supervisor routing ×2–3, sql_agent tool loop ×1–3, narrative ×1) | ~1.5K in / 300 out | ~$0.04–0.07 | ~10–20s |
| Trend / breakdown (adds analysis_agent) | ~6–9 | ~2K in / 400 out | ~$0.06–0.10 | ~15–30s |
| Follow-up (PandasAI) | 1 PandasAI call (itself 1–2 model calls for code-gen + the answer) + 1 narrative call | ~1K in / 300 out | ~$0.02–0.04 | ~8–15s |
| Cleaning run (per table, with findings) | 1 propose_fixes call | ~1–2K in / 500 out | ~$0.02–0.04 | ~10–20s |

**This table is the rubric Phase 6's CI eval reports against** (per the
ROADMAP's Phase 5 evaluation note) — not just descriptive numbers. Suggested
thresholds for Phase 6 to enforce: **flag a regression if p50 latency exceeds
30s for any question type, or if per-question cost exceeds $0.15** (roughly
2× the high end measured above, leaving headroom for legitimately harder
questions without masking a real regression).

Costs would drop substantially with prompt caching (the system prompts and
tool schemas in `sql_agent`/`narrative_agent`/`analysis_agent` are static
across calls — a clear, not-yet-taken Phase 6 optimization) and are not
currently using `claude-sonnet-5-5` for any of the cheaper, lower-judgment
calls (e.g. `classify_lens`, `route`) where it would likely hold quality at
roughly half the cost.
