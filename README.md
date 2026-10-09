# talk-to-your-data

A Slackbot where a stakeholder asks a business question in plain English and gets an
answer, a chart, and the SQL used, posted in-thread. Questions are handled by a
LangGraph multi-agent system that queries Postgres only through an MCP server exposing
a YAML-defined semantic layer. The system has guardrails, tracing (Langfuse), and an
evaluation suite that runs in CI.

## Results

- **Eval (CI-gated):** a 40-question golden set (26 answerable with independently
  computed expected values, 14 out-of-scope). Latest runs: execution accuracy 96-100%,
  faithfulness 96-100%, refusal correctness 100%.
- **Latency:** a typical question takes ~6-8s (4 sequential LLM calls), down from 16-48s
  after profiling -- see [docs/architecture.md](docs/architecture.md).
- **Charts:** chosen in code from the shape of the result (time series -> line,
  category -> bar), or on request ("plot", "pie chart"); if a requested chart is
  impossible for the data, the answer says why.

Dataset: [Olist Brazilian e-commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).

- Build plan, phases, and acceptance criteria: see [ROADMAP.md](ROADMAP.md).
- Conventions and context for working on this repo (including with Claude Code): see
  [CLAUDE.md](CLAUDE.md).
- System design, component diagram, failure modes, and the latency/cost rubric: see
  [docs/architecture.md](docs/architecture.md).

## Quickstart

```bash
uv sync --group dev --group data
cp .env.example .env   # fill in credentials; see .env.example for what's needed when

# One-time dataset fetch (needs a fresh KAGGLE_API_TOKEN from
# https://www.kaggle.com/settings/api -- it expires after 3 hours)
uv run python scripts/fetch_data.py

docker compose --env-file .env -f docker/docker-compose.yml up -d
uv run python scripts/load_data.py
uv run python scripts/setup_db_roles.py   # creates the least-privilege olist_readonly role

uv run pytest                  # fast, no DB or LLM required
uv run pytest -m integration    # requires the steps above to have run
uv run pytest -m llm            # calls the real model; needs credentials (+ MCP server for some)

# `docker compose up -d` above also started mcp-server, cleaning-api, eda-api, and
# slackbot. The Slackbot needs SLACK_BOT_TOKEN/SLACK_APP_TOKEN in .env (a Slack app
# with Socket Mode enabled, scopes app_mentions:read/chat:write/channels:history or
# im:history/files:write) -- without those it'll fail to connect; everything else
# above works regardless. Once connected: @-mention it or DM it with a question,
# then just reply in that thread (no need to re-mention) to ask a follow-up.

# Cleaning agent API
curl -X POST localhost:8001/cleaning-runs -H 'content-type: application/json' \
  -d '{"table": "orders"}'
# -> {"run_id": "...", "status": "awaiting_approval" | "done", "proposals": [...], ...}
# If awaiting_approval, review `proposals` then:
curl -X POST localhost:8001/cleaning-runs/<run_id>/approve -H 'content-type: application/json' \
  -d '{"approved_finding_ids": ["..."]}'

# EDA supervisor agent: ask a business question in plain English
curl -X POST localhost:8002/ask -H 'content-type: application/json' \
  -d '{"question": "How did monthly revenue trend in 2017?"}'
# -> {"question": ..., "sql": ..., "result_summary": ..., "caveats": ..., ...}
# Also reachable over A2A (agent card at /.well-known/agent-card.json, JSON-RPC at /a2a)
# for external agent-to-agent callers.

# Follow-up question on an already-answered finding (PandasAI, Phase 5 -- operates
# on the stored result rows, never re-queries Postgres)
curl "localhost:8002/findings?limit=1"   # find a finding_id from a prior /ask
curl -X POST localhost:8002/findings/<finding_id>/followup -H 'content-type: application/json' \
  -d '{"question": "What is the combined total of the top 3?"}'
```

Proposing fixes and asking questions both call the LLM (`OPENAI_API_KEY`/
`OPENAI_BASE_URL` in `.env`) -- without real credentials, cleaning runs against tables
with no findings still work end-to-end (`status: done` immediately), but anything that
needs the model (a cleaning run with findings, any EDA question) will fail at that step.
