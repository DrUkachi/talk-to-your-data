"""Shared Postgres checkpointer wiring for every LangGraph graph in this project.
One schema (`langgraph`), one connection pattern -- `thread_id` is what
distinguishes one graph's runs from another's, not separate tables/schemas.

A fresh connection is opened per call rather than one being kept open for an
app's lifetime -- deliberate, not an oversight: see agents/cleaning/graph.py's
original design note. A fresh connection per call is what proves resume works
off Postgres state, not an in-memory object a long-lived process still has
around.
"""

import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from dotenv import load_dotenv
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import text

from .db import REPO_ROOT, app_engine

CHECKPOINT_SCHEMA = "langgraph"


def checkpointer_conn_string() -> str:
    load_dotenv(REPO_ROOT / ".env")
    base = (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return f"{base}?options=-c%20search_path%3D{CHECKPOINT_SCHEMA}"


@contextmanager
def compiled_graph(
    build_graph: Callable[[PostgresSaver], CompiledStateGraph],
) -> Iterator[CompiledStateGraph]:
    with app_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}"))
    with PostgresSaver.from_conn_string(checkpointer_conn_string()) as saver:
        saver.setup()
        yield build_graph(saver)


@asynccontextmanager
async def compiled_graph_async(
    build_graph: Callable[[AsyncPostgresSaver], CompiledStateGraph],
) -> AsyncIterator[CompiledStateGraph]:
    """For graphs with async nodes (e.g. the EDA agent's MCP client calls) --
    `fastmcp.Client` is async-only, so any node that calls it must be a coroutine,
    which means the graph needs an async checkpointer too (a sync PostgresSaver
    would block the event loop on every checkpoint read/write)."""
    with app_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}"))
    async with AsyncPostgresSaver.from_conn_string(checkpointer_conn_string()) as saver:
        await saver.setup()
        yield build_graph(saver)
