"""FastAPI service wrapping the cleaning graph. One request per graph step
(start / approve), synchronous -- human-in-the-loop latency is minutes between
approval steps, not something that needs background-job polling infrastructure.
"""

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from langgraph.types import Command, RunnableConfig
from pydantic import BaseModel

from . import graph
from .profiling import TABLE_CHECKS

app = FastAPI(title="talk-to-your-data cleaning API")


def _config(run_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": run_id}}


class StartRunRequest(BaseModel):
    table: str
    max_attempts: int = graph.DEFAULT_MAX_ATTEMPTS


class ApproveRequest(BaseModel):
    approved_finding_ids: list[str]


def _public_state(run_id: str, state: dict[str, Any], paused: bool | None = None) -> dict[str, Any]:
    if paused is None:
        paused = "__interrupt__" in state
    return {
        "run_id": run_id,
        "status": state.get("status"),
        "paused": paused,
        "table": state.get("table"),
        "findings": state.get("findings", []),
        "proposals": state.get("proposals", []),
        "applied_fixes": state.get("applied_fixes", []),
        "validation": state.get("validation"),
        "attempt": state.get("attempt", 0),
    }


@app.post("/cleaning-runs")
def start_run(req: StartRunRequest) -> dict[str, Any]:
    if req.table not in TABLE_CHECKS:
        raise HTTPException(
            400,
            f"no profiling checks defined for table '{req.table}'. Known: {sorted(TABLE_CHECKS)}",
        )
    graph.ensure_clean_table(req.table)
    graph.ensure_fix_log_table()

    run_id = str(uuid.uuid4())
    config = _config(run_id)
    with graph.cleaning_graph() as g:
        result = g.invoke({"table": req.table, "max_attempts": req.max_attempts}, config=config)
        graph.log_applied_fixes(run_id, req.table, result.get("applied_fixes", []))
    return _public_state(run_id, result)


@app.get("/cleaning-runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    config = _config(run_id)
    with graph.cleaning_graph() as g:
        snapshot = g.get_state(config)
    if not snapshot.values:
        raise HTTPException(404, f"no run with id '{run_id}'")
    return _public_state(run_id, snapshot.values, paused=bool(snapshot.next))


@app.post("/cleaning-runs/{run_id}/approve")
def approve_run(run_id: str, req: ApproveRequest) -> dict[str, Any]:
    config = _config(run_id)
    with graph.cleaning_graph() as g:
        snapshot = g.get_state(config)
        if not snapshot.values:
            raise HTTPException(404, f"no run with id '{run_id}'")
        if not snapshot.next:
            raise HTTPException(409, f"run '{run_id}' is not awaiting approval")
        result = g.invoke(
            Command(resume={"approved_finding_ids": req.approved_finding_ids}), config=config
        )
        graph.log_applied_fixes(run_id, snapshot.values["table"], result.get("applied_fixes", []))
    return _public_state(run_id, result)
