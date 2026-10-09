"""Regression check against the real Foundry-hosted model (ROADMAP's Phase 3
evaluation note) -- run explicitly with `uv run pytest -m llm` whenever
llm.py's prompt or tool schema changes. Not part of the default suite: needs
real OPENAI_API_KEY/OPENAI_BASE_URL credentials and costs real tokens.

Assertions are intentionally soft on *exactly* which findings get a proposal --
a model judging a fix unsafe to apply automatically (e.g. a negative `amount`
might be a legitimate refund, not a data error) is a reasonable call, not a
failure. What must hold: every proposal references a real finding, applying the
proposals strictly reduces the number of remaining issues, and it doesn't
regress to proposing nothing at all.
"""

import os

import pytest

from talk_to_your_data.agents.cleaning import fix_strategies, llm, profiling
from talk_to_your_data.db import app_engine

pytestmark = [
    # llm only, deliberately not also `integration` -- so a plain `-m integration`
    # run doesn't sweep in real, costly LLM calls; use `-m llm` explicitly for this.
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OPENAI_API_KEY configured"),
]


def test_real_model_proposes_valid_fixes_that_reduce_findings(dirty_fixture_table):
    engine = app_engine()
    findings = profiling.run_profile(engine, "clean", dirty_fixture_table)
    assert len(findings) == 4  # sanity: the fixture itself is as expected

    proposals = llm.propose_fixes(findings)

    assert len(proposals) >= 1, "model proposed nothing at all for 4 real defects"
    finding_ids = {f.id for f in findings}
    assert all(p.finding_id in finding_ids for p in proposals), (
        "a proposal referenced a finding_id that doesn't exist"
    )

    applied = fix_strategies.apply_fixes(
        engine, "clean", findings, proposals, approved_finding_ids=[p.finding_id for p in proposals]
    )
    assert len(applied) == len(proposals)

    remaining = profiling.run_profile(engine, "clean", dirty_fixture_table)
    assert len(remaining) < len(findings), "applying the model's own proposals didn't fix anything"
