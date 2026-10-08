import pytest

from talk_to_your_data.agents.cleaning.fix_strategies import apply_fixes, render_fix
from talk_to_your_data.agents.cleaning.state import FixProposal, FixStrategy, ProfileFinding
from talk_to_your_data.db import app_engine


def _finding(**overrides) -> ProfileFinding:
    base = dict(
        id="t-check-0",
        table="t",
        check="out_of_range",
        column="amount",
        description="d",
        affected_row_count=1,
        details={},
    )
    base.update(overrides)
    return ProfileFinding(**base)


def test_trim_whitespace_sql():
    stmt = render_fix("clean", _finding(column="notes"), FixStrategy.TRIM_WHITESPACE, {})
    assert str(stmt) == (
        'UPDATE "clean"."t" SET "notes" = TRIM("notes") '
        'WHERE "notes" IS DISTINCT FROM TRIM("notes")'
    )


def test_normalize_case_sql():
    stmt = render_fix("clean", _finding(column="status"), FixStrategy.NORMALIZE_CASE, {})
    assert str(stmt) == (
        'UPDATE "clean"."t" SET "status" = LOWER(TRIM("status")) '
        'WHERE "status" IS DISTINCT FROM LOWER(TRIM("status"))'
    )


def test_drop_exact_duplicates_sql():
    finding = _finding(column=None, details={"columns": ["id", "status"]})
    stmt = render_fix("clean", finding, FixStrategy.DROP_EXACT_DUPLICATES, {})
    rendered = str(stmt)
    assert rendered == (
        'DELETE FROM "clean"."t" a USING "clean"."t" b WHERE a.ctid < b.ctid '
        'AND a."id" IS NOT DISTINCT FROM b."id" AND a."status" IS NOT DISTINCT FROM b."status"'
    )


def test_drop_exact_duplicates_requires_columns():
    finding = _finding(column=None, details={})
    with pytest.raises(ValueError, match="columns"):
        render_fix("clean", finding, FixStrategy.DROP_EXACT_DUPLICATES, {})


def test_null_out_impossible_value_sql():
    finding = _finding(
        column="approved_at", details={"predicate": '"approved_at" < "purchased_at"'}
    )
    stmt = render_fix(
        "clean", finding, FixStrategy.NULL_OUT_IMPOSSIBLE_VALUE, {"null_column": "approved_at"}
    )
    assert str(stmt) == (
        'UPDATE "clean"."t" SET "approved_at" = NULL WHERE "approved_at" < "purchased_at"'
    )


def test_null_out_impossible_value_requires_predicate():
    finding = _finding(column="approved_at", details={})
    with pytest.raises(ValueError, match="predicate"):
        render_fix(
            "clean", finding, FixStrategy.NULL_OUT_IMPOSSIBLE_VALUE, {"null_column": "approved_at"}
        )


def test_clip_outlier_sql():
    stmt = render_fix(
        "clean", _finding(column="amount"), FixStrategy.CLIP_OUTLIER, {"lower": 0, "upper": 100}
    )
    assert 'LEAST(GREATEST("amount", :lower), :upper)' in str(stmt)


def test_clip_outlier_requires_bounds():
    with pytest.raises(ValueError, match="lower"):
        render_fix("clean", _finding(column="amount"), FixStrategy.CLIP_OUTLIER, {})


def test_strategy_requiring_column_raises_without_one():
    finding = _finding(column=None)
    with pytest.raises(ValueError, match="has no column"):
        render_fix("clean", finding, FixStrategy.TRIM_WHITESPACE, {})


@pytest.mark.integration
def test_apply_fixes_is_all_or_nothing_on_a_bad_proposal(dirty_fixture_table):
    """If one proposal in the batch fails, none of them should take effect --
    a partially-cleaned table would be worse than an untouched one."""
    from talk_to_your_data.agents.cleaning import profiling as prof

    engine = app_engine()
    findings = prof.run_profile(engine, "clean", dirty_fixture_table)
    good = next(f for f in findings if f.check == "out_of_range")

    proposals = [
        FixProposal(
            finding_id=good.id,
            strategy=FixStrategy.CLIP_OUTLIER,
            params={"lower": 0, "upper": 999999},
            rationale="x",
            risk="low",
        ),
        FixProposal(
            finding_id="not-a-real-finding-id",
            strategy=FixStrategy.TRIM_WHITESPACE,
            params={},
            rationale="x",
            risk="low",
        ),
    ]

    with pytest.raises(ValueError, match="not in current findings"):
        apply_fixes(
            engine,
            "clean",
            findings,
            proposals,
            approved_finding_ids=[good.id, "not-a-real-finding-id"],
        )

    refreshed = prof.run_profile(engine, "clean", dirty_fixture_table)
    assert any(f.check == "out_of_range" for f in refreshed), (
        "the good fix should have rolled back too"
    )
