"""State and data contracts for the supervisor graph.

Finding is the literal pydantic-ification of the `findings-writeup` skill's
template (Phase 1): question, SQL, result summary, chart (None until Phase 5),
caveats, confidence, interpretation. That shape was decided before this phase
even started -- getting it right then is what makes it just "give this shape a
table" now instead of a redesign.
"""

from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class AnalysisLens(StrEnum):
    TIME_SERIES_TREND = "time_series_trend"
    CATEGORY_BREAKDOWN = "category_breakdown"
    NONE = "none"


class SqlResult(BaseModel):
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]


class AnalysisResult(BaseModel):
    lens: AnalysisLens
    stats: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class Finding(BaseModel):
    question: str
    sql: str
    result_summary: str
    chart_ref: str | None = None
    chart_kind: str | None = None  # line/bar/pie/scatter; not persisted, used by the eval
    caveats: str
    confidence: Literal["low", "medium", "high"]
    interpretation: str


class AgentState(TypedDict, total=False):
    thread_id: str
    question: str
    display_question: str | None  # what the user actually asked (chart title, stored finding)
    sql_result: dict[str, Any] | None
    analysis_result: dict[str, Any] | None
    finding: dict[str, Any] | None
    next: Literal["sql_agent", "analysis_agent", "narrative_agent", "FINISH"]
    turn: int
    max_turns: int
    status: Literal["routing", "done", "failed"]
