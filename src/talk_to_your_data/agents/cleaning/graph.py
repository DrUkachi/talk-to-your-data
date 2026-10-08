"""The cleaning graph: profile -> propose_fixes -> [interrupt] -> apply -> validate,
looping back to profile on validation failure (capped at max_attempts).

cleaning_graph() builds a fresh graph + Postgres checkpointer connection on every
call rather than keeping one open for the app's lifetime -- deliberately, since a
fresh connection per call is exactly what proves resume works off Postgres state,
not an in-memory object a long-lived process happens to still have around.
"""

from contextlib import AbstractContextManager

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from sqlalchemy import text

from talk_to_your_data.checkpointer import compiled_graph
from talk_to_your_data.db import app_engine

from . import fix_strategies, llm, profiling
from .state import CleaningState, FixProposal, ProfileFinding, ValidationResult

CLEAN_SCHEMA = "clean"
DEFAULT_MAX_ATTEMPTS = 3


def ensure_clean_table(table: str) -> None:
    """Materializes clean.<table> from raw.<table> the first time only -- a second
    run builds on prior fixes instead of resetting to raw."""
    with app_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {CLEAN_SCHEMA}"))
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": CLEAN_SCHEMA, "table": table},
        ).fetchone()
        if exists is None:
            conn.execute(
                text(f'CREATE TABLE "{CLEAN_SCHEMA}"."{table}" AS SELECT * FROM "raw"."{table}"')
            )


def ensure_fix_log_table() -> None:
    with app_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {CLEAN_SCHEMA}"))
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CLEAN_SCHEMA}"."fix_log" (
                    id SERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    rows_affected INTEGER NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )


def log_applied_fixes(run_id: str, table: str, applied_fixes: list[dict]) -> None:
    if not applied_fixes:
        return
    with app_engine().begin() as conn:
        for fix in applied_fixes:
            conn.execute(
                text(
                    f'INSERT INTO "{CLEAN_SCHEMA}"."fix_log" '
                    "(run_id, table_name, finding_id, strategy, sql, rows_affected) "
                    "VALUES (:run_id, :table, :finding_id, :strategy, :sql, :rows)"
                ),
                {
                    "run_id": run_id,
                    "table": table,
                    "finding_id": fix["finding_id"],
                    "strategy": fix["strategy"],
                    "sql": fix["sql"],
                    "rows": fix["rows_affected"],
                },
            )


def profile_node(state: CleaningState) -> dict:
    findings = profiling.run_profile(app_engine(), CLEAN_SCHEMA, state["table"])
    return {
        "findings": [f.model_dump(mode="json") for f in findings],
        "status": "awaiting_approval" if findings else "done",
    }


def _route_after_profile(state: CleaningState) -> str:
    return "propose_fixes" if state["findings"] else END


def propose_fixes_node(state: CleaningState) -> dict:
    findings = [ProfileFinding.model_validate(f) for f in state["findings"]]
    proposals = llm.propose_fixes(findings)
    return {"proposals": [p.model_dump(mode="json") for p in proposals]}


def human_approval_node(state: CleaningState) -> dict:
    decision = interrupt(
        {"table": state["table"], "findings": state["findings"], "proposals": state["proposals"]}
    )
    return {"approved_finding_ids": decision.get("approved_finding_ids", []), "status": "applying"}


def apply_node(state: CleaningState) -> dict:
    findings = [ProfileFinding.model_validate(f) for f in state["findings"]]
    proposals = [FixProposal.model_validate(p) for p in state["proposals"]]
    applied = fix_strategies.apply_fixes(
        app_engine(), CLEAN_SCHEMA, findings, proposals, state["approved_finding_ids"]
    )
    return {"applied_fixes": [a.model_dump(mode="json") for a in applied], "status": "validating"}


def validate_node(state: CleaningState) -> dict:
    remaining = profiling.run_profile(app_engine(), CLEAN_SCHEMA, state["table"])
    result = ValidationResult(passed=not remaining, remaining_findings=remaining)
    attempt = state.get("attempt", 0) + 1
    max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    status = "done" if result.passed else ("failed" if attempt >= max_attempts else "profiling")
    return {"validation": result.model_dump(mode="json"), "attempt": attempt, "status": status}


def _route_after_validate(state: CleaningState) -> str:
    return "profile" if state["status"] == "profiling" else END


def build_graph(checkpointer: PostgresSaver) -> CompiledStateGraph:
    g: StateGraph = StateGraph(CleaningState)
    g.add_node("profile", profile_node)
    g.add_node("propose_fixes", propose_fixes_node)
    g.add_node("human_approval", human_approval_node)
    g.add_node("apply", apply_node)
    g.add_node("validate", validate_node)

    g.set_entry_point("profile")
    g.add_conditional_edges(
        "profile", _route_after_profile, {"propose_fixes": "propose_fixes", END: END}
    )
    g.add_edge("propose_fixes", "human_approval")
    g.add_edge("human_approval", "apply")
    g.add_edge("apply", "validate")
    g.add_conditional_edges("validate", _route_after_validate, {"profile": "profile", END: END})

    return g.compile(checkpointer=checkpointer)


def cleaning_graph() -> AbstractContextManager[CompiledStateGraph]:
    return compiled_graph(build_graph)
