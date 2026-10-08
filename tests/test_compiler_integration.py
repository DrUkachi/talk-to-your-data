"""Executes compiled metrics against the real loaded DB and checks them against an
independently hand-written reference query -- the thing a snapshot test can't catch
(a compiler bug that's *consistently* wrong). Requires Postgres up and data loaded:

    docker compose --env-file .env -f docker/docker-compose.yml up -d
    uv run python scripts/load_data.py
    uv run pytest -m integration
"""

import pytest
from sqlalchemy import text

from talk_to_your_data.db import app_engine
from talk_to_your_data.semantic_layer.compiler import compile_metric

pytestmark = pytest.mark.integration

REFERENCE_QUERIES = {
    "revenue": """
        SELECT SUM(oi.price)
        FROM raw.order_items oi
        JOIN raw.orders o ON o.order_id = oi.order_id
        WHERE o.order_status = 'delivered'
    """,
    "order_count": "SELECT COUNT(DISTINCT order_id) FROM raw.orders",
    "customer_count": "SELECT COUNT(DISTINCT customer_unique_id) FROM raw.customers",
    "avg_review_score": "SELECT AVG(review_score) FROM raw.order_reviews",
    "avg_delivery_days": """
        SELECT AVG(EXTRACT(DAY FROM (order_delivered_customer_date - order_purchase_timestamp)))
        FROM raw.orders
        WHERE order_delivered_customer_date IS NOT NULL
    """,
}


@pytest.fixture(scope="module")
def engine():
    return app_engine()


@pytest.mark.parametrize("metric", list(REFERENCE_QUERIES))
def test_metric_matches_hand_written_reference(engine, metric):
    stmt, _ = compile_metric(metric)
    with engine.connect() as conn:
        (compiled_value,) = conn.execute(stmt).one()
        (reference_value,) = conn.execute(text(REFERENCE_QUERIES[metric])).one()
    assert float(compiled_value) == pytest.approx(float(reference_value))
