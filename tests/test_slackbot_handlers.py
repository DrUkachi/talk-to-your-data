"""handle_message's routing logic, with ask_question/answer_followup/
get_latest_finding_for_thread monkeypatched and a duck-typed Slack client --
no real Socket Mode connection (verified by hand against the real workspace
instead, same reason a2a_executor's real client stack isn't driven in
automated tests either).
"""

from talk_to_your_data.agents.eda.state import Finding
from talk_to_your_data.slackbot import app as slackbot_app

BOT_USER_ID = "U_BOT"

FINDING = Finding(
    question="q",
    sql="SELECT 1",
    result_summary="42",
    caveats="none known",
    confidence="high",
    interpretation="it's 42",
)

FINDING_WITH_CHART = FINDING.model_copy(update={"chart_ref": "/tmp/chart.png"})


class _FakeClient:
    def __init__(self, raise_on_upload: bool = False):
        self.posted: list[dict] = []  # real answers/errors only -- acks are tracked apart
        self.acks: list[dict] = []
        self.deleted: list[dict] = []
        self.uploaded: list[dict] = []
        self._raise_on_upload = raise_on_upload

    async def chat_postMessage(self, **kwargs):
        if kwargs.get("text") == slackbot_app.ACK_TEXT:
            self.acks.append(kwargs)
            return {"ts": f"ack.{len(self.acks)}"}
        self.posted.append(kwargs)
        return {"ts": "msg.1"}

    async def chat_delete(self, **kwargs):
        self.deleted.append(kwargs)

    async def files_upload_v2(self, **kwargs):
        if self._raise_on_upload:
            raise RuntimeError("upload failed")
        self.uploaded.append(kwargs)


def _event(**overrides) -> dict:
    base = {"channel": "C1", "channel_type": "channel", "ts": "100.1", "text": "hello"}
    base.update(overrides)
    return base


def _context() -> dict:
    return {"bot_user_id": BOT_USER_ID}


async def test_dm_top_level_message_is_a_new_question(monkeypatch):
    calls = {}

    async def fake_ask_question(question, thread_id=None):
        calls["question"] = question
        calls["thread_id"] = thread_id
        return FINDING

    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient()

    event = _event(channel_type="im", text="what was total revenue?")
    await slackbot_app.handle_message(event, _context(), client)

    assert calls == {"question": "what was total revenue?", "thread_id": "100.1"}
    assert len(client.posted) == 1
    assert client.posted[0]["thread_ts"] == "100.1"


async def test_channel_message_without_mention_is_ignored(monkeypatch):
    monkeypatch.setattr(slackbot_app, "ask_question", _unexpected_call)
    client = _FakeClient()

    event = _event(channel_type="channel", text="just chatting, not for the bot")
    await slackbot_app.handle_message(event, _context(), client)

    assert client.posted == []


async def test_channel_message_with_mention_is_a_new_question(monkeypatch):
    calls = {}

    async def fake_ask_question(question, thread_id=None):
        calls["question"] = question
        calls["thread_id"] = thread_id
        return FINDING

    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient()

    event = _event(channel_type="channel", text=f"<@{BOT_USER_ID}> total revenue?")
    await slackbot_app.handle_message(event, _context(), client)

    assert calls["question"] == "total revenue?"  # mention stripped
    assert calls["thread_id"] == "100.1"


async def test_reply_in_a_tracked_thread_is_a_followup_even_without_mention(monkeypatch):
    calls = {}

    def fake_get_latest(thread_id):
        calls["looked_up_thread"] = thread_id
        return {"id": "parent-finding-id"}

    async def fake_answer_followup(finding_id, question):
        calls["finding_id"] = finding_id
        calls["question"] = question
        return FINDING

    monkeypatch.setattr(slackbot_app, "get_latest_finding_for_thread", fake_get_latest)
    monkeypatch.setattr(slackbot_app, "answer_followup", fake_answer_followup)
    client = _FakeClient()

    event = _event(channel_type="channel", thread_ts="100.1", ts="100.2", text="and by category?")
    await slackbot_app.handle_message(event, _context(), client)

    assert calls["looked_up_thread"] == "100.1"
    assert calls["finding_id"] == "parent-finding-id"
    assert calls["question"] == "and by category?"
    assert client.posted[0]["thread_ts"] == "100.1"


async def test_reply_in_an_untracked_channel_thread_without_mention_is_ignored(monkeypatch):
    monkeypatch.setattr(slackbot_app, "get_latest_finding_for_thread", lambda thread_id: None)
    monkeypatch.setattr(slackbot_app, "ask_question", _unexpected_call)
    client = _FakeClient()

    event = _event(channel_type="channel", thread_ts="100.1", ts="100.2", text="unrelated reply")
    await slackbot_app.handle_message(event, _context(), client)

    assert client.posted == []


async def test_reply_in_an_untracked_channel_thread_with_mention_is_a_new_question(monkeypatch):
    calls = {}

    async def fake_ask_question(question, thread_id=None):
        calls["thread_id"] = thread_id
        return FINDING

    monkeypatch.setattr(slackbot_app, "get_latest_finding_for_thread", lambda thread_id: None)
    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient()

    event = _event(
        channel_type="channel",
        thread_ts="100.1",
        ts="100.2",
        text=f"<@{BOT_USER_ID}> new question in old thread",
    )
    await slackbot_app.handle_message(event, _context(), client)

    assert calls["thread_id"] == "100.1"  # scoped to the thread, not the reply's own ts


async def test_dm_reply_in_an_untracked_thread_still_answers(monkeypatch):
    # DMs have no "mention" concept -- every message is already "to" the bot.
    calls = {}

    async def fake_ask_question(question, thread_id=None):
        calls["thread_id"] = thread_id
        return FINDING

    monkeypatch.setattr(slackbot_app, "get_latest_finding_for_thread", lambda thread_id: None)
    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient()

    event = _event(channel_type="im", thread_ts="100.1", ts="100.2", text="a follow-up thought")
    await slackbot_app.handle_message(event, _context(), client)

    assert calls["thread_id"] == "100.1"


async def test_bots_own_message_is_ignored(monkeypatch):
    monkeypatch.setattr(slackbot_app, "ask_question", _unexpected_call)
    client = _FakeClient()

    event = _event(channel_type="im", bot_id="B1")
    await slackbot_app.handle_message(event, _context(), client)

    assert client.posted == []


async def test_message_with_subtype_is_ignored(monkeypatch):
    monkeypatch.setattr(slackbot_app, "ask_question", _unexpected_call)
    client = _FakeClient()

    event = _event(channel_type="im", subtype="message_changed")
    await slackbot_app.handle_message(event, _context(), client)

    assert client.posted == []


async def test_ask_question_failure_is_reported_as_text_not_raised(monkeypatch):
    async def fake_ask_question_raises(question, thread_id=None):
        raise RuntimeError("mcp server unreachable")

    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question_raises)
    client = _FakeClient()

    event = _event(channel_type="im")
    await slackbot_app.handle_message(event, _context(), client)

    assert len(client.posted) == 1
    assert "mcp server unreachable" in client.posted[0]["text"]


async def test_chart_ref_triggers_a_file_upload(monkeypatch):
    async def fake_ask_question(question, thread_id=None):
        return FINDING_WITH_CHART

    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient()

    event = _event(channel_type="im")
    await slackbot_app.handle_message(event, _context(), client)

    assert len(client.uploaded) == 1
    assert client.uploaded[0]["file"] == "/tmp/chart.png"
    assert client.uploaded[0]["thread_ts"] == "100.1"


async def test_chart_upload_failure_is_reported_but_does_not_raise(monkeypatch):
    async def fake_ask_question(question, thread_id=None):
        return FINDING_WITH_CHART

    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient(raise_on_upload=True)

    event = _event(channel_type="im")
    await slackbot_app.handle_message(event, _context(), client)

    assert any("couldn't attach" in p["text"] for p in client.posted)


async def _unexpected_call(*args, **kwargs):
    raise AssertionError("should not have been called")


async def test_new_question_is_acknowledged_then_the_ack_is_removed(monkeypatch):
    async def fake_ask_question(question, thread_id=None):
        return FINDING

    monkeypatch.setattr(slackbot_app, "ask_question", fake_ask_question)
    client = _FakeClient()
    await slackbot_app.handle_message(_event(channel_type="im"), _context(), client)

    assert len(client.acks) == 1 and len(client.posted) == 1
    assert client.deleted == [{"channel": "C1", "ts": "ack.1"}]


async def test_ack_is_removed_even_when_the_question_fails(monkeypatch):
    async def boom(question, thread_id=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(slackbot_app, "ask_question", boom)
    client = _FakeClient()
    await slackbot_app.handle_message(_event(channel_type="im"), _context(), client)

    assert "Couldn't answer that" in client.posted[0]["text"]
    assert len(client.deleted) == 1
