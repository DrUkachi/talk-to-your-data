"""Pure unit tests (no DB/LLM) for the SQL guardrail -- sqlglot parses
statically, so the whole negative/positive matrix runs without Postgres.
"""

import pytest

from talk_to_your_data.guardrails.sql_guard import (
    MAX_ROW_LIMIT,
    SqlGuardError,
    clamp_limit,
    mask_row,
    validate_sql,
)

# --- negative: must reject ---

REJECTED_QUERIES = [
    ("DELETE FROM raw.orders", "plain DELETE"),
    ("UPDATE raw.orders SET order_status = 'x'", "plain UPDATE"),
    ("INSERT INTO raw.orders (order_id) VALUES ('x')", "plain INSERT"),
    ("DROP TABLE raw.orders", "plain DROP"),
    (
        "WITH x AS (DELETE FROM raw.orders RETURNING *) SELECT count(*) FROM x",
        "data-modifying CTE -- top-level statement type is Select, must walk the full tree",
    ),
    ("SELECT 1; DROP TABLE raw.orders;", "semicolon-stacked statements"),
    ("SELECT 1; -- DROP TABLE raw.orders", "comment-obscured second statement"),
    ("SELECT * FROM pg_catalog.pg_tables", "system catalog access"),
    ("SELECT * FROM information_schema.tables", "information_schema access"),
    ("SELECT * FROM orders", "unqualified table name"),
    ("SELECT * FROM clean.orders", "disallowed schema (clean, not raw)"),
    ("SELECT pg_sleep(100)", "disallowed function"),
    ("SELECT pg_read_file('/etc/passwd')", "disallowed function (file read)"),
]


@pytest.mark.parametrize("query,label", REJECTED_QUERIES, ids=[r[1] for r in REJECTED_QUERIES])
def test_rejects(query, label):
    with pytest.raises(SqlGuardError):
        validate_sql(query)


# --- positive: must NOT reject (a guardrail that's too broad is also a bug) ---

ACCEPTED_QUERIES = [
    ("SELECT * FROM raw.orders", "simple select"),
    ("sElEcT * FrOm raw.orders", "case doesn't matter"),
    ("SELECT * FROM raw.orders;", "trailing semicolon alone is fine"),
    (
        "WITH recent AS (SELECT order_id FROM raw.orders) SELECT * FROM recent",
        "CTE alias must not be treated as an unqualified table",
    ),
    (
        "SELECT 1 AS a FROM raw.orders UNION SELECT 2 AS a FROM raw.orders",
        "UNION parses to a different AST node than Select/With",
    ),
    (
        "SELECT order_status, COUNT(*) OVER (PARTITION BY order_status) FROM raw.orders",
        "window function",
    ),
    (
        "SELECT o.order_id FROM raw.orders o JOIN raw.order_items oi ON oi.order_id = o.order_id",
        "join",
    ),
]


@pytest.mark.parametrize("query,label", ACCEPTED_QUERIES, ids=[a[1] for a in ACCEPTED_QUERIES])
def test_accepts(query, label):
    validate_sql(query)  # must not raise


# --- row limits ---


def test_clamp_limit_caps_at_max():
    assert clamp_limit(MAX_ROW_LIMIT * 10) == MAX_ROW_LIMIT


def test_clamp_limit_passes_through_small_values():
    assert clamp_limit(10) == 10


@pytest.mark.parametrize("bad_limit", [0, -1, -1000])
def test_clamp_limit_rejects_non_positive(bad_limit):
    with pytest.raises(SqlGuardError):
        clamp_limit(bad_limit)


# --- masking ---


def test_masks_known_geolocation_columns():
    row = {"geolocation_lat": -23.54562128115268, "geolocation_lng": -46.63929204800168}
    masked = mask_row(row)
    assert masked["geolocation_lat"] == -23.5
    assert masked["geolocation_lng"] == -46.6


def test_masks_zip_code_prefix_columns_by_suffix():
    row = {"customer_zip_code_prefix": "14409", "seller_zip_code_prefix": "13023"}
    masked = mask_row(row)
    assert masked["customer_zip_code_prefix"] == "14"
    assert masked["seller_zip_code_prefix"] == "13"


def test_masking_passes_through_null():
    row = {"geolocation_lat": None, "customer_zip_code_prefix": None}
    masked = mask_row(row)
    assert masked["geolocation_lat"] is None
    assert masked["customer_zip_code_prefix"] is None


def test_masking_leaves_unrelated_columns_alone():
    row = {"order_status": "delivered", "revenue": 123.45}
    assert mask_row(row) == row


def test_KNOWN_LIMITATION_masking_is_bypassed_by_column_aliasing():
    """Documents, rather than hides, a real gap: masking matches on OUTPUT column
    name. An aliased or aggregated column evades it entirely. Closing this would
    need column-provenance tracking through arbitrary SQL, or masked Postgres
    views -- out of scope for this project (see docs/architecture.md)."""
    row_with_alias = {"x": -23.54562128115268}  # was `geolocation_lat AS x`
    assert mask_row(row_with_alias) == row_with_alias  # NOT masked -- the bypass
