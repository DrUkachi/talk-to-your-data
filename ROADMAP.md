# ROADMAP

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

Workflow for every phase: propose a plan → get explicit approval → implement with
tests → write the phase summary (what was built, key design decisions + trade-offs)
→ check off tasks below and note any deviations from plan.

**Current status: Phase 6b (Langfuse tracing) complete. Phase 6c (Slackbot) not started.**

---

## Phase 1 — Foundation ✅

- [x] Repo layout matches `CLAUDE.md` (src layout, tests, docker, scripts, docs, skills)
  - **Acceptance:** `uv sync` installs cleanly; `uv run pytest` collects (even with 0
    tests); every directory CLAUDE.md describes exists.
- [x] `docker/docker-compose.yml`: Postgres service, pinned version, named volume,
      healthcheck, port from `.env`
  - **Acceptance:** `docker compose up -d` succeeds; `pg_isready` passes; data
    survives a container restart (volume persists).
- [x] `scripts/fetch_data.py` + `scripts/load_data.py`: Olist CSVs → Postgres raw tables
  - **Acceptance:** idempotent re-run doesn't duplicate rows; logged row counts match
    source CSVs; runnable via `uv run python scripts/load_data.py`.
- [x] `CLAUDE.md` schema + business-context section populated from the loaded data
      (table list, grain, key joins, known quirks — e.g. Olist's one-review-can-cover-
      multiple-orders edge cases, multi-item orders, null review comments, delivery
      date outliers)
  - **Acceptance:** a fresh session reading only CLAUDE.md can write a correct join
    between orders/order_items/customers/reviews without querying the DB first.
- [x] 2–3 Claude Code skills in `.claude/skills/`: EDA/profiling, SQL conventions,
      findings write-up
  - **Acceptance:** each has an unambiguous trigger description; invoking each on a
    sample question produces the expected artifact (profiling report / a query that
    follows the stated conventions / a findings doc in the stated format).

**Evaluation note:** no agent exists yet, so no answer-quality eval. The hook here is
*data-quality* checks (row counts, null rates, referential integrity between
orders/items/customers/reviews) — later phases' evals assume these hold, so write them
as assertions in `load_data.py` or a small test, not just eyeballed.

### Phase 1 summary

**Built:** Postgres via Docker Compose (`postgres:16-alpine`, named volume, healthcheck);
a two-script data pipeline (`fetch_data.py` for Kaggle auth/download, `load_data.py` for
CSV→Postgres, kept separate so the loader has zero network/credential dependency);
9 CSVs landed in a dedicated `raw` schema; the full real schema (grain, joins, and 7
specific data-quality judgment calls, e.g. `customer_id` vs `customer_unique_id`,
non-unique `review_id`, non-`delivered` order statuses) written into `CLAUDE.md` from
actual queried values, not assumed from the dataset's reputation; 3 skills
(`eda-profiling`, `sql-conventions`, `findings-writeup`); 16 tests (3 unit, 13
integration, integration gated behind `-m integration` so default `pytest` stays fast
and DB-free).

**Key decisions and trade-offs:**
- *Python 3.11, not 3.12/3.14*: `uv`'s default interpreter resolution picked 3.14,
  which broke `pandasai`'s transitive `scipy`/`pyarrow` pins (no prebuilt wheels, and
  source builds failed on missing Arrow/C++ toolchain and a `meson-python` version
  gate). Pinned via `.python-version` (committed, not gitignored) rather than loosening
  dependency versions, since `pandasai` itself is out of scope until Phase 5 — better to
  fix the interpreter than start relaxing pins for a dependency we don't even use yet.
- *Truncate-and-reload over upsert*: this is a static snapshot dataset; full refresh is
  simpler and equally correct at this size. Would need revisiting if the dataset ever
  became incremental.
- *Dates parsed explicitly at load time*: pandas' default `to_sql` landed every
  timestamp column as Postgres `text`. Left as-is, this would have forced a cast in
  every date-based query from Phase 2 onward. Fixed by passing an explicit
  `parse_dates` list per table in `load_data.py`.
- *Kaggle auth via `kagglehub` + `KAGGLE_API_TOKEN`*: confirmed by reading
  `kagglehub`'s actual source (`kagglehub.config` → `kagglesdk.kaggle_env`) rather than
  assuming — it reads `KAGGLE_API_TOKEN` directly, consistent with Kaggle's newer
  access-token auth (distinct from the legacy username+key pair). Access tokens expire
  after 3 hours, so this is a one-time interactive step, not a long-lived credential.
- *Data-quality checks log, they don't fail the run*: orphan-row and null-rate checks
  in `load_data.py` are `logger.warning`, not assertions — Olist's real quirks (e.g. 775
  orders with no line items) are expected, and hard-failing on them would conflate
  "the loader is broken" with "the source data has the usual rough edges," which is
  exactly what Phase 3's cleaning agent exists to address.

---

## Phase 2 — Semantic layer + MCP ✅

- [x] Metrics/dimensions YAML schema (metric: name, description, sql expression/agg,
      grain, allowed dimensions, allowed filters), validated with pydantic
  - **Acceptance:** 8–10 metrics defined covering orders, revenue, review scores,
    delivery time; schema rejects a malformed metric file in a test.
- [x] Compiler: `(metric, dimensions, filters, time grain) -> SQL`
  - **Acceptance:** unit tests compile each metric and execute the SQL against the
    loaded DB, matching a hand-written reference query for at least 5 metrics.
- [x] FastMCP server: `list_metrics`, `describe_metric`, `query_metric`, read-only
      raw-SQL tool
  - **Acceptance:** server starts; each tool is callable via an MCP test client; the
    raw-SQL tool rejects non-`SELECT` statements.
- [x] MCP server connects to Postgres as a least-privilege role (full guardrail suite
      lands in Phase 6, but least privilege starts here, not retrofitted later)

**Evaluation note:** this is where *faithfulness* starts to matter — compiled SQL must
be independently checkable against the metric's YAML definition. Plan: snapshot tests
pinning `(metric_name, params) -> exact SQL`, so any compiler change is reviewed as an
explicit diff, not silently re-approved.

### Phase 2 summary

**Built:** 5 models (`order_items`, `orders`, `order_reviews`, `order_payments`,
`order_delivery`) in `semantic_layer/models.py` encoding the real joins/grain from
Phase 1's CLAUDE.md notes (e.g. `customer_unique_id` not `customer_id`, category
nulls COALESCEd to the Portuguese name rather than dropped); 9 metrics + 4 dimensions
in `metrics.yaml`, pydantic-validated (`schema.py`) and cross-validated against the
model registry (`registry.py`); a SQLAlchemy-based compiler
(`(metric, dimensions, filters, time_grain) -> (Select, sql_text)`); the
`olist_readonly` Postgres role (`scripts/setup_db_roles.py`, idempotent, survives
`load_data.py`'s table-recreation via `ALTER DEFAULT PRIVILEGES`); a FastMCP server
(`list_metrics`, `describe_metric`, `query_metric`, `run_sql`) served over HTTP.
47 tests total (20 unit / 27 integration): 11 SQL snapshot cases across all 9 metrics,
5 metrics cross-checked against hand-written reference queries, 6 schema/registry
validation-failure cases, 9 MCP-tool tests via FastMCP's in-process client. Ruff and
mypy clean.

**Verified, not just unit-tested:** ran all 9 metrics against the real loaded DB (sane
numbers: 99,441 orders, 96,096 customers, ~13.2M BRL revenue, 8.1% late-delivery rate);
confirmed the readonly role can read but not write, and that its grant survives a real
`load_data.py` reload; started the MCP server as an actual HTTP service (not just the
in-process test client) and called `query_metric` over a real socket.

**Key decisions and trade-offs:**
- *Models in code, metrics/dimensions in YAML*: getting a join right (and not fanning
  out a `SUM`) is business logic, not config — `order_items` alone would double-count
  revenue on the 9,803 multi-item orders without the grain-safe join in `models.py`.
  Each metric operates on exactly one model, so there's no join-graph resolution at
  query time and no way for the compiler to introduce a fan-out bug. Trade-off: adding
  a metric that needs a genuinely new join means touching reviewed Python, not just
  YAML — the right call for 5 fixed models, the wrong call at dbt-scale.
  Dimension *metadata* (name, type, description) lives in YAML per the original
  spec; the column mapping that depends on join aliases stays in `models.py`.
- *SQLAlchemy `Select` built from `literal_column`/`text`, not an f-string*: dimension
  and measure SQL fragments are code-authored and trusted; filter *values* are always
  bound parameters (`bindparam(..., expanding=True)`). Metric/dimension names are dict
  lookups that raise `CompilerError`/`KeyError` on anything unrecognized. This is what
  makes the compiler injection-safe even before Phase 6's guardrails exist.
- *`CREATE ROLE ... PASSWORD` can't bind a parameter* (confirmed by hitting a real
  Postgres syntax error) — Postgres's DDL grammar doesn't accept a prepared-statement
  placeholder there. Fixed with `psycopg.sql.Literal` for correct quoting instead of
  an f-string, keeping identifiers (`psycopg.sql.Identifier`) and the literal password
  value both safely escaped without needing bind parameters.
- *FastMCP over HTTP, not stdio*: confirmed by actually starting the server and
  calling it over a real socket, not just the in-process test client. stdio only works
  when a client spawns the server as its own subprocess, which doesn't fit Phase 3+'s
  separate long-running processes (FastAPI service, Slack app).
- *`run_sql` guardrails are intentionally shallow* (single-statement check, `SELECT`/
  `WITH` prefix check, wrapped in a `LIMIT`): the full guardrail suite (parsed
  allow-listing, PII masking, policy-level row limits) is explicit Phase 6 scope.
  Building it now against an unstated spec would mean redoing it later.

---

## Phase 3 — LangGraph data-cleaning agent ✅

- [x] Graph: profile → propose fixes → human approval (interrupt) → apply → validate →
      loop on failure, using a Postgres checkpointer
  - **Acceptance:** graph compiles with the checkpointer; an approval interrupt
    actually pauses execution and resumes correctly after approval; a deliberately
    broken dataset triggers at least one fix→apply→validate loop.
- [x] FastAPI service wrapping the graph, Dockerized
  - **Acceptance:** `docker compose up` brings up API + Postgres; POST starts a run and
    returns a run id; GET returns current state / pending approval for that run.
- [x] Unit tests per node (profile/propose/apply/validate), not just end-to-end

**Evaluation note:** quality here means "fixed real problems without touching values it
shouldn't have." Plan: a small set of synthetic data-quality injections (nulls, dupes,
bad types) with known-correct fixes, re-run as a regression set whenever the
fix-proposal prompt changes.

### Phase 3 summary

**Built:** a 5-node graph (`profile → propose_fixes → [interrupt] → apply → validate`,
looping to `profile` on failed validation, capped at `max_attempts=3`) over a
`CleaningState`; a `TABLE_CHECKS` registry of 4 deterministic check types (logical
ordering, out-of-range, exact duplicates, categorical noise) covering `orders`,
`products`, `order_reviews`, `order_items`; 5 fix strategies
(`trim_whitespace`/`normalize_case`/`drop_exact_duplicates`/`null_out_impossible_value`/
`clip_outlier`) rendered deterministically from an LLM-chosen strategy + params,
applied transactionally (all-or-nothing); a `clean` schema materialized once from
`raw` per table (second runs build on prior fixes, don't reset); a `langgraph` schema
holding `PostgresSaver`'s checkpoint tables; a FastAPI service
(`POST /cleaning-runs`, `GET /cleaning-runs/{id}`, `POST .../approve`) built and run
via `docker compose`. 75 tests total (32 unit / 43 integration) — unit tests include
pure SQL-render assertions for all 5 strategies and the tool-use parsing with a
stubbed Anthropic client (no real credentials needed); integration tests include a
synthetic dirty fixture exercising all 4 check types end-to-end through interrupt →
partial-approval → loop → full-approval → done.

**Verified, not just unit-tested:** profiled all 4 real `raw.*` tables and confirmed
they're genuinely clean by these checks (zero findings) — an honest outcome given the
scoping decision below, not a gap; killed and recreated the graph object mid-run
(simulating a process restart) and confirmed it resumed correctly from Postgres, not
from in-memory state; built and ran the actual Docker image via
`docker compose up -d --build`, hit it with real `curl` requests over the network, and
confirmed the least-privilege-adjacent `clean`/`langgraph` schemas got created
correctly from inside the container (which talks to Postgres via the compose network
hostname, not `localhost`).

**Key decisions and trade-offs:**
- *Scoping "defect" narrower than "quirk"*: most of Phase 1/2's documented Olist
  quirks (non-unique `review_id`, null delivery dates, 8 order statuses) are
  legitimate business facts the semantic layer already handles correctly — "fixing"
  them would be wrong. This agent targets a different, narrower thing: illogical
  orderings, out-of-range values, exact duplicates, categorical noise. Confirmed by
  actually profiling real data: zero findings across all 4 tables. The synthetic
  dirty fixture (not real data) is what makes the agent's logic testable at all.
- *LLM picks strategy + params referencing a `finding_id`, never raw SQL or a
  column name*: `apply` resolves the actual table/column from the matching
  `ProfileFinding`, not from LLM free text, so there's no path for the LLM to point
  a write at an arbitrary identifier. Same throughline as Phase 2's compiler.
- *Cleaning agent uses `app_engine()` directly, not the MCP server*: the "agents only
  touch Postgres through MCP" rule (CLAUDE.md) is about the Q&A path's guardrails
  against natural-language input. This agent needs write access and never processes
  user-facing natural language — extending MCP with write tools to unify the two
  would weaken the boundary Phase 6 depends on. Documented explicitly in CLAUDE.md
  so it doesn't look like a one-off inconsistency later.
- *A fresh graph + checkpointer connection per request, not a long-lived one*:
  proved by the restart test. This also meant discovering mid-build that
  `PostgresSaver` has no schema parameter — fixed via `?options=-c search_path=...`
  on the connection string, confirmed with a throwaway script before wiring it in.
- *`model_dump(mode="json")`, not the default*: the default pydantic dump kept
  `FixStrategy` as a live Python enum object inside state, which LangGraph's
  checkpointer warned about serializing via msgpack. Caught by actually running an
  end-to-end flow and reading the warning, not by inspection.
- *Docker image excludes dev tools*: `uv sync` installs the `dev` dependency group by
  default (confirmed by checking `uv sync --help`, not assumed) — fixed with
  `--no-default-groups` in the Dockerfile so pytest/ruff/mypy don't ship in the
  runtime image.

**Post-completion fix (once real Foundry credentials were configured):** forced
`tool_choice` (`{"type": "tool", ...}`) returns a 400 on the `aie-academy-hub`
deployment for both `claude-opus-5-5` and `claude-sonnet-5` ("not supported for this
model") — this had never been exercised against the real API before, since all
Phase 3 tests used a stubbed client. Fixed in `llm.py`: dropped `tool_choice`
entirely (defaults to `"auto"`), added an explicit "you must call the tool" prompt
instruction, and `propose_fixes` now raises a clear error if no `tool_use` block
comes back instead of silently returning nothing. Re-verified the entire interrupt
→ approve → apply → validate flow against the real model, through the actual Docker
container (not just in-process): injected a genuine ordering violation into
`clean.orders`, confirmed the real model proposed a well-reasoned
`null_out_impossible_value` fix, approved it via `POST .../approve`, and confirmed
validation passed. Added `tests/test_llm_propose_fixes_live.py` (marked `llm`,
skipped by default, `ANTHROPIC_API_KEY`-gated) as the permanent version of this
check — assertions are intentionally soft on *which* findings get a proposal (the
model skipping a fix it judges unsafe, e.g. a negative `amount` that might be a
legitimate refund, is a reasonable call, not a test failure).

---

## Phase 4 — Stateful EDA + multi-agent ✅

- [x] Persistent state + findings store (a "finding" = question, SQL, result summary,
      chart reference, confidence/caveats)
- [x] Supervisor graph routing to SQL/metrics, analysis, and narrative agents
- [x] One agent exposed over the A2A protocol with an agent card
  - **Acceptance:** an external A2A client can discover the agent card and get a valid
    response to a routed request.

**Evaluation note:** first phase with genuinely multi-step answers — start a *miniature*
golden-question set here (5–10 questions) even though the full 40-question suite is
Phase 6, so supervisor routing has a regression check from day one instead of being
eyeballed until the capstone.

### Phase 4 summary

**Built:** a supervisor graph (`supervisor → {sql_agent, analysis_agent,
narrative_agent} → supervisor → ... → END`) where every worker reports back to the
supervisor for the next routing decision; `sql_agent` is a bounded tool-use loop
against the Phase 2 MCP server (list_metrics/describe_metric/query_metric/run_sql,
reached over HTTP, consistent with why that server picked HTTP transport in the first
place); `analysis_agent` classifies a result into one of two lenses
(`time_series_trend`, `category_breakdown`) or `none`, then computes the actual stats
deterministically in code — the LLM only supplies judgment (which lens, and an
optional date range), same split as Phase 2's compiler and Phase 3's fix strategies;
`narrative_agent` writes the `Finding`'s prose fields, with `question`/`sql` filled
from the actual inputs in code, never regenerated by the model. A durable `findings`
table persists every answered question independent of the LangGraph checkpointer
(which persists conversational/execution state, factored out of Phase 3's `graph.py`
into a shared `checkpointer.py` — sync and async variants, since `fastmcp.Client` is
async-only and that forced the whole EDA graph to be async). `ask_question()` is a
single protocol-agnostic entry point; a FastAPI `/ask` and an A2A executor are both
thin adapters around it. 25 new tests (13 unit / 6 integration / 6 llm), bringing the
project total to 101 (45 unit / 49 integration / 7 llm) — pure analysis-lens math and
the MCP tool-schema adapter need no LLM or DB at all; the A2A executor is tested with
`ask_question` stubbed, not the real `a2a.client` stack, for reasons below.

**Verified, not just unit-tested:** the full supervisor loop against the real model
for 6 golden questions (correct routing judgment on all 6 — analysis skipped for
simple point-value questions, used for trend/breakdown questions); a real
`a2a.client.Client` discovering the agent card and completing a full JSON-RPC
round-trip against a live server; the entire 4-service stack
(`postgres`/`mcp-server`/`cleaning-api`/`eda-api`) built and run via
`docker compose up -d --build`, hit with real `curl`/A2A-client requests, confirming
`eda-api` reaches `mcp-server` reaches `postgres` correctly over the compose network
(service hostnames, not `localhost`).

**Key decisions, trade-offs, and bugs actually caught by running things:**
- *No real LLM credentials were available when Phase 3 was built and tested*; they
  are now. Running Phase 3's `propose_fixes` for real exposed that forced
  `tool_choice` 400s on this Foundry deployment — fixed before starting Phase 4 (see
  Phase 3 summary's post-completion note), and every Phase 4 tool-use call site
  (`sql_agent`, `classify_lens`, `write_finding`, `route`) was written already knowing
  this constraint, rather than discovering it three more times.
- *The semantic layer has no date-range filter* (Phase 2's `query_metric` only
  supports categorical IN-list filters) — discovered empirically when a "trend in
  2017" question came back with every period the data has, not just 2017. Rather
  than extend Phase 2's compiler, `classify_lens` also extracts an optional
  start/end range when the question names one, and `compute_trend_stats` filters to
  it in pandas — the same judgment/computation split applied one level downstream of
  where the gap actually was.
- *A real bug caught by the test suite, not manual testing*: `supervisor_node`
  unconditionally set `status="routing"` on every pass, including its final
  housekeeping pass after `narrative_agent` completes — silently overwriting
  `"done"` with `"routing"`. Fixed by only touching `status` when there's still work
  ahead; added a regression test for it specifically.
- *A real bug caught by an integration test*: `save_finding` returns a string UUID,
  but Postgres/psycopg hands `list_findings` back a `uuid.UUID` object for the same
  column, so direct comparison between the two silently never matched. Fixed by
  normalizing `id` to a string in `list_findings`.
- *`a2a-sdk` 1.2.2's types are protobuf-generated, not pydantic* — discovered by
  checking the class MRO directly after `model_fields`/`inspect.signature` both
  failed on `AgentCard`/`Message`, rather than guessing at a pydantic-style API that
  doesn't apply here.
- *`AgentInterface.url` must be an absolute, externally-reachable URL* — a relative
  `"/a2a"` value made a real `a2a.client.Client` fail with "missing http(s)
  protocol." Fixed with an `EDA_API_BASE_URL` env var the agent card uses verbatim;
  documented in both `api.py` and `.env.example` so it doesn't look like an
  arbitrary inconsistency later.
- *The real `a2a.client.Client` stack has its own background-dispatcher lifecycle*
  that doesn't play cleanly with `httpx.ASGITransport` in a test harness (a
  "Dispatcher task is not running" warning appeared under that setup). Rather than
  fight a third-party library's internals in automated tests, the real client was
  verified by hand against actual running servers (twice — once bare, once through
  Docker); the automated test suite instead exercises `EdaAgentExecutor` directly
  against duck-typed stand-ins for `RequestContext`/`EventQueue`, which tests our
  code's logic without needing the SDK's full construction machinery.
- *`llm`-marked tests must not also carry `integration`*: dual-marking them (as
  Phase 3's single live test happened to, harmlessly, since it was cheap) means a
  plain `-m integration` run sweeps them in too. This went unnoticed until Phase 4's
  parametrized 6-question golden set — expensive and dependent on the MCP server
  being up — got swept into an `-m integration` run and hung for two minutes before
  being caught. Fixed in both phases' test files; documented as a repo-wide
  convention in CLAUDE.md so it doesn't recur a third time.

---

## Phase 5 — System design ✅

- [x] `docs/architecture.md`: Mermaid component diagram, failure-modes table,
      latency and cost estimate per question
- [x] PandasAI added as a tool for *follow-up* questions on already-returned query
      results (not for the initial query — the semantic layer / MCP path stays the only
      way the first query hits Postgres)

**Evaluation note:** cost and latency become explicit eval dimensions here, not just
accuracy. This phase should produce the *rubric* (scoring functions and thresholds),
which Phase 6's CI eval then reports against — don't leave the rubric implicit until
the capstone.

### Phase 5 summary

**Built:** `docs/architecture.md` with a Mermaid component diagram covering the full
6-phase target (built components solid, Phase 6's Slackbot/Langfuse/guardrails/CI-eval
marked planned/dashed — actually rendered and visually checked with `mermaid-cli`, not
just assumed to parse); a failure-modes table drawn from what the build actually hit
or deliberately bounded, not hypothetical; a latency/cost table using call counts
measured during Phase 4's real testing and current Anthropic pricing, with explicit
thresholds (flag a regression above 30s p50 latency or $0.15/question) — the rubric
Phase 6's CI eval reports against. A `followup_agent.py` using PandasAI via
`pandasai-litellm` (no official Anthropic integration exists) against an in-memory
DataFrame reconstructed from a finding's *already-stored* rows — extended the
`findings` table with `result_columns`/`result_rows` (JSONB) and `parent_finding_id`
so follow-ups never re-query Postgres and never see data the user wasn't already
shown. New `GET /findings`, `GET /findings/{id}`, `POST /findings/{id}/followup`
endpoints. 14 new tests, bringing the project total to 115 (55 unit / 52 integration /
8 llm).

**Verified, not just unit-tested:** `pandasai-litellm` against the real Foundry
endpoint before writing any production code around it (no `pandasai-anthropic`
package exists — confirmed on PyPI); all four PandasAI response shapes
(string/number/dataframe/chart) against the real model; the Mermaid diagram actually
rendered via `mermaid-cli` (installing missing system libs and a puppeteer
`--no-sandbox` config along the way) and visually inspected, not just assumed
syntactically valid; the full follow-up flow through the actual Docker `eda-api`
container, computing the correct combined total for the top-3-category follow-up
(matching a value independently computed by hand).

**Key decisions, trade-offs, and bugs actually caught by running things:**
- *No official `pandasai-anthropic` package* — `pandasai-litellm` plus LiteLLM's
  `anthropic/<model>` provider (which accepts a custom `api_base`) is the real path,
  confirmed against `aie-academy-hub` before committing to the design.
- *Follow-ups reconstruct the DataFrame from stored rows, never re-query* — a
  follow-up re-running the original SQL could see different data than what the user
  actually looked at (the underlying tables can change between questions), which
  would be confusing for "a follow-up on *this* answer." Costs a JSONB column; buys
  the guarantee that a follow-up's data is always exactly what was already shown.
- *`last_code_executed` becomes the follow-up `Finding`'s `sql` field* — verified
  PandasAI v3 runs pandas-over-DuckDB internally and exposes the generated SQL/code
  directly in its response, so there's genuine transparency to show, not just "trust
  PandasAI." Same "show the exact thing that produced the number" principle as
  every other `sql` field in this project, just one level removed.
- *`Finding.chart_ref` gets populated for the first time this phase* — a "chart"
  response's value is a **local filesystem path** (`exports/charts/...`). Flagged
  explicitly in the failure-modes table rather than treated as done: Phase 6 needs
  to get it somewhere a Slack client can actually fetch it.
- *I repeated the exact `llm`+`integration` dual-marking mistake* that Phase 4's
  summary documented as a convention to avoid, in the new `test_followup_agent_live.py`
  — caught by the same symptom (an `-m integration` run took noticeably longer than
  expected) before it became a repeated hang. Fixed; the convention evidently needs
  more than one documented instance to stick, so this note is now the second.
- *The idempotent-`ALTER TABLE`-as-migration pattern, used since Phase 1/3,
  continues to hold* at this table count/size — explicitly not introducing a
  migration framework for a 3-column addition to one table.

---

## Phase 6 — Capstone

Split into 4 sub-phases, each with its own plan → approve → build cycle like every
phase so far — Phase 6 is four largely independent pieces, not one. Built in this
order because 6d's refusal-correctness eval dimension needs 6a's refusal mechanism to
exist first.

**External setup required before each sub-phase can run for real** (not something I
can do without you): a Slack app (Socket Mode enabled, bot token, app token, signing
secret) for 6c; a Langfuse Cloud account (public/secret keys) for 6b; a GitHub repo +
Actions secrets (`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`/`ANTHROPIC_MODEL`, plus
long-lived legacy `KAGGLE_USERNAME`/`KAGGLE_KEY` — not the 3-hour `KAGGLE_API_TOKEN` —
for loading data in CI) for 6d.

### Phase 6a — Guardrails ✅

- [x] Read-only DB role enforced at the connection level (already true since Phase 2 —
      this sub-phase is about the layers *above* that connection)
- [x] SQL validation: real parsing + allow-listing via `sqlglot` (already a transitive
      dependency), not string matching — statement type, referenced tables/schemas,
      no system catalog access
- [x] Row limits enforced as policy (a hard cap), not just a default parameter value
- [x] PII/quasi-identifier masking — concretely, for *this* dataset: `geolocation`
      lat/lng (round to ~1 decimal) and zip-code-prefix columns (truncate), since
      Olist has no names/emails to begin with
- [x] Out-of-scope question refusal — a scope-check step before `sql_agent` ever runs,
      so an unanswerable question never reaches the data layer at all

#### Phase 6a summary

**Built:** `guardrails/sql_guard.py` (sqlglot-based parsing: rejects anything that
isn't exactly one `SELECT`/`WITH`/`UNION` statement, walks the **entire AST** for
write/DDL nodes rather than just checking the top-level statement type, enforces a
schema allow-list with CTE aliases correctly excluded from the qualification check,
rejects disallowed functions, clamps row limits to a policy ceiling, and masks
geolocation/zip columns by output name) wired into both `run_sql` and `query_metric`;
`guardrails/scope_guard.py` (an LLM classifier gating `ask_question()` *before* the
supervisor graph starts, so a refusal never touches `AgentState` at all — transparent
to every adapter — REST, A2A, future Slack — without any of them needing to know a
refusal path exists). 48 new tests, bringing the project total to 163 (88 unit /
56 integration / 19 llm), covering real attack scenarios (a
data-modifying CTE whose outer node is a harmless `Select`, comment-obscured
statement smuggling, cross-schema access, case evasion) **and** positive controls
(CTEs, joins, window functions, `UNION` — which parses to a different AST node type
than `Select`/`With` and would have been falsely rejected by the original, narrower
plan).

**Verified, not just unit-tested:** every rejection/acceptance case parsed for real
with `sqlglot` before being written into the plan (not assumed) — this is how the
data-modifying-CTE gap and the `UNION`/CTE-alias false-positive risks were found,
*before* writing the guardrail, not after a test failed; the full guardrail suite run
against the real Dockerized `mcp-server` (a data-modifying CTE rejected, geolocation
columns actually rounded in the response); the masking bypass not just left
untested but *asserted to exist* (`test_KNOWN_LIMITATION_...`); the refusal path
proven to short-circuit before ever needing the MCP server running at all, and 10
real-model scenarios (4 in-scope, 4 out-of-scope, 2 adversarial) via
`test_scope_guard_live.py`.

**Key decisions, trade-offs, and gaps found while planning (before they became
bugs):**
- *AST-walk for write nodes, not a statement-type check* — found by actually parsing
  `WITH x AS (DELETE FROM raw.orders RETURNING *) SELECT count(*) FROM x` and seeing
  its top-level type is `Select`. A plan built only from the SQL-injection-cheatsheet
  style of scenario (plain `DELETE`, stacked statements) would have missed this
  entirely.
- *CTE aliases excluded from schema-qualification checks* — found by parsing a
  completely benign `WITH recent AS (...) SELECT * FROM recent` and seeing the CTE
  reference show up in `find_all(exp.Table)` with no schema, which the naive rule
  would have falsely rejected. Positive-control testing caught what negative-only
  testing couldn't.
- *Masking is best-effort and documented as bypassable*, not built as a false sense
  of completeness — a real fix (column-provenance tracking, or masked Postgres
  views) is a genuine scope increase, decided against for this project explicitly
  rather than silently deferred.
- *Refusal gates `ask_question()`, not the supervisor graph* — keeps the graph's job
  purely "answer an in-scope question," keeps the guardrail independently testable
  (proven by a test that succeeds with the MCP server deliberately not running), and
  means every current and future adapter (REST, A2A, Slack) gets refusal for free.
- *`check_scope` raises on a malformed model response, matching every other
  tool-use call site in this project* (`propose_fixes`, `classify_lens`,
  `write_finding`, `_route`) rather than failing open or closed silently — a
  deliberate reversal from an earlier draft of this function that considered
  failing open, for consistency with the rest of the codebase's error-handling
  philosophy.

---

### Phase 6b — Langfuse tracing ✅

- [x] Every LLM call site wrapped for tracing (`propose_fixes`, `run_sql_agent`,
      `classify_lens`/`analyze`, `write_finding`, `_route`, `check_scope`,
      `answer_followup`) — each is already a narrow, named function specifically so
      this is mechanical
- [x] Real model/usage/cost attached to each generation span, not just a named span
- [x] `run_sql_agent`'s per-turn tool-use loop gets its own nested generation span
      per turn, plus a `tool`-typed span per MCP tool call

#### Phase 6b summary

**Built:** `tracing.py`'s `record_generation()` — reads `model`/`usage.input_tokens`/
`usage.output_tokens` off a real `anthropic.types.Message` and forwards them (plus a
computed cost, from a small hardcoded USD-per-token table, since Foundry's model
names aren't guaranteed to resolve against Langfuse's own price registry) onto the
current span via `update_current_generation()`. Every direct Anthropic call site is
decorated with `@observe(as_type="generation")` and calls it right after
`client.messages.create(...)`; `run_sql_agent`'s bounded tool-use loop additionally
opens one nested `start_as_current_observation(as_type="generation")` per turn and
one `as_type="tool"` span per MCP tool call, so a single question's full LLM+tool
trace is visible, not just one top-level span. `answer_followup` (PandasAI) is the
one call site with no raw `Message` to read usage off — covered instead by
LiteLLM's own native Langfuse callback (`litellm.success_callback = ["langfuse"]`),
confirmed to be a recognized callback string in the installed `litellm` version by
reading its source, not assumed, and smoke-tested against the real Foundry endpoint
before relying on it. `pyproject.toml`'s `langfuse` constraint bumped `>=2.50` →
`>=4.0` (stale from before Phase 6 was planned; the v4 OTel-based SDK is what's
actually used throughout). 4 new tests, bringing the project total to 167
(91 unit / 56 integration / 20 llm): 3 unit tests pin `record_generation`'s cost
math exactly (a priced model, a differently-priced model, an unpriced model falling
back to `cost_details=None`) against a stub response; 1 `llm`-marked test feeds it a
*real* Anthropic response (not the stub) to catch any shape mismatch the stub
wouldn't.

**Verified, not just unit-tested:** tested `opentelemetry-instrumentation-anthropic`
(Langfuse's own recommended auto-instrumentation path) for real against the Foundry
endpoint before deciding against it — it produces correctly-typed, correctly-nested
spans, but left model/usage/cost/input/output all unpopulated in default config;
confirmed the explicit approach's spans carry the right underlying OTel attributes
(`langfuse.observation.model.name`, `langfuse.observation.usage_details`) by
inspecting a raw span locally; then separately polled this project's actual Langfuse
Cloud org (`client.api.observations.get_many`, the v2 endpoint Langfuse's own
deprecation notice says is the live one) for 60+ seconds after a real traced call
and found `model`/`usage_details`/`cost_details` still `None` on query-back — a
real, reproducible backend-side gap (documented in `docs/architecture.md`'s
failure-modes table), not a timing fluke and not something fixable from this
project's side; ran the full non-`llm` suite with real Langfuse credentials loaded
to confirm `update_current_generation()` calls don't block on network I/O in tests
that use a stubbed Anthropic client (10.5s either way — no regression); re-ran all 6
of Phase 4's golden questions against the real model with the new tracing wired
through `sql_agent`/`analysis_agent`/`narrative_agent`/`supervisor` simultaneously,
confirming the extra spans don't change routing or answers.

**Key decisions, trade-offs, and a bug caught before it shipped:**
- *A regression I introduced and fixed in the same session*: `record_generation`
  unconditionally reads `response.usage.input_tokens` — broke 5 existing unit tests
  in `test_llm_propose_fixes.py`/`test_scope_guard.py` whose stub `_StubResponse`
  only implemented `.content`, predating tracing. Fixed by giving the stubs a
  `.usage`/`.model` that actually mirror the real `anthropic.types.Message` shape
  (the more correct fix, since real responses always have these — not by making
  `record_generation` defensive against a case that can't happen in production).
- *Explicit instrumentation, not the auto-instrumentor* — this project has a small,
  fixed number of Anthropic call sites, all already behind named wrapper functions;
  explicit is both necessary (auto-instrumentation didn't populate the data) and
  sufficient (no other call sites exist to miss), so running both and getting
  duplicate, emptier spans for the same call wasn't worth it.
- *A hardcoded USD-per-token table, not Langfuse's model-price registry* — Foundry
  serves `claude-opus-5-5`/`claude-sonnet-5-5` under names that aren't guaranteed to
  resolve against whatever provider/model strings Langfuse has on file for
  cost auto-calculation; computing cost ourselves from the same pricing already
  documented in `docs/architecture.md`'s latency/cost table avoids depending on that
  resolution succeeding.
- *`answer_followup`'s LiteLLM-logged generation lands as its own, unlinked trace*,
  not nested under `answer_followup`'s `@observe` span — linking them would mean
  reconfiguring PandasAI's global LLM singleton with the current trace/observation
  id on every call, for a follow-up path that already carries Phase 6a's documented
  best-effort posture elsewhere. Accepted, not solved, and written down rather than
  left for someone to discover by noticing a disconnected trace later.

---

### Phase 6c — Slackbot

- [ ] Slack Bolt app (Socket Mode): question in thread → answer + chart + SQL posted
      in-thread, as a thin adapter over `ask_question()`/`answer_followup()` (same
      pattern as the REST and A2A adapters)

### Phase 6d — Eval suite + CI

- [ ] 40-question golden set, each with an independently-computed expected value
- [ ] Scoring functions in code for all three dimensions (not eyeballed):
  - **Execution accuracy** — expected value vs. the pipeline's answer, within tolerance
  - **Faithfulness** — every number in the narrative traceable to the finding's stored
    `result_rows` (Phase 5's JSONB columns make this directly queryable)
  - **Refusal correctness** — out-of-scope questions in the set correctly hit 6a's
    refusal path
- [ ] CI job (GitHub Actions) that runs lint/type-check/unit/integration always, and
      the eval suite on PRs, failing the PR if any dimension regresses below threshold

**Evaluation note — this is the full rubric, each dimension needs a scoring function
in code, not eyeballing:**
- *Execution accuracy:* does the metric/SQL path return the expected value within a
  defined numeric tolerance?
- *Faithfulness:* is every number in the narrative answer traceable back to the
  returned SQL result (no fabricated figures)?
- *Refusal correctness:* do out-of-scope questions reliably hit the refusal path
  instead of producing a hallucinated answer?
