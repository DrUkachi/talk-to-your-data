"""strategy + params -> SQL, deterministically. The LLM (llm.py) never emits SQL
directly -- it picks one of these strategies and parameters, referencing a
finding_id. render_fix resolves the actual table/column from the matching
ProfileFinding, not from anything the LLM supplied, so there's no path for the LLM
to point a write at an arbitrary identifier.
"""

from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.engine import Engine

from .profiling import quote_ident
from .state import AppliedFix, FixProposal, FixStrategy, ProfileFinding


def _require_column(finding: ProfileFinding) -> str:
    if not finding.column:
        raise ValueError(f"finding '{finding.id}' has no column; this strategy requires one")
    return finding.column


def render_fix(
    schema: str, finding: ProfileFinding, strategy: FixStrategy, params: dict[str, Any]
) -> TextClause:
    table = f"{quote_ident(schema)}.{quote_ident(finding.table)}"

    if strategy == FixStrategy.TRIM_WHITESPACE:
        col = quote_ident(_require_column(finding))
        return text(
            f"UPDATE {table} SET {col} = TRIM({col}) WHERE {col} IS DISTINCT FROM TRIM({col})"
        )

    if strategy == FixStrategy.NORMALIZE_CASE:
        col = quote_ident(_require_column(finding))
        return text(
            f"UPDATE {table} SET {col} = LOWER(TRIM({col})) "
            f"WHERE {col} IS DISTINCT FROM LOWER(TRIM({col}))"
        )

    if strategy == FixStrategy.DROP_EXACT_DUPLICATES:
        columns = finding.details.get("columns")
        if not columns:
            raise ValueError(f"finding '{finding.id}' has no details['columns'] for dedup")
        match_clause = " AND ".join(
            f"a.{quote_ident(c)} IS NOT DISTINCT FROM b.{quote_ident(c)}" for c in columns
        )
        return text(
            f"DELETE FROM {table} a USING {table} b WHERE a.ctid < b.ctid AND {match_clause}"
        )

    if strategy == FixStrategy.NULL_OUT_IMPOSSIBLE_VALUE:
        predicate = finding.details.get("predicate")
        if not predicate:
            raise ValueError(f"finding '{finding.id}' has no details['predicate'] to null out")
        null_column = params.get("null_column") or finding.column
        if not null_column:
            raise ValueError(
                "null_out_impossible_value needs params['null_column'] or finding.column"
            )
        col = quote_ident(null_column)
        return text(f"UPDATE {table} SET {col} = NULL WHERE {predicate}")

    if strategy == FixStrategy.CLIP_OUTLIER:
        col = quote_ident(_require_column(finding))
        if "lower" not in params or "upper" not in params:
            raise ValueError("clip_outlier requires params['lower'] and params['upper']")
        return text(
            f"UPDATE {table} SET {col} = LEAST(GREATEST({col}, :lower), :upper) "
            f"WHERE {col} < :lower OR {col} > :upper"
        ).bindparams(lower=params["lower"], upper=params["upper"])

    raise ValueError(f"unknown strategy '{strategy}'")


def apply_fixes(
    engine: Engine,
    schema: str,
    findings: list[ProfileFinding],
    proposals: list[FixProposal],
    approved_finding_ids: list[str],
) -> list[AppliedFix]:
    """All proposals apply in a single transaction: if any fix's SQL fails, none of
    them take effect, rather than leaving the table partially fixed."""
    findings_by_id = {f.id: f for f in findings}
    approved = set(approved_finding_ids)
    applied: list[AppliedFix] = []

    with engine.begin() as conn:
        for proposal in proposals:
            if proposal.finding_id not in approved:
                continue
            finding = findings_by_id.get(proposal.finding_id)
            if finding is None:
                raise ValueError(
                    f"approved finding_id '{proposal.finding_id}' not in current findings"
                )
            stmt = render_fix(schema, finding, proposal.strategy, proposal.params)
            result = conn.execute(stmt)
            applied.append(
                AppliedFix(
                    finding_id=proposal.finding_id,
                    strategy=proposal.strategy,
                    sql=str(stmt),
                    rows_affected=result.rowcount,
                )
            )
    return applied
