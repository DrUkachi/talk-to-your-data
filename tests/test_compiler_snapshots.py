"""Pins (metric, params) -> exact SQL text, so a compiler change shows up as an
explicit diff in review instead of silently changing what gets sent to Postgres
(and posted to Slack as "the SQL used"). No DB needed -- these only call compile_metric.
"""

import pytest

from talk_to_your_data.semantic_layer.compiler import compile_metric

CASES = [
    (
        "revenue",
        {},
        "SELECT sum(oi.price) AS revenue \n"
        "FROM \n"
        "            raw.order_items oi\n"
        "            JOIN raw.orders o ON o.order_id = oi.order_id\n"
        "            JOIN raw.products p ON p.product_id = oi.product_id\n"
        "            LEFT JOIN raw.product_category_name_translation t\n"
        "                ON t.product_category_name = p.product_category_name\n"
        "         \n"
        "WHERE o.order_status IN ('delivered')",
    ),
    (
        "freight_revenue",
        {},
        "SELECT sum(oi.freight_value) AS freight_revenue \n"
        "FROM \n"
        "            raw.order_items oi\n"
        "            JOIN raw.orders o ON o.order_id = oi.order_id\n"
        "            JOIN raw.products p ON p.product_id = oi.product_id\n"
        "            LEFT JOIN raw.product_category_name_translation t\n"
        "                ON t.product_category_name = p.product_category_name\n"
        "         \n"
        "WHERE o.order_status IN ('delivered')",
    ),
    (
        "order_count",
        {},
        "SELECT count(DISTINCT o.order_id) AS order_count \n"
        "FROM \n"
        "            raw.orders o\n"
        "            JOIN raw.customers c ON c.customer_id = o.customer_id\n"
        "        ",
    ),
    (
        "customer_count",
        {},
        "SELECT count(DISTINCT c.customer_unique_id) AS customer_count \n"
        "FROM \n"
        "            raw.orders o\n"
        "            JOIN raw.customers c ON c.customer_id = o.customer_id\n"
        "        ",
    ),
    (
        "avg_review_score",
        {},
        "SELECT avg(r.review_score) AS avg_review_score \n"
        "FROM \n"
        "            raw.order_reviews r\n"
        "            JOIN raw.orders o ON o.order_id = r.order_id\n"
        "        ",
    ),
    (
        "review_count",
        {},
        "SELECT count(*) AS review_count \n"
        "FROM \n"
        "            raw.order_reviews r\n"
        "            JOIN raw.orders o ON o.order_id = r.order_id\n"
        "        ",
    ),
    (
        "avg_delivery_days",
        {},
        "SELECT avg(EXTRACT(EPOCH FROM "
        "(o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400.0) "
        "AS avg_delivery_days \n"
        "FROM raw.orders o \n"
        "WHERE o.order_delivered_customer_date IS NOT NULL",
    ),
    (
        "late_delivery_rate",
        {},
        "SELECT avg(CASE WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date "
        "THEN 1.0 ELSE 0.0 END) AS late_delivery_rate \n"
        "FROM raw.orders o \n"
        "WHERE o.order_delivered_customer_date IS NOT NULL",
    ),
    (
        "avg_payment_installments",
        {},
        "SELECT avg(p.payment_installments) AS avg_payment_installments \n"
        "FROM \n"
        "            raw.order_payments p\n"
        "            JOIN raw.orders o ON o.order_id = p.order_id\n"
        "        ",
    ),
    (
        "revenue",
        {"dimensions": ["product_category"], "time_grain": "month", "limit": 5},
        "SELECT date_trunc('month', o.order_purchase_timestamp) AS period, "
        "COALESCE(t.product_category_name_english, p.product_category_name) AS product_category, "
        "sum(oi.price) AS revenue \n"
        "FROM \n"
        "            raw.order_items oi\n"
        "            JOIN raw.orders o ON o.order_id = oi.order_id\n"
        "            JOIN raw.products p ON p.product_id = oi.product_id\n"
        "            LEFT JOIN raw.product_category_name_translation t\n"
        "                ON t.product_category_name = p.product_category_name\n"
        "         \n"
        "WHERE o.order_status IN ('delivered') "
        "GROUP BY date_trunc('month', o.order_purchase_timestamp), "
        "COALESCE(t.product_category_name_english, p.product_category_name) ORDER BY "
        "date_trunc('month', o.order_purchase_timestamp), "
        "COALESCE(t.product_category_name_english, p.product_category_name) \n"
        " LIMIT 5",
    ),
    (
        "order_count",
        {"filters": {"order_status": ["delivered", "shipped"]}},
        "SELECT count(DISTINCT o.order_id) AS order_count \n"
        "FROM \n"
        "            raw.orders o\n"
        "            JOIN raw.customers c ON c.customer_id = o.customer_id\n"
        "         \n"
        "WHERE o.order_status IN ('delivered', 'shipped')",
    ),
]


@pytest.mark.parametrize(
    "metric,kwargs,expected_sql", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)]
)
def test_compiled_sql_matches_snapshot(metric, kwargs, expected_sql):
    _, sql = compile_metric(metric, **kwargs)
    assert sql == expected_sql
