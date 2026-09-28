"""A model that plays a script, for checking what surrounds a model without calling one.

ADK takes a ``BaseLlm`` in place of a model name, so an agent can be given a
script: one list of responses per call it makes, keyed by the agent's name. That
is enough to make a real run happen — real tools, real transfers between agents,
real plugins — with nothing sent anywhere and nothing to pay for.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


class Scripted(BaseLlm):
    """Plays each agent's model calls from a script: one list of responses per call."""

    script: dict[str, list[list[LlmResponse]]]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        agent = llm_request.config.labels["adk_agent_name"]
        calls = self.script.get(agent)
        if not calls:
            raise AssertionError(f"the script says nothing more for {agent}")
        for response in calls.pop(0):
            if stream or not response.partial:
                yield response


def reply(*parts: types.Part, partial: bool | None = None) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=list(parts)), partial=partial)


def says(value: str, partial: bool | None = None) -> LlmResponse:
    return reply(types.Part(text=value), partial=partial)


def calls(name: str, **arguments: object) -> LlmResponse:
    return reply(types.Part(function_call=types.FunctionCall(name=name, args=dict(arguments))))


def hands_to(agent: str) -> LlmResponse:
    return calls("transfer_to_agent", agent_name=agent)
