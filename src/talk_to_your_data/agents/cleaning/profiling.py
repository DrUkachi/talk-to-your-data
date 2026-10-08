"""Deterministic data-quality checks -- no LLM involved, by design. Reproducible
profiling means the LLM's judgment (in llm.py) is spent on "what fix makes sense",
not "what's wrong", which would be harder to test and harder to trust.

TABLE_CHECKS is a per-table registry (code, not YAML) for the same reason Phase 2's
models are code: what counts as "impossible" is domain knowledge, reviewed like any
other business logic.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .state import ProfileFinding

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


@dataclass(frozen=True)
class OrderingCheck:
    before: str
    after: str
    description: str


@dataclass(frozen=True)
class RangeCheck:
    column: str
    description: str
    min_value: float | None = None
    max_value: float | None = None


@dataclass(frozen=True)
class DuplicateCheck:
    description: str


@dataclass(frozen=True)
class CategoricalNoiseCheck:
    column: str
    description: str


Check = OrderingCheck | RangeCheck | DuplicateCheck | CategoricalNoiseCheck

# min/max values and column names here are Python literals from this registry, not
# user input -- safe to interpolate directly, same trust boundary as Phase 2's models.
TABLE_CHECKS: dict[str, list[Check]] = {
    "orders": [
        OrderingCheck(
            before="order_purchase_timestamp",
            after="order_approved_at",
            description="order approved before it was purchased",
        ),
        OrderingCheck(
            before="order_purchase_timestamp",
            after="order_delivered_customer_date",
            description="order delivered before it was purchased",
        ),
        DuplicateCheck(description="fully duplicate order rows"),
        CategoricalNoiseCheck(
            column="order_status", description="whitespace/casing noise in order_status"
        ),
    ],
    "products": [
        RangeCheck(
            column="product_weight_g", min_value=0, description="non-positive product weight"
        ),
        RangeCheck(column="product_photos_qty", min_value=0, description="negative photo count"),
        DuplicateCheck(description="fully duplicate product rows"),
        CategoricalNoiseCheck(
            column="product_category_name",
            description="whitespace/casing noise in product_category_name",
        ),
    ],
    "order_reviews": [
        RangeCheck(
            column="review_score", min_value=1, max_value=5, description="review score outside 1-5"
        ),
        DuplicateCheck(description="fully duplicate review rows"),
    ],
    "order_items": [
        RangeCheck(column="price", min_value=0, description="non-positive price"),
        RangeCheck(column="freight_value", min_value=0, description="negative freight value"),
        DuplicateCheck(description="fully duplicate order_item rows"),
    ],
}


def _get_columns(engine: Engine, schema: str, table: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table "
                "ORDER BY ordinal_position"
            ),
            {"schema": schema, "table": table},
        ).fetchall()
    if not rows:
        raise ValueError(f"table '{schema}.{table}' not found (or has no columns)")
    return [r[0] for r in rows]


def _run_ordering_check(
    engine: Engine, schema: str, table: str, check: OrderingCheck, index: int
) -> ProfileFinding | None:
    before, after = quote_ident(check.before), quote_ident(check.after)
    predicate = f"{after} < {before}"
    sql = f"SELECT COUNT(*) FROM {quote_ident(schema)}.{quote_ident(table)} WHERE {predicate}"
    with engine.connect() as conn:
        count = conn.execute(text(sql)).scalar_one()
    if count == 0:
        return None
    return ProfileFinding(
        id=f"{table}-ordering-{index}",
        table=table,
        check="logical_ordering",
        column=check.after,
        description=check.description,
        affected_row_count=count,
        details={
            "predicate": predicate,
            "before_column": check.before,
            "after_column": check.after,
        },
    )


def _run_range_check(
    engine: Engine, schema: str, table: str, check: RangeCheck, index: int
) -> ProfileFinding | None:
    col = quote_ident(check.column)
    conditions = []
    if check.min_value is not None:
        conditions.append(f"{col} < {check.min_value}")
    if check.max_value is not None:
        conditions.append(f"{col} > {check.max_value}")
    predicate = " OR ".join(conditions)
    sql = f"SELECT COUNT(*) FROM {quote_ident(schema)}.{quote_ident(table)} WHERE {predicate}"
    with engine.connect() as conn:
        count = conn.execute(text(sql)).scalar_one()
    if count == 0:
        return None
    return ProfileFinding(
        id=f"{table}-range-{check.column}-{index}",
        table=table,
        check="out_of_range",
        column=check.column,
        description=check.description,
        affected_row_count=count,
        details={"predicate": predicate},
    )


def _run_duplicate_check(
    engine: Engine, schema: str, table: str, check: DuplicateCheck, index: int
) -> ProfileFinding | None:
    columns = _get_columns(engine, schema, table)
    partition = ", ".join(quote_ident(c) for c in columns)
    sql = f"""
        SELECT COUNT(*) FROM (
            SELECT ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY (SELECT 1)) AS rn
            FROM {quote_ident(schema)}.{quote_ident(table)}
        ) ranked WHERE rn > 1
    """
    with engine.connect() as conn:
        count = conn.execute(text(sql)).scalar_one()
    if count == 0:
        return None
    return ProfileFinding(
        id=f"{table}-duplicates-{index}",
        table=table,
        check="exact_duplicates",
        column=None,
        description=check.description,
        affected_row_count=count,
        details={"columns": columns},
    )


def _run_categorical_noise_check(
    engine: Engine, schema: str, table: str, check: CategoricalNoiseCheck, index: int
) -> ProfileFinding | None:
    col = quote_ident(check.column)
    qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
    with engine.connect() as conn:
        raw_distinct, norm_distinct = conn.execute(
            text(
                f"SELECT COUNT(DISTINCT {col}), COUNT(DISTINCT TRIM(LOWER({col}))) "
                f"FROM {qualified} WHERE {col} IS NOT NULL"
            )
        ).one()
        if raw_distinct == norm_distinct:
            return None
        affected = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {qualified} WHERE {col} IS DISTINCT FROM TRIM(LOWER({col}))"
            )
        ).scalar_one()
    return ProfileFinding(
        id=f"{table}-noise-{check.column}-{index}",
        table=table,
        check="categorical_noise",
        column=check.column,
        description=check.description,
        affected_row_count=affected,
        details={"raw_distinct": raw_distinct, "normalized_distinct": norm_distinct},
    )


_RUNNERS: dict[type, Callable[[Engine, str, str, Any, int], ProfileFinding | None]] = {
    OrderingCheck: _run_ordering_check,
    RangeCheck: _run_range_check,
    DuplicateCheck: _run_duplicate_check,
    CategoricalNoiseCheck: _run_categorical_noise_check,
}


def run_profile(engine: Engine, schema: str, table: str) -> list[ProfileFinding]:
    if table not in TABLE_CHECKS:
        raise ValueError(
            f"no profiling checks defined for table '{table}'. Known tables: {sorted(TABLE_CHECKS)}"
        )
    findings = []
    for index, check in enumerate(TABLE_CHECKS[table]):
        runner = _RUNNERS[type(check)]
        finding = runner(engine, schema, table, check, index)
        if finding is not None:
            findings.append(finding)
    return findings
