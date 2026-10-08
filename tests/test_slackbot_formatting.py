"""Pure formatting logic -- no Slack connection, no LLM."""

from talk_to_your_data.agents.eda.state import Finding
from talk_to_your_data.slackbot.app import MAX_SQL_BLOCK_CHARS, finding_to_blocks


def _finding(**overrides) -> Finding:
    base = dict(
        question="q",
        sql="SELECT 1",
        result_summary="42",
        caveats="none known",
        confidence="high",
        interpretation="it's 42",
    )
    base.update(overrides)
    return Finding(**base)


def _block_texts(blocks: list[dict]) -> str:
    return "\n".join(
        b["text"]["text"] for b in blocks if b["type"] in ("section",) and "text" in b
    )


def test_includes_interpretation_and_result_summary():
    blocks = finding_to_blocks(_finding(interpretation="it's 42", result_summary="total: 42"))
    joined = _block_texts(blocks)
    assert "it's 42" in joined
    assert "total: 42" in joined


def test_includes_confidence_and_caveats_in_context_block():
    blocks = finding_to_blocks(_finding(confidence="medium", caveats="filtered to 2017"))
    context_block = next(b for b in blocks if b["type"] == "context")
    text = context_block["elements"][0]["text"]
    assert "medium" in text
    assert "filtered to 2017" in text


def test_includes_sql_in_a_code_block():
    blocks = finding_to_blocks(_finding(sql="SELECT count(*) FROM orders"))
    joined = _block_texts(blocks)
    assert "```SELECT count(*) FROM orders```" in joined


def test_refusal_with_empty_sql_gets_a_placeholder_not_an_empty_code_block():
    blocks = finding_to_blocks(_finding(sql=""))
    joined = _block_texts(blocks)
    assert "no SQL" in joined
    assert "``````" not in joined


def test_long_sql_is_truncated():
    long_sql = "SELECT " + "a, " * 2000 + "1"
    blocks = finding_to_blocks(_finding(sql=long_sql))
    sql_block_text = next(
        b for b in blocks if "text" in b and "```" in b["text"]["text"]
    )["text"]["text"]
    assert len(sql_block_text) < len(long_sql)
    assert "truncated" in sql_block_text
    assert len(sql_block_text) <= MAX_SQL_BLOCK_CHARS + 100
