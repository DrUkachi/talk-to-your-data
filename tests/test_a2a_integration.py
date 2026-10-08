"""Tests the A2A wiring with ask_question stubbed, not the real a2a.client stack
(that was verified by hand against a real running server + real a2a.client -- it
has its own background-dispatcher lifecycle that doesn't play well with ASGI
transport in a test harness). What's tested here is OUR code: does the executor
extract the question, call ask_question, and construct a valid Message; does the
agent card route resolve; does the plain /ask endpoint work.
"""

from fastapi.testclient import TestClient

from talk_to_your_data.agents.eda import a2a_executor, api
from talk_to_your_data.agents.eda.state import Finding

FAKE_FINDING = Finding(
    question="what is the answer?",
    sql="SELECT 1",
    result_summary="42",
    caveats="none known",
    confidence="high",
    interpretation="it's 42",
)


class _FakeEventQueue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


class _FakeRequestContext:
    def __init__(self, question: str, context_id: str) -> None:
        self._question = question
        self.context_id = context_id

    def get_user_input(self, delimiter: str = "\n") -> str:
        return self._question


async def test_executor_calls_ask_question_and_enqueues_a_message(monkeypatch):
    async def fake_ask_question(question, thread_id=None):
        assert question == "what is the answer?"
        assert thread_id == "thread-123"
        return FAKE_FINDING

    monkeypatch.setattr(a2a_executor, "ask_question", fake_ask_question)

    executor = a2a_executor.EdaAgentExecutor()
    queue = _FakeEventQueue()
    context = _FakeRequestContext("what is the answer?", "thread-123")
    await executor.execute(context, queue)

    assert len(queue.events) == 1
    message = queue.events[0]
    assert message.context_id == "thread-123"
    assert "42" in message.parts[0].text
    assert "it's 42" in message.parts[0].text


async def test_executor_surfaces_errors_as_text_instead_of_raising(monkeypatch):
    async def fake_ask_question_raises(question, thread_id=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(a2a_executor, "ask_question", fake_ask_question_raises)

    executor = a2a_executor.EdaAgentExecutor()
    queue = _FakeEventQueue()
    await executor.execute(_FakeRequestContext("q", "t"), queue)

    assert len(queue.events) == 1
    assert "boom" in queue.events[0].parts[0].text


def test_agent_card_is_discoverable():
    client = TestClient(api.app)
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "talk-to-your-data EDA agent"
    assert card["supportedInterfaces"][0]["url"].endswith("/a2a")
    assert card["skills"][0]["id"] == "ask_business_question"


def test_ask_endpoint_returns_a_finding(monkeypatch):
    async def fake_ask_question(question, thread_id=None):
        return FAKE_FINDING

    monkeypatch.setattr(api, "ask_question", fake_ask_question)

    client = TestClient(api.app)
    response = client.post("/ask", json={"question": "what is the answer?"})
    assert response.status_code == 200
    assert response.json()["result_summary"] == "42"
