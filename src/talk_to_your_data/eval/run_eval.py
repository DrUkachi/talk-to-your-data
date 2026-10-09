"""CI-gated eval runner (ROADMAP's Phase 6d) -- scores the golden set across
execution accuracy, faithfulness, and refusal correctness, and exits non-zero if
any dimension drops below threshold. Also checks p50 latency against
docs/architecture.md's documented 30s threshold.

Run directly (`uv run python -m talk_to_your_data.eval.run_eval`), not through
pytest -- this is what CI calls for the pass/fail gate and the report.
tests/test_eval_golden_set.py has thin pytest wrappers around the same scorers
for per-question visibility when debugging locally.

Runs cases with bounded concurrency (5 at a time) -- same total LLM cost either
way, just faster wall-clock in CI than running 40 questions serially.
"""

import asyncio
import os
import statistics
import sys
import time
import uuid
from typing import Any

from talk_to_your_data.agents.eda.ask import ask_question
from talk_to_your_data.agents.eda.findings_store import get_latest_finding_for_thread
from talk_to_your_data.agents.eda.followup_agent import answer_followup
from talk_to_your_data.eval.golden_set import (
    ANSWERABLE_CASES,
    CHART_CASES,
    FOLLOWUP_CASES,
    REFUSAL_CASES,
    AnswerableCase,
    ChartCase,
    FollowupCase,
    RefusalCase,
)
from talk_to_your_data.eval.scorers import (
    score_chart,
    score_execution_accuracy,
    score_faithfulness,
    score_followup,
    score_refusal_correctness,
)

THRESHOLDS = {
    "execution_accuracy": 0.90,
    "faithfulness": 0.90,
    "refusal_correctness": 1.0,
    # Chart choice/explanation and thread follow-ups are LLM-path-dependent (the
    # model may shape its SQL differently run to run), so they get a margin.
    "chart_correctness": 0.85,
    "followup_accuracy": 0.80,
}
LATENCY_P50_THRESHOLD_SECONDS = 30.0
LATENCY_PROBE_SIZE = 5
MAX_CONCURRENCY = 5

_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


async def _run_answerable_case(case: AnswerableCase) -> dict[str, Any]:
    thread_id = f"eval-{uuid.uuid4()}"
    start = time.monotonic()
    async with _semaphore:
        try:
            finding = await ask_question(case["question"], thread_id=thread_id)
        except Exception as e:  # noqa: BLE001 -- a hard failure counts against accuracy, not a crash
            return {
                "question": case["question"],
                "latency": time.monotonic() - start,
                "accuracy_pass": False,
                "accuracy_detail": f"raised {type(e).__name__}: {e!r}",
                "faithfulness_pass": False,
                "faithfulness_detail": "not scored -- no answer produced",
            }
    latency = time.monotonic() - start
    stored = get_latest_finding_for_thread(thread_id)
    result_rows = stored["result_rows"] if stored else []

    acc_pass, acc_detail = score_execution_accuracy(
        case["expected_value"], case["tolerance"], result_rows, finding
    )
    faith_pass, faith_detail = score_faithfulness(finding, result_rows)
    return {
        "question": case["question"],
        "latency": latency,
        "accuracy_pass": acc_pass,
        "accuracy_detail": acc_detail,
        "faithfulness_pass": faith_pass,
        "faithfulness_detail": faith_detail,
    }


async def _run_refusal_case(case: RefusalCase) -> dict[str, Any]:
    thread_id = f"eval-refusal-{uuid.uuid4()}"
    start = time.monotonic()
    async with _semaphore:
        try:
            finding = await ask_question(case["question"], thread_id=thread_id)
        except Exception as e:  # noqa: BLE001 -- one bad case (e.g. scope_guard's
            # "model didn't call the tool" guard tripping on an adversarial
            # prompt -- confirmed happening for real, not hypothetical) must not
            # crash the other 39 cases; it's a refusal-correctness failure, not a
            # suite-ending exception.
            return {
                "question": case["question"],
                "latency": time.monotonic() - start,
                "refusal_pass": False,
                "refusal_detail": f"raised {type(e).__name__} instead of refusing: {e!r}",
            }
    latency = time.monotonic() - start
    passed, detail = score_refusal_correctness(finding)
    return {
        "question": case["question"],
        "latency": latency,
        "refusal_pass": passed,
        "refusal_detail": detail,
    }


async def _run_chart_case(case: ChartCase) -> dict[str, Any]:
    async with _semaphore:
        try:
            finding = await ask_question(case["question"], thread_id=f"eval-chart-{uuid.uuid4()}")
        except Exception as e:  # noqa: BLE001 -- a hard failure counts against the score
            return {
                "question": case["question"],
                "chart_pass": False,
                "chart_detail": f"raised {type(e).__name__}: {e!r}",
            }
    passed, detail = score_chart(finding, case["expected"])
    return {"question": case["question"], "chart_pass": passed, "chart_detail": detail}


async def _run_followup_case(case: FollowupCase) -> dict[str, Any]:
    label = f"{case['parent_question']} -> {case['question']}"
    thread_id = f"eval-followup-{uuid.uuid4()}"
    async with _semaphore:
        try:
            await ask_question(case["parent_question"], thread_id=thread_id)
            parent = get_latest_finding_for_thread(thread_id)
            if parent is None:
                raise RuntimeError("parent finding was not stored")
            finding = await answer_followup(parent["id"], case["question"])
        except Exception as e:  # noqa: BLE001
            return {
                "question": label,
                "followup_pass": False,
                "followup_detail": f"raised {type(e).__name__}: {e!r}",
            }
    stored = get_latest_finding_for_thread(thread_id)
    rows = stored["result_rows"] if stored else []
    passed, detail = score_followup(
        case["expected_value"], case["tolerance"], case["mode"], rows, finding
    )
    return {"question": label, "followup_pass": passed, "followup_detail": detail}


async def _latency_probe() -> list[float]:
    """The 30s target is per-question for a single user. Latencies measured inside
    the 5-way-concurrent run above include queueing on shared LLM/DB capacity, so
    they overstate it -- probe a few questions one at a time instead."""
    latencies = []
    for case in ANSWERABLE_CASES[:LATENCY_PROBE_SIZE]:
        start = time.monotonic()
        try:
            await ask_question(case["question"], thread_id=f"eval-latency-{uuid.uuid4()}")
        except Exception:  # noqa: BLE001 -- a failed probe still counts its elapsed time
            pass
        latencies.append(time.monotonic() - start)
    return latencies


async def run_all() -> dict[str, Any]:
    answerable = await asyncio.gather(*(_run_answerable_case(c) for c in ANSWERABLE_CASES))
    refusal = await asyncio.gather(*(_run_refusal_case(c) for c in REFUSAL_CASES))
    chart = await asyncio.gather(*(_run_chart_case(c) for c in CHART_CASES))
    followup = await asyncio.gather(*(_run_followup_case(c) for c in FOLLOWUP_CASES))
    return {
        "answerable": list(answerable),
        "refusal": list(refusal),
        "chart": list(chart),
        "followup": list(followup),
        "probe_latencies": await _latency_probe(),
    }


def _report(results: dict[str, Any]) -> bool:
    answerable = results["answerable"]
    refusal = results["refusal"]
    chart = results["chart"]
    followup = results["followup"]

    acc_rate = sum(r["accuracy_pass"] for r in answerable) / len(answerable)
    faith_rate = sum(r["faithfulness_pass"] for r in answerable) / len(answerable)
    refusal_rate = sum(r["refusal_pass"] for r in refusal) / len(refusal)
    chart_rate = sum(r["chart_pass"] for r in chart) / len(chart)
    followup_rate = sum(r["followup_pass"] for r in followup) / len(followup)
    p50_latency = statistics.median(results["probe_latencies"])

    print(f"\n{'=' * 70}\nEVAL REPORT\n{'=' * 70}")
    acc_threshold = THRESHOLDS["execution_accuracy"]
    faith_threshold = THRESHOLDS["faithfulness"]
    refusal_threshold = THRESHOLDS["refusal_correctness"]
    print(f"Execution accuracy:  {acc_rate:.1%} (threshold {acc_threshold:.0%})")
    print(f"Faithfulness:        {faith_rate:.1%} (threshold {faith_threshold:.0%})")
    print(f"Refusal correctness: {refusal_rate:.1%} (threshold {refusal_threshold:.0%})")
    print(
        f"Chart correctness:   {chart_rate:.1%} (threshold {THRESHOLDS['chart_correctness']:.0%})"
        f" over {len(chart)} cases"
    )
    print(
        f"Follow-up accuracy:  {followup_rate:.1%} "
        f"(threshold {THRESHOLDS['followup_accuracy']:.0%}) over {len(followup)} cases"
    )
    latency_threshold = LATENCY_P50_THRESHOLD_SECONDS
    print(
        f"p50 latency:         {p50_latency:.1f}s over {len(results['probe_latencies'])} "
        f"serial questions (threshold {latency_threshold:.0f}s)"
    )

    print("\nFailures:")
    any_failures = False
    for r in answerable:
        if not r["accuracy_pass"]:
            any_failures = True
            print(f"  [accuracy]     {r['question']!r}: {r['accuracy_detail']}")
        if not r["faithfulness_pass"]:
            any_failures = True
            print(f"  [faithfulness] {r['question']!r}: {r['faithfulness_detail']}")
    for r in refusal:
        if not r["refusal_pass"]:
            any_failures = True
            print(f"  [refusal]      {r['question']!r}: {r['refusal_detail']}")
    for r in chart:
        if not r["chart_pass"]:
            any_failures = True
            print(f"  [chart]        {r['question']!r}: {r['chart_detail']}")
    for r in followup:
        if not r["followup_pass"]:
            any_failures = True
            print(f"  [follow-up]    {r['question']!r}: {r['followup_detail']}")
    if not any_failures:
        print("  (none)")

    passed = (
        acc_rate >= THRESHOLDS["execution_accuracy"]
        and faith_rate >= THRESHOLDS["faithfulness"]
        and refusal_rate >= THRESHOLDS["refusal_correctness"]
        and chart_rate >= THRESHOLDS["chart_correctness"]
        and followup_rate >= THRESHOLDS["followup_accuracy"]
        and p50_latency <= LATENCY_P50_THRESHOLD_SECONDS
    )
    print(f"\n{'PASS' if passed else 'FAIL'}")
    return passed


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY not set -- the eval suite needs real credentials.",
            file=sys.stderr,
        )
        sys.exit(1)
    results = asyncio.run(run_all())
    passed = _report(results)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
