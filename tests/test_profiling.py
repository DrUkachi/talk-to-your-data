import pytest

from talk_to_your_data.agents.cleaning import profiling
from talk_to_your_data.db import app_engine

pytestmark = pytest.mark.integration


def test_profile_detects_all_four_injected_defects(dirty_fixture_table):
    findings = profiling.run_profile(app_engine(), "clean", dirty_fixture_table)
    checks = {f.check for f in findings}
    assert checks == {"logical_ordering", "out_of_range", "exact_duplicates", "categorical_noise"}


def test_ordering_finding_has_expected_row_count(dirty_fixture_table):
    findings = profiling.run_profile(app_engine(), "clean", dirty_fixture_table)
    ordering = next(f for f in findings if f.check == "logical_ordering")
    assert ordering.affected_row_count == 1
    assert ordering.column == "approved_at"


def test_range_finding_has_expected_row_count(dirty_fixture_table):
    findings = profiling.run_profile(app_engine(), "clean", dirty_fixture_table)
    out_of_range = next(f for f in findings if f.check == "out_of_range")
    assert out_of_range.affected_row_count == 1
    assert out_of_range.column == "amount"


def test_duplicate_finding_counts_only_the_extra_copy(dirty_fixture_table):
    findings = profiling.run_profile(app_engine(), "clean", dirty_fixture_table)
    dup = next(f for f in findings if f.check == "exact_duplicates")
    assert dup.affected_row_count == 1
    assert set(dup.details["columns"]) == {"id", "status", "purchased_at", "approved_at", "amount"}


def test_categorical_noise_finding_flags_only_the_noisy_row(dirty_fixture_table):
    findings = profiling.run_profile(app_engine(), "clean", dirty_fixture_table)
    noise = next(f for f in findings if f.check == "categorical_noise")
    assert noise.affected_row_count == 1
    assert noise.details["raw_distinct"] == 3
    assert noise.details["normalized_distinct"] == 2


def test_profile_on_a_clean_table_finds_nothing(dirty_fixture_table):
    """Confirms checks don't false-positive on data that's already fine --
    run it twice: a clean table should profile to zero findings."""
    from sqlalchemy import text

    engine = app_engine()
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM "clean"."cleaning_fixture" WHERE id IN (2, 3, 4, 5)'))
    findings = profiling.run_profile(engine, "clean", dirty_fixture_table)
    assert findings == []


def test_profile_raises_for_unknown_table():
    with pytest.raises(ValueError, match="no profiling checks defined"):
        profiling.run_profile(app_engine(), "clean", "not_a_real_table")
