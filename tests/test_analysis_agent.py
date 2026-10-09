import pytest

from talk_to_your_data.agents.eda.analysis_agent import compute_breakdown_stats, compute_trend_stats


def test_trend_stats_computes_pct_change_and_direction():
    rows = [
        {"period": "2017-01-01T00:00:00", "revenue": 100.0},
        {"period": "2017-02-01T00:00:00", "revenue": 150.0},
        {"period": "2017-03-01T00:00:00", "revenue": 200.0},
    ]
    stats = compute_trend_stats(rows, None, None)
    assert stats["periods_used"] == 3
    assert stats["first_value"] == 100.0
    assert stats["last_value"] == 200.0
    assert stats["pct_change"] == 100.0
    assert stats["direction"] == "up"


def test_trend_stats_filters_to_requested_range():
    rows = [
        {"period": "2016-12-01T00:00:00", "revenue": 10.0},
        {"period": "2017-01-01T00:00:00", "revenue": 100.0},
        {"period": "2017-12-01T00:00:00", "revenue": 300.0},
        {"period": "2018-01-01T00:00:00", "revenue": 999.0},
    ]
    stats = compute_trend_stats(rows, "2017-01-01", "2017-12-31")
    assert stats["periods_used"] == 2
    assert stats["first_value"] == 100.0
    assert stats["last_value"] == 300.0


def test_trend_stats_handles_empty_range():
    rows = [{"period": "2017-01-01T00:00:00", "revenue": 100.0}]
    stats = compute_trend_stats(rows, "2020-01-01", "2020-12-31")
    assert "error" in stats


def test_trend_stats_direction_flat_for_small_change():
    rows = [
        {"period": "2017-01-01T00:00:00", "revenue": 100.0},
        {"period": "2017-02-01T00:00:00", "revenue": 100.5},
    ]
    stats = compute_trend_stats(rows, None, None)
    assert stats["direction"] == "flat"


def test_trend_stats_direction_down():
    rows = [
        {"period": "2017-01-01T00:00:00", "revenue": 200.0},
        {"period": "2017-02-01T00:00:00", "revenue": 100.0},
    ]
    stats = compute_trend_stats(rows, None, None)
    assert stats["direction"] == "down"


def test_breakdown_stats_ranks_top_and_bottom():
    rows = [
        {"category": "a", "revenue": 500.0},
        {"category": "b", "revenue": 100.0},
        {"category": "c", "revenue": 300.0},
        {"category": "d", "revenue": 50.0},
    ]
    stats = compute_breakdown_stats(rows)
    assert stats["metric_column"] == "revenue"
    assert [r["category"] for r in stats["top"]] == ["a", "c", "b"]
    assert [r["category"] for r in stats["bottom"]] == ["c", "b", "d"]
    assert stats["top"][0]["share_pct"] == pytest.approx(500 / 950 * 100)


def test_breakdown_stats_handles_empty_rows():
    assert compute_breakdown_stats([]) == {"error": "no rows"}


def test_trend_stats_handles_a_differently_named_time_column_and_string_numbers():
    rows = [
        {"order_month": "2017-01-01", "orders": "100"},
        {"order_month": "2017-02-01", "orders": "150"},
    ]
    stats = compute_trend_stats(rows, None, None)
    assert stats["first_period"] == "2017-01-01" and stats["pct_change"] == 50.0
    assert stats["direction"] == "up"


def test_trend_stats_reports_an_error_instead_of_raising_without_a_time_column():
    stats = compute_trend_stats([{"status": "x", "n": 1}, {"status": "y", "n": 2}], None, None)
    assert "error" in stats


def test_breakdown_stats_handles_numeric_strings_and_missing_numeric_column():
    rows = [{"category": "a", "avg_score": "4.5"}, {"category": "b", "avg_score": "3.0"}]
    stats = compute_breakdown_stats(rows)
    assert stats["metric_column"] == "avg_score"
    assert stats["top"][0]["category"] == "a"
    assert "error" in compute_breakdown_stats([{"category": "a", "label": "x"}])
