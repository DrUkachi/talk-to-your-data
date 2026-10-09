"""Deterministic chart selection and rendering for a Finding's result rows.

Whether to chart, and which kind, is decided here in code from the SHAPE of the
result (never by the LLM), so a chart can't disagree with the stored rows and every
branch is unit-testable without a model:

- one temporal column + numeric column(s), >=2 rows   -> line
- one category column + a numeric column, >=2 rows    -> bar (top N if many)
- a single row / single value                          -> no chart

An explicit request in the question ("plot", "chart", "graph", "visualize", or a
kind such as "pie") adds two things: it may pick a compatible kind (pie/bar/line),
and it enables a looser scatter fallback for two numeric columns. If the user asked
for a chart and none can be made, `ChartResult.note` says why -- the narrative agent
is required to relay it (see narrative_agent.write_finding), so the user is never
left wondering where the chart went.
"""

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from talk_to_your_data.db import REPO_ROOT  # noqa: E402

from .state import SqlResult  # noqa: E402

MAX_BARS = 15
MAX_PIE_SLICES = 8
MAX_SERIES = 4

_EXPLICIT_RE = re.compile(
    r"\b(plot|chart|graph|visuali[sz]e|visuali[sz]ation|draw|pie|histogram)\b", re.I
)
_KIND_HINTS = {"pie": "pie", "bar": "bar", "line": "line", "scatter": "scatter"}
_TEMPORAL_NAME_RE = re.compile(r"(month|date|day|week|year|quarter|period|time)", re.I)
_DATE_VALUE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T].*)?$")


@dataclass
class ChartResult:
    path: str | None
    kind: str | None = None
    # Set only when the user explicitly asked for a chart and none could be made.
    note: str | None = None


def wants_chart(question: str) -> bool:
    return bool(_EXPLICIT_RE.search(question))


def _kind_hint(question: str) -> str | None:
    if re.search(r"\bpie\b", question, re.I):
        return "pie"
    for word, kind in _KIND_HINTS.items():
        if re.search(rf"\b{word}(\s+(chart|graph|plot))?\b", question, re.I):
            return kind
    return None


def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)  # MCP serializes Postgres numerics as strings
        except ValueError:
            return None
    return None


def classify_columns(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for col in columns:
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if not values:
            kinds[col] = "other"
            continue
        numeric = all(to_float(v) is not None for v in values)
        datelike = all(
            isinstance(v, datetime) or (isinstance(v, str) and _DATE_VALUE_RE.match(v))
            for v in values
        )
        if datelike or (_TEMPORAL_NAME_RE.search(col) and (not numeric or _is_year_like(values))):
            kinds[col] = "temporal"
        elif numeric:
            kinds[col] = "numeric"
        elif all(isinstance(v, str) for v in values):
            kinds[col] = "category"
        else:
            kinds[col] = "other"
    return kinds


def _is_year_like(values: list[Any]) -> bool:
    return all(1900 <= (to_float(v) or 0) <= 2200 for v in values)


def _short_label(value: Any) -> str:
    text = str(value)[:10]
    return text[:7] if re.fullmatch(r"\d{4}-\d{2}-01", text) else text  # month starts -> YYYY-MM


def _pretty(col: str) -> str:
    return col.replace("_", " ")


def _plan(question: str, result: SqlResult) -> tuple[str, dict[str, Any]] | tuple[None, str]:
    """Returns (kind, spec) when a chart is possible, else (None, reason)."""
    rows, columns = result.rows, result.columns
    if len(rows) < 2:
        return None, "the result is a single value, so there is nothing to plot"

    kinds = classify_columns(columns, rows)
    temporal = [c for c in columns if kinds[c] == "temporal"]
    category = [c for c in columns if kinds[c] == "category"]
    numeric = [c for c in columns if kinds[c] == "numeric"]
    hint = _kind_hint(question)
    explicit = wants_chart(question)

    if temporal and numeric:
        kind = "bar" if hint == "bar" else "line"
        return kind, {"x": temporal[0], "ys": numeric[:MAX_SERIES]}
    if category and numeric:
        kind = hint if hint in ("pie", "bar") else "bar"
        return kind, {"x": category[0], "ys": numeric[:1]}
    if explicit and len(numeric) >= 2:
        return "scatter", {"x": numeric[0], "ys": [numeric[1]]}

    if not numeric:
        return None, "the result has no numeric column to plot"
    return None, (
        "the result has numbers but no time or category column to put on the x-axis "
        "(columns: " + ", ".join(columns) + ")"
    )


def _chart_dir() -> Path:
    path = Path(os.environ.get("CHART_DIR") or REPO_ROOT / "data" / "charts")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render(kind: str, spec: dict[str, Any], result: SqlResult, title: str, out: Path) -> None:
    x_col, y_cols = spec["x"], spec["ys"]
    xs = [r[x_col] for r in result.rows]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    try:
        if kind == "scatter":
            raw = [(to_float(r[x_col]), to_float(r[y_cols[0]])) for r in result.rows]
            pts = [(a, b) for a, b in raw if a is not None and b is not None]
            ax.scatter([a for a, _ in pts], [b for _, b in pts])
            ax.set_xlabel(_pretty(x_col))
            ax.set_ylabel(_pretty(y_cols[0]))
        elif kind == "line":
            labels = [_short_label(x) for x in xs]
            for y in y_cols:
                ax.plot(labels, [to_float(r[y]) for r in result.rows], marker="o", label=_pretty(y))
            ax.set_xlabel(_pretty(x_col))
            if len(y_cols) > 1:
                ax.legend()
            else:
                ax.set_ylabel(_pretty(y_cols[0]))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
            step = max(1, len(labels) // 12)
            ax.set_xticks(range(0, len(labels), step))
            ax.set_xticklabels(labels[::step], rotation=45, ha="right")
        else:  # bar / pie: one category column, one numeric column, largest first
            y = y_cols[0]
            pairs = [(str(r[x_col]), to_float(r[y]) or 0.0) for r in result.rows]
            pairs.sort(key=lambda p: p[1], reverse=True)
            limit = MAX_PIE_SLICES if kind == "pie" else MAX_BARS
            shown = pairs[:limit]
            if len(pairs) > limit:
                title += f" (top {limit} of {len(pairs)})"
            if kind == "pie" and all(v >= 0 for _, v in shown):
                total = sum(v for _, v in shown) or 1.0
                big = [(k, v) for k, v in shown if v / total >= 0.03]
                small = [(k, v) for k, v in shown if v / total < 0.03]
                if small:
                    big.append((f"other ({len(small)})", sum(v for _, v in small)))
                ax.pie(
                    [v for _, v in big],
                    labels=[k for k, _ in big],
                    autopct=lambda p: f"{p:.1f}%",
                    pctdistance=0.8,
                )
            else:
                ax.barh([k for k, _ in shown][::-1], [v for _, v in shown][::-1])
                ax.set_xlabel(_pretty(y))
        ax.set_title(title[:90])
        fig.tight_layout()
        fig.savefig(out, dpi=150)
    finally:
        plt.close(fig)


def _restrict_to_window(result: SqlResult, window: tuple[str, str] | None) -> SqlResult:
    """The semantic layer has no date filter, so a "trend in 2017" query returns every
    period; analysis_agent narrows to the requested window for its stats. Chart the
    same window, or the picture would contradict the narrative."""
    if window is None:
        return result
    temporal = next(
        (c for c, k in classify_columns(result.columns, result.rows).items() if k == "temporal"),
        None,
    )
    if temporal is None:
        return result
    lo, hi = window
    rows = [r for r in result.rows if lo[:10] <= str(r[temporal])[:10] <= hi[:10]]
    return result.model_copy(update={"rows": rows}) if rows else result


def build_chart(
    question: str,
    result: SqlResult,
    thread_id: str = "chart",
    period_window: tuple[str, str] | None = None,
    title: str | None = None,
) -> ChartResult:
    result = _restrict_to_window(result, period_window)
    kind, detail = _plan(question, result)
    if kind is None:
        # Only an explicit request obliges us to explain the absence of a chart.
        note = f"No chart was made: {detail}." if wants_chart(question) else None
        return ChartResult(path=None, note=note)
    assert isinstance(detail, dict)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", thread_id)[:60]
    out = _chart_dir() / f"{safe_id}-{uuid.uuid4().hex[:8]}.png"
    try:
        _render(kind, detail, result, title or question, out)
    except Exception as e:  # noqa: BLE001 -- a rendering bug must not lose the text answer
        note = f"The chart could not be rendered ({type(e).__name__})."
        return ChartResult(path=None, note=note if wants_chart(question) else None)
    return ChartResult(path=str(out), kind=kind)
