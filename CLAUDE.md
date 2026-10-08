# CLAUDE.md

**Read ROADMAP.md first, every session.** It has the phase plan, current status, and
acceptance criteria. Don't start implementing a phase until the user has reviewed and
approved that phase's plan — even if an earlier phase looks "obviously" done enough to
move on.

## What this is

A Slackbot where a stakeholder asks a business question in plain English and gets an
answer, a chart, and the SQL used, posted in-thread. A LangGraph multi-agent system
answers the question by querying Postgres only through an MCP server, which exposes
metrics defined in a semantic layer (YAML, compiled to SQL) rather than letting agents
write arbitrary joins against raw tables. Guardrails, tracing, and a CI-gated
evaluation suite are first-class, not bolted on at the end.

This is a self-directed learning project / portfolio piece, built in 6 phases (see
ROADMAP.md). The person you're working with is an experienced AI engineer — skip
"what is LangGraph" explanations, but always surface *why* a design choice was made,
especially trade-offs that affect answer quality or evaluability.

## Working agreement

- **Plan, then build.** For each phase: write the plan, show it, wait for explicit
  approval, then implement. Write tests as you go, not after.
- **Evaluation is the top priority.** Whenever a design choice could affect answer
  quality (prompt structure, retrieval strategy, metric compiler behavior, agent
  routing, etc.), say how it will be measured — don't leave it implicit. See each
  phase's "Evaluation" note in ROADMAP.md.
- **End of phase:** summarize what was built, the key design decisions and their
  trade-offs, and update ROADMAP.md (check off tasks, note deviations from plan).
- **Secrets:** never hardcode credentials. Everything comes from environment
  variables; `.env` is gitignored, `.env.example` documents the names. LLM calls go
  through the Anthropic SDK configured for Microsoft Foundry — base URL and key come
  from env, nothing provider-specific hardcoded into business logic.
- **Token/context discipline:** read only the files you need; don't re-read large data
  files (CSVs in `data/`, query result dumps) — summarize or sample instead.

## Repo layout

```
.
├── CLAUDE.md
├── ROADMAP.md
├── README.md
├── pyproject.toml              # uv-managed, Python 3.11+
├── .env.example
├── .claude/skills/              # eda-profiling, sql-conventions, findings-writeup
├── docker/
│   ├── docker-compose.yml       # postgres + mcp-server + cleaning-api + eda-api
│   └── Dockerfile               # shared image; command: overridden per service
├── docs/
│   └── architecture.md          # component diagram, failure modes, latency/cost (Phase 5)
├── scripts/
│   ├── fetch_data.py            # Kaggle -> data/raw/ (only script touching network/credentials)
│   ├── load_data.py             # data/raw/*.csv -> Postgres raw schema
│   ├── setup_db_roles.py         # idempotent: creates/grants the olist_readonly role
│   └── _shared.py                # filename <-> table-name mapping used by fetch/load
├── src/talk_to_your_data/
│   ├── db.py                     # app_engine() / readonly_engine() factories
│   ├── checkpointer.py           # shared Postgres checkpointer (sync + async) for every graph
│   ├── semantic_layer/
│   │   ├── models.py             # 5 models: joins, grain, dimensions (code, reviewed)
│   │   ├── schema.py             # pydantic schema for metrics.yaml
│   │   ├── registry.py           # cross-validates metrics.yaml against models.py
│   │   ├── metrics.yaml          # 9 metrics + 4 dimensions (declarative, edit freely)
│   │   └── compiler.py           # (metric, dims, filters, time_grain) -> SQL
│   ├── mcp_server/
│   │   └── server.py             # FastMCP: list_metrics, describe_metric, query_metric, run_sql
│   ├── agents/
│   │   ├── cleaning/             # profile -> propose -> approve -> apply -> validate (Phase 3)
│   │   │   ├── state.py          # CleaningState, FixStrategy, ProfileFinding, FixProposal
│   │   │   ├── profiling.py      # deterministic checks (TABLE_CHECKS registry, no LLM)
│   │   │   ├── fix_strategies.py # strategy -> SQL, deterministic; apply_fixes (transactional)
│   │   │   ├── llm.py            # propose_fixes: the only LLM call in this agent
│   │   │   ├── graph.py          # the LangGraph StateGraph wiring
│   │   │   └── api.py            # FastAPI: POST /cleaning-runs, GET .../{id}, POST .../approve
│   │   └── eda/                  # supervisor + sql/analysis/narrative agents (Phase 4)
│   │       ├── state.py          # AgentState, Finding, SqlResult, AnalysisResult
│   │       ├── mcp_tools.py      # MCP tool schema -> Anthropic ToolParam adapter
│   │       ├── sql_agent.py      # bounded tool-use loop against the MCP server
│   │       ├── analysis_agent.py # lens classification (LLM) + stats computation (code)
│   │       ├── narrative_agent.py # writes the Finding's prose fields
│   │       ├── supervisor.py     # the routing graph (every worker reports back to it)
│   │       ├── findings_store.py # durable findings table, independent of the checkpointer
│   │       ├── followup_agent.py # PandasAI (LiteLLM) over an already-returned finding's rows (Phase 5)
│   │       ├── ask.py            # ask_question(): protocol-agnostic entry point
│   │       ├── a2a_executor.py   # A2A AgentExecutor adapter over ask_question()
│   │       └── api.py            # FastAPI: POST /ask, GET/POST /findings..., + A2A routes
│   ├── slackbot/                 # Slack Bolt app, Socket Mode (Phase 6)
│   └── eval/                     # golden set + scorers, run from CI (Phase 6)
├── tests/                        # mirrors src/ layout
└── data/                         # gitignored; raw Olist CSVs loaded locally
```

## Conventions

- Python 3.11+, dependencies managed with `uv` (`uv sync`, `uv run ...`). Don't use
  pip/poetry/conda for this project.
- Lint/format: `ruff`. Type-check: `mypy` against `src/`.
- Tests: `pytest`, under `tests/`, mirroring the `src/` package layout. Unit tests for
  node/compiler logic in isolation, not just end-to-end graph runs.
- Three pytest markers, each a **separate** runtime dependency —
  `uv run pytest` (default) excludes both `integration` and `llm`:
  - `integration`: needs Docker Postgres running with data loaded.
  - `llm`: calls the real Anthropic/Foundry API (costs tokens); some of these also
    need the MCP server running (`uv run python -m talk_to_your_data.mcp_server.server`).
  - A test needing real credentials gets **only** `llm`, not also `integration` —
    even if it happens to touch Postgres too. Dual-marking them doesn't add
    precision, it just means a plain `-m integration` run unexpectedly sweeps in
    costly real-model calls. Found this the hard way when a parametrized
    golden-question suite got swept into an `-m integration` run and hung.
- SQL style: defined by the `sql-conventions` skill (Phase 1) — use it when writing or
  reviewing queries rather than improvising a style.
- **Q&A agents** (Phase 4's supervisor/SQL/analysis/narrative agents, the Phase 6
  Slackbot) only touch Postgres through the MCP server's read-only tools, never a
  direct SQLAlchemy/psycopg connection — this is what makes the guardrails and
  tracing in Phase 6 actually enforceable against arbitrary natural-language input.
  The **cleaning agent** (Phase 3) is the deliberate exception: it's an internal
  pipeline tool that needs write access, connects directly via `db.app_engine()`,
  and never goes near user-facing natural language. Don't extend the MCP server
  with write tools to "unify" this — that would undermine the read-only boundary
  Phase 6 depends on.
- An LLM proposes a **strategy + parameters** referencing something it was already
  given (a `finding_id`, a `metric` name), never raw SQL or an arbitrary identifier.
  Code resolves the actual table/column and renders the SQL deterministically. This
  pattern repeats across the semantic layer (Phase 2) and the cleaning agent
  (Phase 3) on purpose — it's what keeps agent-writable paths auditable without
  having to validate arbitrary LLM-generated SQL.
- **The `aie-academy-hub` Foundry deployment rejects forced `tool_choice`**
  (`{"type": "tool", ...}` / `"any"`) with a 400 — confirmed against both
  `claude-opus-5-5` and `claude-sonnet-5`, not assumed. Every tool-use call site
  (Phase 3's `propose_fixes`, Phase 4's planned supervisor/sql/analysis agents) uses
  the default `"auto"` tool_choice plus an explicit "you must call the tool" prompt
  instruction, and checks for a `tool_use` block in the response rather than relying
  on the API to guarantee one.
- **PandasAI (Phase 5) goes through `pandasai-litellm`, not a direct Anthropic
  integration** — no official `pandasai-anthropic` package exists; confirmed by
  checking PyPI, not assumed. `LiteLLM(model="anthropic/<model>", api_key=...,
  api_base=ANTHROPIC_BASE_URL)` routes correctly to the Foundry deployment.
  PandasAI is the one place in this codebase where an LLM's generated code
  actually executes — scoped deliberately to an in-memory DataFrame reconstructed
  from a finding's already-stored rows, never a live DB connection; see
  `followup_agent.py`'s docstring.
- The `findings` table's schema has grown via plain idempotent `ALTER TABLE ...
  ADD COLUMN IF NOT EXISTS` statements in `ensure_findings_table()` (Phase 5 added
  `result_columns`/`result_rows`/`parent_finding_id` this way) — there's no
  migration framework in this project, and this project's tables are few and
  small enough that this hasn't needed to change.

## Data: Olist Brazilian e-commerce

Loaded by `scripts/fetch_data.py` (Kaggle → `data/raw/`) and `scripts/load_data.py`
(CSV → Postgres). Raw, unmodified tables live in the `raw` schema; Phase 3's cleaning
agent is what's allowed to produce cleaned versions elsewhere — nothing here mutates
`raw` in place. Numbers below are from the actual loaded data, not the dataset's public
documentation — re-verify with the `eda-profiling` skill if they look stale.

**Tables** (`raw.<name>`, loaded from `olist_<name>_dataset.csv` unless noted):

| Table | Grain | Rows |
|---|---|---|
| `orders` | one row per order | 99,441 |
| `order_items` | one row per line item (an order can have several) | 112,650 |
| `order_payments` | one row per payment installment/method on an order | 103,886 |
| `order_reviews` | one row per review | 99,224 |
| `customers` | one row per order's customer record, **not** one per person | 99,441 |
| `products` | one row per product | 32,951 |
| `sellers` | one row per seller | 3,095 |
| `geolocation` | many rows per zip-code prefix (lat/lng samples, not a clean dimension) | 1,000,163 |
| `product_category_name_translation` | Portuguese → English category names | 71 |

**Key joins:** `orders.order_id` → `order_items.order_id` → `order_items.product_id` →
`products.product_id` / `order_items.seller_id` → `sellers.seller_id`;
`orders.customer_id` → `customers.customer_id`; `orders.order_id` →
`order_payments.order_id` and → `order_reviews.order_id`.

**Business-context judgment calls — decide explicitly, don't default silently:**

- `customers.customer_id` is unique per **order** (99,441 of 99,441); the actual
  repeat-purchase person is `customer_unique_id` (only 96,096 distinct values).
  "Number of customers" means `COUNT(DISTINCT customer_unique_id)`, not
  `COUNT(DISTINCT customer_id)` or `COUNT(*)`.
- `order_status` has 8 values (`delivered` ~97%, plus `shipped`, `canceled`,
  `unavailable`, `invoiced`, `processing`, `created`, `approved`). Revenue/delivery
  metrics must state which statuses they include — "all orders" and "delivered
  orders" give different answers and both are defensible depending on the question.
- 775 orders have **no** `order_items` row at all (mostly non-delivered statuses) —
  an inner join from `orders` to `order_items` silently drops them.
- 9,803 orders have more than one line item — summing `order_items.price` per order
  before joining to anything order-grained avoids an accidental fan-out.
- `order_reviews.review_id` is **not unique** (99,224 rows, 98,410 distinct ids) and
  some orders have more than one review — don't treat `review_id` as a primary key.
- `order_payments` is one-to-many per order (up to 29 rows for one order —
  installments/split payment); includes a `not_defined` `payment_type`.
- `order_delivered_customer_date` is null for ~3% of orders (undelivered/canceled) —
  delivery-time metrics need to either filter these or handle the null explicitly.
- `product_category_name` is null for 610 products and is in Portuguese otherwise —
  join `product_category_name_translation` for English, and decide how nulls should
  be labeled rather than silently excluding them from category breakdowns.
- Date/timestamp columns are loaded as real Postgres `timestamp` columns (parsed
  explicitly in `load_data.py`, since pandas' default `to_sql` would otherwise land
  them as `text`) — no casting needed for date arithmetic.

## Stack

Python 3.11+ · uv · Postgres (Docker Compose) · FastMCP · LangGraph (+ Postgres
checkpointer) · PandasAI · Slack Bolt (Socket Mode) · Langfuse · pytest · Anthropic SDK
via Microsoft Foundry.
