"""Durable record of answered questions -- distinct from the LangGraph checkpointer
(which persists conversational/execution state, thread-scoped and resumable like
Phase 3's interrupts). This is the business-level knowledge base: it survives
independent of any thread and is queryable on its own ("what have we found before").
"""

import uuid
from typing import Any

from sqlalchemy import text

from talk_to_your_data.db import app_engine

from .state import Finding

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


def save_finding(thread_id: str, finding: Finding) -> str:
    finding_id = str(uuid.uuid4())
    with app_engine().begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO "{SCHEMA}"."findings"
                    (id, thread_id, question, sql, result_summary, chart_ref,
                     caveats, confidence, interpretation)
                VALUES
                    (:id, :thread_id, :question, :sql, :result_summary, :chart_ref,
                     :caveats, :confidence, :interpretation)
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
            },
        )
    return finding_id


def list_findings(limit: int = 20) -> list[dict[str, Any]]:
    with app_engine().connect() as conn:
        result = conn.execute(
            text(f'SELECT * FROM "{SCHEMA}"."findings" ORDER BY created_at DESC LIMIT :limit'),
            {"limit": limit},
        )
        rows = [dict(row._mapping) for row in result]
    # Postgres/psycopg hands back a uuid.UUID object for the `id` column; stringify
    # it so callers can compare directly against what save_finding() returned,
    # rather than needing to know about this type quirk themselves.
    for row in rows:
        row["id"] = str(row["id"])
    return rows
