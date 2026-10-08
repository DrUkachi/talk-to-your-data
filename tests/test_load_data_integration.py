"""Requires Postgres up and data loaded:

docker compose --env-file .env -f docker/docker-compose.yml up -d
uv run python scripts/load_data.py
uv run pytest -m integration
"""

import os
from pathlib import Path

import pytest
from _shared import EXPECTED_FILES, table_name_for
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def engine():
    load_dotenv(REPO_ROOT / ".env")
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


@pytest.mark.parametrize("filename", EXPECTED_FILES)
def test_raw_table_exists_and_has_rows(engine, filename):
    table = table_name_for(filename)
    with engine.connect() as conn:
        count = conn.execute(text(f'SELECT COUNT(*) FROM raw."{table}"')).scalar_one()
    assert count > 0


def test_date_columns_loaded_as_real_timestamps_not_text(engine):
    with engine.connect() as conn:
        dtype = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'raw' AND table_name = 'orders' "
                "AND column_name = 'order_purchase_timestamp'"
            )
        ).scalar_one()
    assert dtype == "timestamp without time zone"


@pytest.mark.parametrize(
    "child,parent",
    [("order_items", "orders"), ("order_reviews", "orders"), ("order_payments", "orders")],
)
def test_no_orphan_rows_against_orders(engine, child, parent):
    with engine.connect() as conn:
        orphans = conn.execute(
            text(
                f"SELECT COUNT(*) FROM raw.{child} c "
                f"LEFT JOIN raw.{parent} p ON c.order_id = p.order_id "
                "WHERE p.order_id IS NULL"
            )
        ).scalar_one()
    assert orphans == 0
