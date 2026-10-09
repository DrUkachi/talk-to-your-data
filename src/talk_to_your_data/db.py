"""Engine factories for the two Postgres roles this project uses.

`app_engine()` (POSTGRES_USER) can create/modify tables -- used by scripts/load_data.py
and scripts/setup_db_roles.py. `readonly_engine()` (POSTGRES_READONLY_USER) can only
SELECT from the `raw` schema -- used by the MCP server, which is the only thing
agents are allowed to query Postgres through.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# Agent-written SQL is valid-but-unbounded: a correlated NOT EXISTS over an unindexed
# table ran for 10+ minutes in the eval suite and hung every question behind it.
READONLY_STATEMENT_TIMEOUT_MS = 15_000


def _engine(user_env: str, password_env: str, **connect_args: str) -> Engine:
    load_dotenv(REPO_ROOT / ".env")
    url = (
        f"postgresql+psycopg://{os.environ[user_env]}:{os.environ[password_env]}"
        f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url, connect_args=connect_args)


def app_engine() -> Engine:
    return _engine("POSTGRES_USER", "POSTGRES_PASSWORD")


def readonly_engine() -> Engine:
    return _engine(
        "POSTGRES_READONLY_USER",
        "POSTGRES_READONLY_PASSWORD",
        options=f"-c statement_timeout={READONLY_STATEMENT_TIMEOUT_MS}",
    )
