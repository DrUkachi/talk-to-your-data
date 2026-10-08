"""Load the Olist CSVs from data/raw/ into Postgres as raw.<table> tables.

Landed data stays in its own `raw` schema, untouched, so Phase 3's cleaning
agent has a clear "raw in, clean out" boundary. Full truncate-and-reload each
run (not upsert) -- this is a static snapshot dataset, so that's simpler and
just as correct.

No network or credential dependency here on purpose (see fetch_data.py for
that) -- run with:

    uv run python scripts/load_data.py
"""

import logging
import sys
from pathlib import Path

import pandas as pd
from _shared import EXPECTED_FILES, table_name_for
from sqlalchemy import Engine, text

from talk_to_your_data.db import app_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
SCHEMA = "raw"

# pandas doesn't parse dates by default -- without this they'd land as `text`,
# making date arithmetic in Phase 2's metric compiler need a cast everywhere.
DATE_COLUMNS: dict[str, list[str]] = {
    "orders": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "order_items": ["shipping_limit_date"],
    "order_reviews": ["review_creation_date", "review_answer_timestamp"],
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("load_data")


def load_csv(engine: Engine, csv_path: Path) -> int:
    table = table_name_for(csv_path.name)
    date_cols = DATE_COLUMNS.get(table, [])
    df = pd.read_csv(csv_path, encoding="utf-8-sig", parse_dates=date_cols or None)
    df.to_sql(table, engine, schema=SCHEMA, if_exists="replace", index=False)
    return len(df)


def check_row_counts(engine: Engine, expected_counts: dict[str, int]) -> None:
    with engine.connect() as conn:
        for table, expected in expected_counts.items():
            actual = conn.execute(text(f'SELECT COUNT(*) FROM "{SCHEMA}"."{table}"')).scalar_one()
            if actual != expected:
                raise RuntimeError(f"{table}: loaded {expected} rows but DB has {actual}")
            logger.info("  %-30s %d rows (verified)", table, actual)


def data_quality_checks(engine: Engine) -> None:
    """Log-only checks: surface known Olist data-quality quirks, don't fix them (Phase 3's job)."""
    checks = [
        (
            "order_items rows with no matching order",
            """
            SELECT COUNT(*) FROM raw.order_items oi
            LEFT JOIN raw.orders o ON oi.order_id = o.order_id
            WHERE o.order_id IS NULL
            """,
        ),
        (
            "order_reviews rows with no matching order",
            """
            SELECT COUNT(*) FROM raw.order_reviews r
            LEFT JOIN raw.orders o ON r.order_id = o.order_id
            WHERE o.order_id IS NULL
            """,
        ),
        (
            "order_payments rows with no matching order",
            """
            SELECT COUNT(*) FROM raw.order_payments p
            LEFT JOIN raw.orders o ON p.order_id = o.order_id
            WHERE o.order_id IS NULL
            """,
        ),
    ]
    with engine.connect() as conn:
        for label, sql in checks:
            n = conn.execute(text(sql)).scalar_one()
            if n:
                logger.warning("  %s: %d", label, n)
            else:
                logger.info("  %s: none", label)

        total, null_delivered, null_category = conn.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM raw.orders),
                    (SELECT COUNT(*) FROM raw.orders WHERE order_delivered_customer_date IS NULL),
                    (SELECT COUNT(*) FROM raw.products WHERE product_category_name IS NULL)
                """
            )
        ).one()
        logger.info(
            "  orders.order_delivered_customer_date null rate: %d/%d (%.1f%%)",
            null_delivered,
            total,
            100 * null_delivered / total,
        )
        logger.info("  products.product_category_name nulls: %d", null_category)


def main() -> None:
    missing = [f for f in EXPECTED_FILES if not (RAW_DIR / f).exists()]
    if missing:
        logger.error("Missing CSVs in %s: %s. Run scripts/fetch_data.py first.", RAW_DIR, missing)
        sys.exit(1)

    engine = app_engine()
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))

    logger.info("Loading %d CSVs into schema '%s'...", len(EXPECTED_FILES), SCHEMA)
    counts: dict[str, int] = {}
    for filename in EXPECTED_FILES:
        table = table_name_for(filename)
        n = load_csv(engine, RAW_DIR / filename)
        counts[table] = n
        logger.info("  %-30s %d rows loaded from %s", table, n, filename)

    logger.info("Verifying row counts in Postgres...")
    check_row_counts(engine, counts)

    logger.info("Data-quality checks (logged, not enforced -- Phase 3 handles fixes)...")
    data_quality_checks(engine)

    logger.info("Done.")


if __name__ == "__main__":
    main()
