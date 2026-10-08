# ROADMAP

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

Workflow for every phase: propose a plan → get explicit approval → implement with
tests → write the phase summary (what was built, key design decisions + trade-offs)
→ check off tasks below and note any deviations from plan.

**Current status: Phase 2 complete. Phase 3 not started.**

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

## Phase 3 — LangGraph data-cleaning agent

- [ ] Graph: profile → propose fixes → human approval (interrupt) → apply → validate →
      loop on failure, using a Postgres checkpointer
  - **Acceptance:** graph compiles with the checkpointer; an approval interrupt
    actually pauses execution and resumes correctly after approval; a deliberately
    broken dataset triggers at least one fix→apply→validate loop.
- [ ] FastAPI service wrapping the graph, Dockerized
  - **Acceptance:** `docker compose up` brings up API + Postgres; POST starts a run and
    returns a run id; GET returns current state / pending approval for that run.
- [ ] Unit tests per node (profile/propose/apply/validate), not just end-to-end

**Evaluation note:** quality here means "fixed real problems without touching values it
shouldn't have." Plan: a small set of synthetic data-quality injections (nulls, dupes,
bad types) with known-correct fixes, re-run as a regression set whenever the
fix-proposal prompt changes.

---

## Phase 4 — Stateful EDA + multi-agent

- [ ] Persistent state + findings store (a "finding" = question, SQL, result summary,
      chart reference, confidence/caveats)
- [ ] Supervisor graph routing to SQL/metrics, analysis, and narrative agents
- [ ] One agent exposed over the A2A protocol with an agent card
  - **Acceptance:** an external A2A client can discover the agent card and get a valid
    response to a routed request.

**Evaluation note:** first phase with genuinely multi-step answers — start a *miniature*
golden-question set here (5–10 questions) even though the full 40-question suite is
Phase 6, so supervisor routing has a regression check from day one instead of being
eyeballed until the capstone.

---

## Phase 5 — System design

- [ ] `docs/architecture.md`: Mermaid component diagram, failure-modes table,
      latency and cost estimate per question
- [ ] PandasAI added as a tool for *follow-up* questions on already-returned query
      results (not for the initial query — the semantic layer / MCP path stays the only
      way the first query hits Postgres)

**Evaluation note:** cost and latency become explicit eval dimensions here, not just
accuracy. This phase should produce the *rubric* (scoring functions and thresholds),
which Phase 6's CI eval then reports against — don't leave the rubric implicit until
the capstone.

---

## Phase 6 — Capstone

- [ ] Slackbot (Bolt, Socket Mode): question in thread → answer + chart + SQL posted
      in-thread
- [ ] Guardrails: read-only DB role enforced at the connection level, SQL validation
      (parse + allow-list, not just string matching), row limits, PII masking (check
      which Olist fields count — e.g. geolocation lat/lng + customer city can be
      quasi-identifying even without names), out-of-scope question refusal
- [ ] Langfuse tracing wired through every agent/tool call
- [ ] Eval suite: 40-question golden set; CI job that fails the PR if scores regress
      below threshold

**Evaluation note — this is the full rubric, each dimension needs a scoring function
in code, not eyeballing:**
- *Execution accuracy:* does the metric/SQL path return the expected value within a
  defined numeric tolerance?
- *Faithfulness:* is every number in the narrative answer traceable back to the
  returned SQL result (no fabricated figures)?
- *Refusal correctness:* do out-of-scope questions reliably hit the refusal path
  instead of producing a hallucinated answer?
