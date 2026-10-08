"""SQL validation and output masking for the only two places agents can read
query results: MCP's run_sql tool (free-form SQL) and query_metric
(compiler-generated, but its output still passes through the same masking and
row-limit policy).

Validation walks the full AST for write/DDL nodes anywhere in the tree -- not
just the top-level statement type -- because Postgres allows data-modifying
CTEs (`WITH x AS (DELETE ... RETURNING *) SELECT ...`) whose outermost node is
a harmless-looking SELECT (confirmed by parsing one, not assumed). The
readonly DB role would also reject the write at execution time (defense in
depth), but this should catch it before ever reaching Postgres.

Masking is name-based on OUTPUT columns and known-bypassable: `SELECT
geolocation_lat AS x` or `SELECT AVG(geolocation_lat)` both evade it, since
neither produces an output column named `geolocation_lat`. This is a real,
documented limitation (see docs/architecture.md's failure-modes table), not
an oversight -- real column-provenance tracking through arbitrary SQL, or
masked Postgres views, would close it but is out of scope for this project.
"""

from typing import Any

import sqlglot
from sqlglot import exp

ALLOWED_SCHEMAS = {"raw"}
DISALLOWED_SCHEMAS = {"pg_catalog", "information_schema"}
MAX_ROW_LIMIT = 1000

_WRITE_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
)

DISALLOWED_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "dblink",
    "dblink_connect",
    "lo_import",
    "lo_export",
    "pg_terminate_backend",
    "pg_cancel_backend",
}


class SqlGuardError(ValueError):
    pass


def validate_sql(query: str) -> exp.Expression:
    """Parses `query` and raises SqlGuardError unless it's exactly one
    read-only SELECT/WITH/UNION statement against an allowed schema."""
    try:
        statements = [s for s in sqlglot.parse(query, dialect="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlGuardError(f"could not parse SQL: {e}") from e

    if len(statements) != 1:
        raise SqlGuardError(f"exactly one SQL statement is required, found {len(statements)}")
    stmt = statements[0]

    if not isinstance(stmt, exp.Select | exp.With | exp.Union):
        raise SqlGuardError(
            f"only SELECT/WITH/UNION statements are allowed, got {type(stmt).__name__}"
        )

    write_nodes = list(stmt.find_all(*_WRITE_NODE_TYPES))
    if write_nodes:
        raise SqlGuardError(
            f"write/DDL statements are not allowed, found {type(write_nodes[0]).__name__} "
            "(e.g. inside a data-modifying CTE)"
        )

    cte_aliases = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        if table.name.lower() in cte_aliases:
            continue  # a CTE reference, not a real table -- no schema to check
        schema = (table.db or "").lower()
        if schema in DISALLOWED_SCHEMAS or (schema and schema not in ALLOWED_SCHEMAS):
            raise SqlGuardError(f"access to schema '{schema}' is not allowed")
        if not schema:
            raise SqlGuardError(
                f"table '{table.name}' must be schema-qualified (e.g. raw.{table.name})"
            )

    for func in stmt.find_all(exp.Func):
        if (func.name or "").lower() in DISALLOWED_FUNCTIONS:
            raise SqlGuardError(f"function '{func.name}' is not allowed")

    return stmt


def clamp_limit(requested: int) -> int:
    if requested <= 0:
        raise SqlGuardError(f"limit must be positive, got {requested}")
    return min(requested, MAX_ROW_LIMIT)


# column name -> masking function. Matched against OUTPUT column names, so an
# aliased or aggregated column bypasses this -- see module docstring.
MASKING_RULES: dict[str, Any] = {
    "geolocation_lat": lambda v: round(v, 1) if v is not None else None,
    "geolocation_lng": lambda v: round(v, 1) if v is not None else None,
}


def _is_zip_column(name: str) -> bool:
    return name.lower().endswith("zip_code_prefix")


def mask_row(row: dict[str, Any]) -> dict[str, Any]:
    masked = dict(row)
    for key, value in masked.items():
        if key in MASKING_RULES:
            masked[key] = MASKING_RULES[key](value)
        elif _is_zip_column(key) and value is not None:
            masked[key] = str(value)[:2]
    return masked
