"""Scoring functions for the Phase 6d eval suite's three dimensions. Each
returns (passed, detail) -- a bare bool doesn't say why a case failed when the
suite reports a regression.
"""

import re
from typing import Any

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
