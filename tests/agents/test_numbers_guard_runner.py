"""The Numbers Guard inside a real ADK run, with a scripted model in place of Gemini."""

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from batchward.agents.numbers_guard import HELD_BACK, NumbersGuardPlugin


class Scripted(BaseLlm):
    """Plays each agent's model calls from a script: one list of responses per call."""

    script: dict[str, list[list[LlmResponse]]]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        for response in self.script[llm_request.config.labels["adk_agent_name"]].pop(0):
            if stream or not response.partial:
                yield response


def reply(*parts, partial=None):
    return LlmResponse(content=types.Content(role="model", parts=list(parts)), partial=partial)


def text(value, partial=None):
    return reply(types.Part(text=value), partial=partial)


def texts(events):
    return [
        part.text
        for event in events
        if event.author != "user" and event.content
        for part in event.content.parts or []
        if part.text
    ]


def converse(script, question, *, streaming=False):
    """Ask a desk and its analyst one question. Returns the text shown and the text stored."""

    async def go():
        model = Scripted(model="scripted", script=script)
        analyst = LlmAgent(name="analyst", model=model, instruction="Answer.")
        desk = LlmAgent(name="desk", model=model, instruction="Route.", sub_agents=[analyst])
        runner = InMemoryRunner(
            app=App(name="guarded", root_agent=desk, plugins=[NumbersGuardPlugin()])
        )
        session = await runner.session_service.create_session(app_name="guarded", user_id="u")
        mode = StreamingMode.SSE if streaming else StreamingMode.NONE
        shown = [
            event
            async for event in runner.run_async(
                user_id="u",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=question)]),
                run_config=RunConfig(streaming_mode=mode),
            )
        ]
        kept = await runner.session_service.get_session(
            app_name="guarded", user_id="u", session_id=session.id
        )
        return texts(shown), texts(kept.events)

    return asyncio.run(go())


def test_a_streamed_answer_with_an_invented_figure_never_reaches_the_client():
    streamed = [
        text("Batch AZ4021 went to 42 chemists", partial=True),
        text(" worth ₹9,99,999.", partial=True),
        text("Batch AZ4021 went to 42 chemists worth ₹9,99,999."),
    ]
    shown, kept = converse({"desk": [streamed]}, "Who got AZ4021?", streaming=True)
    assert shown == [HELD_BACK]
    assert kept == [HELD_BACK]


def test_a_streamed_answer_that_checks_out_arrives_whole():
    streamed = [
        text("AZ4021 went to", partial=True),
        text(" 38 chemists.", partial=True),
        text("AZ4021 went to 38 chemists."),
    ]
    shown, _ = converse({"desk": [streamed]}, "Did AZ4021 go to 38 chemists?", streaming=True)
    assert shown == ["AZ4021 went to 38 chemists."]


def test_text_beside_a_transfer_is_held_back_and_the_transfer_still_happens():
    desk = reply(
        types.Part(text="About ₹9,99,999 of AZ4021 is still with 42 chemists; passing you on."),
        types.Part(
            function_call=types.FunctionCall(
                name="transfer_to_agent", args={"agent_name": "analyst"}
            )
        ),
    )
    script = {"desk": [[desk]], "analyst": [[text("Handled.")]]}
    for streaming in (False, True):
        shown, kept = converse(
            {agent: [list(call) for call in calls] for agent, calls in script.items()},
            "Who got AZ4021?",
            streaming=streaming,
        )
        assert shown == ["Handled."]
        assert kept == ["Handled."]
