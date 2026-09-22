"""End-to-end checks against the real Gemini API.

Marked live and skipped without GOOGLE_API_KEY, because they cost money, need a
network, and a model's wording varies. They check what must not vary: the
figures the team quotes are the ones its tools returned (ADR 0003).

Run with: uv run pytest -m live
"""

import inspect
import os

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from batchward.agents import tools
from batchward.agents.team import build_app

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("GOOGLE_API_KEY"), reason="needs GOOGLE_API_KEY"),
]


def ask(question: str) -> str:
    runner = InMemoryRunner(app=build_app())
    session = runner.session_service.create_session(app_name=runner.app_name, user_id="owner")
    if inspect.isawaitable(session):
        pytest.skip("this ADK version creates sessions asynchronously; update the helper")
    message = types.Content(role="user", parts=[types.Part(text=question)])
    replies = [
        part.text
        for event in runner.run(user_id="owner", session_id=session.id, new_message=message)
        if event.is_final_response() and event.content
        for part in event.content.parts or []
        if part.text
    ]
    return "\n".join(replies)


def test_the_recall_answer_quotes_the_traced_chemist_count(stock, recall):
    answer = ask("Batch AZ4021 has been recalled. How many chemists received it?")
    assert str(len(recall.chemists)) in answer


def test_the_morning_brief_quotes_the_expiry_risk_the_tool_returned(stock):
    expected = tools.stock_health_summary()["expiry_risk"]["value"]["formatted"]
    brief = ask("Give me the morning brief.")
    assert expected in brief
