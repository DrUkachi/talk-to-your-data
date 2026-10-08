"""Slack Bolt adapter (Socket Mode) -- the fourth thin adapter around
ask_question()/answer_followup(), same pattern as api.py's REST routes and
a2a_executor.py. Runs as its own process (no inbound port needed -- Socket Mode
is an outbound connection), calling the shared library functions directly
in-process rather than over HTTP to eda-api. That's also what makes chart
upload possible for free: answer_followup's PandasAI chart PNG lands on *this*
process's local disk, ready to hand straight to files_upload_v2 -- no
cross-container file-sharing problem to solve.

Subscribes to a single event type (`message`), not `message` + `app_mention`
both -- confirmed empirically (a live test mention delivered BOTH events for
the same message) that subscribing to both double-fires a handler for a single
mention. A mention is instead detected by substring-matching `<@{bot_user_id}>`
in the message text, using Bolt's own `context["bot_user_id"]` (populated by
its internal auth call at startup, not a separate one here).

Thread semantics map directly onto Slack's own threading, no separate protocol:
- A DM with no thread -> always a new question (every DM is "to" the bot).
- A channel/group message with no thread -> a new question only if it mentions
  the bot (otherwise every unrelated message in a channel the bot's been added
  to would trigger an answer attempt).
- A reply inside a thread this bot already has a finding for -> a follow-up via
  answer_followup, regardless of mention -- so replying doesn't require
  re-tagging the bot, same as a normal threaded conversation.
- A reply inside a thread with no tracked finding -> only handled if it
  mentions the bot (treated as a new question scoped to that thread);
  otherwise ignored (most likely an unrelated human thread).
"""

import asyncio
import logging
import os
import re
from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from talk_to_your_data.agents.eda.ask import ask_question
from talk_to_your_data.agents.eda.findings_store import get_latest_finding_for_thread
from talk_to_your_data.agents.eda.followup_agent import answer_followup
from talk_to_your_data.agents.eda.state import Finding

logger = logging.getLogger(__name__)

# Keeps a long SQL string comfortably under Slack's ~3000-char block text limit.
MAX_SQL_BLOCK_CHARS = 2800


def _mentions_bot(text: str, bot_user_id: str | None) -> bool:
    return bool(bot_user_id) and f"<@{bot_user_id}>" in text


def _strip_mention(text: str, bot_user_id: str | None) -> str:
    if bot_user_id:
        text = re.sub(rf"<@{re.escape(bot_user_id)}>", "", text)
    return text.strip()


def finding_to_blocks(finding: Finding) -> list[dict[str, Any]]:
    """Pure -- unit-testable without a real Slack connection."""
    sql = finding.sql or "(no SQL -- this was a refusal)"
    if len(sql) > MAX_SQL_BLOCK_CHARS:
        sql = sql[:MAX_SQL_BLOCK_CHARS] + "\n-- truncated --"

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": finding.interpretation}},
        {"type": "section", "text": {"type": "mrkdwn", "text": finding.result_summary}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Confidence: *{finding.confidence}* · Caveats: {finding.caveats}",
                }
            ],
        },
    ]
    if sql.strip():
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{sql}```"}})
    return blocks


async def _post_finding(client: Any, channel: str, thread_ts: str, finding: Finding) -> None:
    await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        blocks=finding_to_blocks(finding),
        text=finding.result_summary,
    )
    if finding.chart_ref:
        try:
            await client.files_upload_v2(
                channel=channel, file=finding.chart_ref, thread_ts=thread_ts
            )
        except Exception:
            logger.exception("failed to upload chart %s to channel %s", finding.chart_ref, channel)
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="(generated a chart, but couldn't attach it here)",
            )


async def _answer_new_question(client: Any, channel: str, thread_ts: str, question: str) -> None:
    try:
        finding = await ask_question(question, thread_id=thread_ts)
    except Exception as e:  # noqa: BLE001 -- surfaced to the user, not swallowed;
        # mirrors a2a_executor's "report as text, don't crash the adapter" pattern.
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=f"Couldn't answer that: {e}"
        )
        return
    await _post_finding(client, channel, thread_ts, finding)


async def _answer_followup(
    client: Any, channel: str, thread_ts: str, finding_id: str, question: str
) -> None:
    try:
        finding = await answer_followup(finding_id, question)
    except Exception as e:  # noqa: BLE001
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=f"Couldn't answer that: {e}"
        )
        return
    await _post_finding(client, channel, thread_ts, finding)


async def handle_message(event: dict[str, Any], context: Any, client: Any) -> None:
    if event.get("bot_id") or "subtype" in event:
        return

    bot_user_id = context.get("bot_user_id")
    raw_text = event.get("text", "")
    question = _strip_mention(raw_text, bot_user_id)
    if not question:
        return

    channel = event["channel"]
    channel_type = event.get("channel_type")
    ts = event["ts"]
    thread_ts = event.get("thread_ts")
    is_reply = thread_ts is not None and thread_ts != ts
    reply_thread_ts = thread_ts if thread_ts is not None and is_reply else ts
    mentioned = _mentions_bot(raw_text, bot_user_id)

    if is_reply:
        existing = get_latest_finding_for_thread(reply_thread_ts)
        if existing is not None:
            await _answer_followup(client, channel, reply_thread_ts, existing["id"], question)
            return
        if channel_type != "im" and not mentioned:
            return  # an unrelated reply in someone else's thread -- not addressed to us
    elif channel_type != "im" and not mentioned:
        return  # a channel/group message that doesn't mention us -- not addressed to us

    await _answer_new_question(client, channel, reply_thread_ts, question)


async def _ignore_app_mention() -> None:
    # Confirmed live: Slack delivers BOTH an app_mention and a message event for
    # a single channel mention. handle_message already covers mentions (see
    # module docstring), so without this, Bolt logs every mention as an
    # "Unhandled request" 404 -- functionally harmless, but log noise on every
    # single question asked. A registered no-op listener silences it.
    return


def build_app() -> AsyncApp:
    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    app.event("message")(handle_message)
    app.event("app_mention")(_ignore_app_mention)
    return app


async def _run() -> None:
    # AsyncSocketModeHandler's __init__ constructs an aiohttp.ClientSession,
    # which requires a running event loop -- confirmed by hitting
    # "RuntimeError: no running event loop" when this was constructed before
    # asyncio.run(), not assumed. Must be built inside the loop, not outside it.
    app = build_app()
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    await handler.start_async()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
