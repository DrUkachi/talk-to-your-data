"""Thin adapter: A2A's Task/Message protocol in, ask_question() out, Message back.
No new logic here on purpose -- ask_question() is the single protocol-agnostic
entry point; the plain POST /ask endpoint and (Phase 6) the Slackbot are the other
adapters around the same function.

a2a-sdk 1.2.2's `a2a.types` are protobuf-generated (not pydantic) -- confirmed by
inspecting the class MRO directly rather than assumed, since every pydantic-style
API I tried on AgentCard/Message first (model_fields, inspect.signature) failed.
"""

import uuid

import a2a.types as a2a_types
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .ask import ask_question


class EdaAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        question = context.get_user_input()
        thread_id = context.context_id or str(uuid.uuid4())

        try:
            finding = await ask_question(question, thread_id=thread_id)
            text = (
                f"{finding.interpretation}\n\n{finding.result_summary}\n\n"
                f"Caveats: {finding.caveats}"
            )
        except Exception as e:  # noqa: BLE001 -- surfaced to the A2A caller, not swallowed
            text = f"Couldn't answer that: {e}"

        message = a2a_types.Message(
            message_id=str(uuid.uuid4()),
            role=a2a_types.Role.ROLE_AGENT,
            parts=[a2a_types.Part(text=text)],
            context_id=thread_id,
        )
        await event_queue.enqueue_event(message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("this agent answers synchronously; there's nothing to cancel")
