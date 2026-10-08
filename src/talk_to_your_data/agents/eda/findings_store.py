"""Durable record of answered questions -- distinct from the LangGraph checkpointer
(which persists conversational/execution state, thread-scoped and resumable like
Phase 3's interrupts). This is the business-level knowledge base: it survives
independent of any thread and is queryable on its own ("what have we found before").

Also stores the raw result (result_columns/result_rows) so Phase 5's follow-up
questions can reconstruct the exact DataFrame the user already saw, without
re-querying Postgres -- a follow-up seeing different data than what was actually
shown would be confusing, and the ROADMAP's constraint is that PandasAI never
originates a query against the database anyway.
"""

import json
import uuid
from typing import Any

from sqlalchemy import text

from talk_to_your_data.db import app_engine

from .state import Finding, SqlResult

SCHEMA = "findings"


def ensure_findings_table() -> None:
    with app_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{SCHEMA}"."findings" (
                    id UUID PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    chart_ref TEXT,
                    caveats TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    interpretation TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        # Added in Phase 5, as plain idempotent ALTERs rather than a migration
        # framework -- consistent with how every table in this project has been
        # evolved so far (see scripts/setup_db_roles.py, load_data.py).
        for ddl in (
            f'ALTER TABLE "{SCHEMA}"."findings" ADD COLUMN IF NOT EXISTS result_columns JSONB',
            f'ALTER TABLE "{SCHEMA}"."findings" ADD COLUMN IF NOT EXISTS result_rows JSONB',
            f'ALTER TABLE "{SCHEMA}"."findings" ADD COLUMN IF NOT EXISTS parent_finding_id UUID',
        ):
            conn.execute(text(ddl))


def save_finding(
    thread_id: str,
    finding: Finding,
    sql_result: SqlResult,
    parent_finding_id: str | None = None,
) -> str:
    finding_id = str(uuid.uuid4())
    with app_engine().begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO "{SCHEMA}"."findings"
                    (id, thread_id, question, sql, result_summary, chart_ref,
                     caveats, confidence, interpretation,
                     result_columns, result_rows, parent_finding_id)
                VALUES
                    (:id, :thread_id, :question, :sql, :result_summary, :chart_ref,
                     :caveats, :confidence, :interpretation,
                     CAST(:result_columns AS JSONB), CAST(:result_rows AS JSONB),
                     :parent_finding_id)
                """
            ),
            {
                "id": finding_id,
                "thread_id": thread_id,
                "question": finding.question,
                "sql": finding.sql,
                "result_summary": finding.result_summary,
                "chart_ref": finding.chart_ref,
                "caveats": finding.caveats,
                "confidence": finding.confidence,
                "interpretation": finding.interpretation,
                "result_columns": json.dumps(sql_result.columns),
                "result_rows": json.dumps(sql_result.rows, default=str),
                "parent_finding_id": parent_finding_id,
            },
        )
    return finding_id


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    # Postgres/psycopg hands back uuid.UUID objects for UUID columns; stringify
    # so callers can compare directly against what save_finding() returned,
    # rather than needing to know about this type quirk themselves.
    row["id"] = str(row["id"])
    if row.get("parent_finding_id") is not None:
        row["parent_finding_id"] = str(row["parent_finding_id"])
    return row


def list_findings(limit: int = 20) -> list[dict[str, Any]]:
    with app_engine().connect() as conn:
        result = conn.execute(
            text(f'SELECT * FROM "{SCHEMA}"."findings" ORDER BY created_at DESC LIMIT :limit'),
            {"limit": limit},
        )
        rows = [dict(row._mapping) for row in result]
    return [_normalize_row(row) for row in rows]


def get_latest_finding_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Phase 6c's Slackbot uses this to decide whether a message replying inside
    a Slack thread is a follow-up on that thread's most recent finding, or an
    unrelated thread it's never answered in (see slackbot/app.py)."""
    with app_engine().connect() as conn:
        result = conn.execute(
            text(
                f'SELECT * FROM "{SCHEMA}"."findings" WHERE thread_id = :thread_id '
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"thread_id": thread_id},
        )
        row = result.mappings().first()
    return _normalize_row(dict(row)) if row is not None else None


def get_finding(finding_id: str) -> dict[str, Any] | None:
    with app_engine().connect() as conn:
        result = conn.execute(
            text(f'SELECT * FROM "{SCHEMA}"."findings" WHERE id = :id'),
            {"id": finding_id},
        )
        row = result.mappings().first()
    return _normalize_row(dict(row)) if row is not None else None
