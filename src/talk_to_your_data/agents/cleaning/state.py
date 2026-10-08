"""State and data contracts for the cleaning graph.

Kept as plain dicts/lists in CleaningState (not pydantic objects) since LangGraph's
Postgres checkpointer serializes state -- pydantic models round-trip through
model_dump()/model_validate() at the node boundary instead.

Security note: FixProposal deliberately has no `table`/`column` field. The LLM only
picks a strategy + params for a `finding_id` it was given; apply.py resolves the
actual table/column from the matching ProfileFinding, never from LLM free text. That's
what makes it safe to let the LLM choose without validating arbitrary identifiers.
"""

from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class FixStrategy(StrEnum):
    TRIM_WHITESPACE = "trim_whitespace"
    NORMALIZE_CASE = "normalize_case"
    DROP_EXACT_DUPLICATES = "drop_exact_duplicates"
    NULL_OUT_IMPOSSIBLE_VALUE = "null_out_impossible_value"
    CLIP_OUTLIER = "clip_outlier"


class ProfileFinding(BaseModel):
    id: str
    table: str
    check: Literal["logical_ordering", "out_of_range", "exact_duplicates", "categorical_noise"]
    column: str | None = None
    description: str
    affected_row_count: int
    details: dict[str, Any] = Field(default_factory=dict)


class FixProposal(BaseModel):
    finding_id: str
    strategy: FixStrategy
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    risk: Literal["low", "medium", "high"]


class AppliedFix(BaseModel):
    finding_id: str
    strategy: FixStrategy
    sql: str
    rows_affected: int


class ValidationResult(BaseModel):
    passed: bool
    remaining_findings: list[ProfileFinding] = Field(default_factory=list)


class CleaningState(TypedDict, total=False):
    table: str
    findings: list[dict]
    proposals: list[dict]
    approved_finding_ids: list[str]
    applied_fixes: list[dict]
    validation: dict | None
    attempt: int
    max_attempts: int
    status: Literal["profiling", "awaiting_approval", "applying", "validating", "done", "failed"]
