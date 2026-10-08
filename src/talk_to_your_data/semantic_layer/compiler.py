"""(metric, dimensions, filters, time_grain) -> SQL.

Dimension/measure/model SQL fragments come from the registry (code-authored,
reviewed, never user input) and are inserted via literal_column/text. Filter
*values* are always bound parameters, never string-concatenated -- that's what
keeps this injection-safe even before Phase 6's guardrails exist. Metric/dimension
*names* are dict lookups that raise on anything unrecognized.
"""

from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    bindparam,
    func,
    literal_column,
    select,
    text,
)
from sqlalchemy.dialects import postgresql

from .registry import METRICS, MODELS_REGISTRY

ALLOWED_GRAINS = ("day", "week", "month", "quarter", "year")


class CompilerError(ValueError):
    pass


def _agg_expression(agg: str, measure_sql: str) -> ColumnElement[Any]:
    if measure_sql == "*":
        if agg != "count":
            raise CompilerError("measure '*' only valid with agg=count")
        return func.count(literal_column("*"))
    col: ColumnElement[Any] = literal_column(measure_sql)
    if agg == "sum":
        return func.sum(col)
    if agg == "avg":
        return func.avg(col)
    if agg == "count":
        return func.count(col)
    if agg == "count_distinct":
        return func.count(col.distinct())
    raise CompilerError(f"unknown aggregation '{agg}'")


def compile_metric(
    metric_name: str,
    dimensions: list[str] | None = None,
    filters: dict[str, list[str]] | None = None,
    time_grain: str | None = None,
    limit: int = 1000,
) -> tuple[Select, str]:
    """Returns (executable SQLAlchemy Select, SQL text with bind values inlined for display)."""
    dimensions = dimensions or []
    filters = filters or {}

    if metric_name not in METRICS:
        raise CompilerError(f"unknown metric '{metric_name}'. Call list_metrics() for valid names.")
    metric = METRICS[metric_name]
    model = MODELS_REGISTRY[metric.model]

    if time_grain is not None and time_grain not in ALLOWED_GRAINS:
        raise CompilerError(f"unknown time_grain '{time_grain}', must be one of {ALLOWED_GRAINS}")

    select_cols: list[ColumnElement[Any]] = []
    group_by_exprs: list[ColumnElement[Any]] = []

    if time_grain is not None:
        time_expr = func.date_trunc(time_grain, literal_column(model.time_dimension))
        select_cols.append(time_expr.label("period"))
        group_by_exprs.append(time_expr)

    for dim in dimensions:
        if dim not in model.dimensions:
            raise CompilerError(f"dimension '{dim}' is not available on model '{model.name}'")
        dim_expr: ColumnElement[Any] = literal_column(model.dimensions[dim])
        select_cols.append(dim_expr.label(dim))
        group_by_exprs.append(dim_expr)

    measure_sql = "*" if metric.measure == "*" else model.measures[metric.measure]
    select_cols.append(_agg_expression(metric.agg, measure_sql).label(metric_name))

    stmt = select(*select_cols).select_from(text(model.from_sql))

    # mix of TextClause (base_filter) and ColumnElement (dimension filters)
    where_clauses: list[Any] = []
    if model.base_filter:
        where_clauses.append(text(model.base_filter))

    combined_filters = {**metric.default_filters, **filters}
    bind_params: dict[str, list[str]] = {}
    for i, (dim, values) in enumerate(combined_filters.items()):
        if dim not in model.dimensions:
            raise CompilerError(
                f"filter dimension '{dim}' is not available on model '{model.name}'"
            )
        param_name = f"filter_{i}"
        param = bindparam(param_name, expanding=True, type_=String())
        where_clauses.append(literal_column(model.dimensions[dim]).in_(param))
        bind_params[param_name] = list(values)

    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))

    if group_by_exprs:
        stmt = stmt.group_by(*group_by_exprs).order_by(*group_by_exprs)
        stmt = stmt.limit(limit)

    if bind_params:
        stmt = stmt.params(**bind_params)

    sql_text = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    return stmt, sql_text
