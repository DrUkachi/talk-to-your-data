"""Scoring functions for the Phase 6d eval suite's three dimensions. Each
returns (passed, detail) -- a bare bool doesn't say why a case failed when the
suite reports a regression.
"""

import re
from pathlib import Path
from typing import Any

from talk_to_your_data.agents.eda.charting import classify_columns, to_float
from talk_to_your_data.agents.eda.state import Finding

_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _extract_numbers(text: str) -> list[float]:
    numbers = []
    for match in _NUMBER_RE.findall(text):
        cleaned = match.replace(",", "")
        if cleaned in ("", "-", "."):
            continue
        try:
            numbers.append(float(cleaned))
        except ValueError:
            continue
    return numbers


def _flatten_row_values(rows: list[dict[str, Any]]) -> list[float]:
    values = []
    for row in rows:
        for v in row.values():
            if isinstance(v, int | float) and not isinstance(v, bool):
                values.append(float(v))
            elif isinstance(v, str):
                # The MCP server serializes Postgres numerics (Decimal) as strings.
                try:
                    values.append(float(v))
                except ValueError:
                    continue
    return values


def _within_tolerance(value: float, expected: float, tolerance: float) -> bool:
    if expected == 0:
        return abs(value) < 1e-6
    return abs(value - expected) / abs(expected) <= tolerance


def score_execution_accuracy(
    expected_value: float,
    tolerance: float,
    result_rows: list[dict[str, Any]],
    finding: Finding | None = None,
) -> tuple[bool, str]:
    """Checks the raw returned rows AND the finding's narrative -- some
    questions (e.g. "average order value") have no dedicated metric, so
    sql_agent correctly derives the answer via arithmetic across two separately
    -returned aggregates (revenue, order_count) and states only the *derived*
    number in prose, never as a literal row value. Confirmed happening for
    real against the live model, not hypothetical -- an earlier version of this
    scorer only checked result_rows and flagged several correct answers as
    failures because of exactly this.
    """
    candidates = _flatten_row_values(result_rows)
    if finding is not None:
        candidates = candidates + _extract_numbers(
            f"{finding.result_summary} {finding.interpretation}"
        )
    if any(_within_tolerance(v, expected_value, tolerance) for v in candidates):
        return True, f"found a value within {tolerance:.1%} of expected {expected_value}"
    return False, (
        f"expected {expected_value} (tolerance {tolerance:.1%}), "
        f"none of the returned values or narrative numbers {candidates} matched"
    )


def score_faithfulness(finding: Finding, result_rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """Best-effort, not a proof: checks every number mentioned in the narrative
    traces back to a stored row value, or a simple derivation (sum, % share of
    the total, pairwise sum/difference/ratio of stored values) -- the same honesty
    standard as Phase 6a's masking limitation.
    Verifying arbitrary derived-number provenance in free text is a hard, open
    problem; this catches fabricated/unrelated figures, not every possible gap.
    """
    narrative = f"{finding.result_summary} {finding.interpretation}"
    # Numbers the user's own question supplied ("2017", "5-star") are restated, not data.
    from_question = set(_extract_numbers(finding.question))
    mentioned = [n for n in _extract_numbers(narrative) if n not in from_question]
    if not mentioned:
        return True, "no numbers in the narrative to check"

    row_values = _flatten_row_values(result_rows)
    total = sum(row_values) if row_values else 0.0
    derived = set(row_values)
    if total:
        derived.add(total)
        for v in row_values:
            if v:
                derived.add(100.0 * v / total)  # percentage share of the total
    # Restatements of a single value in other units: fraction -> percent, and
    # thousands/millions shorthand ("549.4K", "13.2M").
    for v in row_values:
        derived.update({v * 100.0, v / 1_000.0, v / 1_000_000.0})
    # "Everything except one category": the total minus one stored value.
    for v in row_values:
        derived.add(total - v)
    # Simple derivations across two stored values (an average from sum / count, a
    # "non-delivered" count from total - delivered, a combined total, a percentage).
    if len(row_values) <= 8:
        for a in row_values:
            for b in row_values:
                if a is not b:
                    derived.update({a + b, a - b, abs(a - b)})
                    if b:
                        derived.update({a / b, 100.0 * a / b})

    unexplained = [
        n
        for n in mentioned
        if n not in (0.0, 1.0)  # too common in non-data phrasing ("a single category") to be useful
        and not any(_within_tolerance(n, d, 0.01) for d in derived if d)
    ]
    if unexplained:
        return False, f"narrative mentions {unexplained}, not traceable to the stored data"
    return True, "every narrative number traces back to the stored data (or a simple derivation)"


def score_refusal_correctness(finding: Finding) -> tuple[bool, str]:
    is_refusal = finding.interpretation == "Refused: out of scope for this system."
    if is_refusal:
        return True, "correctly refused"
    return False, f"should have refused but answered: {finding.result_summary!r}"


def score_chart(finding: Finding, expected: str) -> tuple[bool, str]:
    text = f"{finding.result_summary} {finding.interpretation} {finding.caveats}"
    mentions_chart = bool(re.search(r"chart|plot|graph", text, re.I))
    has_file = bool(finding.chart_ref) and Path(finding.chart_ref or "").exists()
    if expected in ("line", "bar", "pie"):
        if has_file and finding.chart_kind == expected:
            return True, f"produced a {expected} chart"
        return (
            False,
            f"expected a {expected} chart, got kind={finding.chart_kind!r} file={has_file}",
        )
    if expected == "none":
        if finding.chart_ref is None:
            return True, "correctly made no chart for a single value"
        return False, f"made an unrequested {finding.chart_kind} chart for a single value"
    if expected == "requested":
        if has_file:
            return True, f"produced a {finding.chart_kind} chart for the explicit request"
        if mentions_chart:
            return True, "no chart possible, and the answer says so"
        return False, "chart requested, but no chart was made and nothing explained why"
    raise ValueError(f"unknown expected chart outcome {expected!r}")


def score_followup(
    expected_value: float,
    tolerance: float,
    mode: str,
    result_rows: list[dict[str, Any]],
    finding: Finding,
) -> tuple[bool, str]:
    if mode == "value":
        return score_execution_accuracy(expected_value, tolerance, result_rows, finding)
    if mode == "rows_sum":
        if not result_rows:
            return False, "follow-up returned no rows to sum"
        kinds = classify_columns(list(result_rows[0]), result_rows)
        numeric = next((c for c, k in kinds.items() if k == "numeric"), None)
        if numeric is None:
            return False, f"no numeric column in follow-up rows {list(result_rows[0])}"
        total = sum(to_float(r[numeric]) or 0.0 for r in result_rows)
        if _within_tolerance(total, expected_value, tolerance):
            return True, f"rows sum to {total}, expected {expected_value}"
        return False, f"rows sum to {total}, expected {expected_value}"
    raise ValueError(f"unknown follow-up scoring mode {mode!r}")
