# talk-to-your-data

A Slackbot where a stakeholder asks a business question in plain English and gets an
answer, a chart, and the SQL used, posted in-thread. Questions are handled by a
LangGraph multi-agent system that queries Postgres only through an MCP server exposing
a YAML-defined semantic layer. The system has guardrails, tracing (Langfuse), and an
evaluation suite that runs in CI.

Dataset: [Olist Brazilian e-commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).

- Build plan, phases, and acceptance criteria: see [ROADMAP.md](ROADMAP.md).
- Conventions and context for working on this repo (including with Claude Code): see
  [CLAUDE.md](CLAUDE.md).

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

uv run pytest                  # fast, no DB required
uv run pytest -m integration    # requires the steps above to have run

# MCP server (semantic layer + read-only SQL tool), served over HTTP
uv run python -m talk_to_your_data.mcp_server.server

# Cleaning agent API (`docker compose up -d` above already started this too)
curl -X POST localhost:8001/cleaning-runs -H 'content-type: application/json' \
  -d '{"table": "orders"}'
# -> {"run_id": "...", "status": "awaiting_approval" | "done", "proposals": [...], ...}
# If awaiting_approval, review `proposals` then:
curl -X POST localhost:8001/cleaning-runs/<run_id>/approve -H 'content-type: application/json' \
  -d '{"approved_finding_ids": ["..."]}'
```

Proposing fixes calls the LLM (`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` in `.env`) -- without
real credentials, runs against tables with no findings still work end-to-end (they finish
immediately with `status: done`), but a run with findings will fail at the propose step.
