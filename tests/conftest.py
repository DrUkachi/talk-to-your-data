"""Shared fixtures for the cleaning-agent tests: a synthetic table with one
instance of each of the 4 check types, each with a known-correct fix -- the
regression set the ROADMAP's evaluation note asks for. Lives under the real
`clean` schema (not a separate test DB) but under a table name ("cleaning_fixture")
that doesn't collide with any real table, and is dropped/recreated per test.
"""

from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

from talk_to_your_data.agents.cleaning import profiling
from talk_to_your_data.db import app_engine

# Loaded here, not just lazily inside db.py/llm.py, so collection-time checks
# (e.g. a `skipif` on OPENAI_API_KEY) see the real environment too.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

FIXTURE_SCHEMA = "clean"
FIXTURE_TABLE = "cleaning_fixture"

profiling.TABLE_CHECKS[FIXTURE_TABLE] = [
    profiling.OrderingCheck(
        before="purchased_at", after="approved_at", description="approved before purchased"
    ),
    profiling.RangeCheck(column="amount", min_value=0, description="negative amount"),
    profiling.DuplicateCheck(description="fully duplicate fixture rows"),
    profiling.CategoricalNoiseCheck(
        column="status", description="whitespace/casing noise in status"
    ),
]


@pytest.fixture
def dirty_fixture_table() -> str:
    engine = app_engine()
    qualified = f'"{FIXTURE_SCHEMA}"."{FIXTURE_TABLE}"'
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {FIXTURE_SCHEMA}"))
        conn.execute(text(f"DROP TABLE IF EXISTS {qualified}"))
        conn.execute(
            text(
                f"""
                CREATE TABLE {qualified} (
                    id INT,
                    status TEXT,
                    purchased_at TIMESTAMP,
                    approved_at TIMESTAMP,
                    amount NUMERIC
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {qualified}
                    (id, status, purchased_at, approved_at, amount)
                VALUES
                    (1, 'delivered', '2024-01-01 10:00', '2024-01-01 11:00', 10.00),
                    (2, 'delivered', '2024-01-02 10:00', '2024-01-01 09:00', 20.00),
                    (3, 'delivered', '2024-01-03 10:00', '2024-01-03 11:00', -5.00),
                    (4, 'shipped',   '2024-01-04 10:00', '2024-01-04 11:00', 15.00),
                    (4, 'shipped',   '2024-01-04 10:00', '2024-01-04 11:00', 15.00),
                    (5, ' Delivered ', '2024-01-05 10:00', '2024-01-05 11:00', 30.00),
                    (6, 'delivered', '2024-01-06 10:00', '2024-01-06 11:00', 40.00)
                """
            )
        )
    yield FIXTURE_TABLE
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {qualified}"))
