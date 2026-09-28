"""Asking the team every case and scoring what comes back.

Each case gets its own app and its own session, so one question never sees
another's context. What a run produces is kept as data — the answer, the agents
that spoke, the tools they called — and judged afterwards by the checks, which
means a failing case can be explained without asking the model again.

A run that does not finish is a failure with its reason, not an exception: an
eval suite that stops at the first network error grades nothing.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass

from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

from batchward.agents.team import DEFAULT_MODEL, build_app
from batchward.evals.cases import CASES, Case
from batchward.evals.checks import Run, judge

TRANSFER = "transfer_to_agent"
"""Handing a question to a specialist is routing, not a tool the answer quotes."""
SECONDS = 120.0
"""How long one case may take before it counts as unanswered."""


@dataclass(frozen=True, slots=True)
class Result:
    """One case, what it produced, and every way it fell short."""

    case: Case
    run: Run
    failures: tuple[str, ...]
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class Report:
    """What a whole suite did, and against which model."""

    model: str
    results: tuple[Result, ...]
    seconds: float = 0.0

    @property
    def passed(self) -> tuple[Result, ...]:
        return tuple(result for result in self.results if result.passed)

    @property
    def failed(self) -> tuple[Result, ...]:
        return tuple(result for result in self.results if not result.passed)

    @property
    def checks(self) -> int:
        return sum(len(result.case.checks) for result in self.results)

    def text(self) -> str:
        """The report as the command prints it: every case, then every failure in full."""
        lines = [f"{len(self.results)} cases against {self.model}, {self.checks} checks"]
        for result in self.results:
            mark = "pass" if result.passed else "FAIL"
            lines.append(f"  {mark}  {result.case.name:<16} {result.seconds:5.1f}s")
            for failure in result.failures:
                lines.append(f"          {failure}")
        lines.append(f"{len(self.passed)} of {len(self.results)} cases passed")
        for result in self.failed:
            lines += ["", f"{result.case.name}: {result.case.about or result.case.question}"]
            lines.append(f"  asked    {result.case.question}")
            lines.append(f"  answered {_short(result.run.answer)}")
            lines.append(f"  by       {result.run.answered_by or 'nobody'}")
            lines.append(f"  called   {', '.join(dict.fromkeys(result.run.tools)) or 'nothing'}")
            for failure in result.failures:
                lines.append(f"  failed   {failure}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        """The report as plain data, for keeping a run to compare against later."""
        return {
            "model": self.model,
            "seconds": round(self.seconds, 1),
            "cases": len(self.results),
            "passed": len(self.passed),
            "results": [
                {
                    "case": result.case.name,
                    "question": result.case.question,
                    "about": result.case.about,
                    "passed": result.passed,
                    "failures": list(result.failures),
                    "answered_by": result.run.answered_by,
                    "tools": list(result.run.tools),
                    "answer": result.run.answer,
                    "seconds": round(result.seconds, 1),
                }
                for result in self.results
            ],
        }


async def ask(
    question: str, *, model: str | BaseLlm | None = None, seconds: float = SECONDS
) -> Run:
    """Put one question to a fresh team and keep what it did."""
    app = build_app(model)
    answer: list[str] = []
    agents: list[str] = []
    tools: list[str] = []
    error = None
    try:
        async with asyncio.timeout(seconds):
            await _converse(app, question, answer, agents, tools)
    except TimeoutError:
        error = f"nothing was answered within {seconds:.0f} seconds"
    except Exception as trouble:  # an eval reports what went wrong; it does not stop
        error = f"{type(trouble).__name__}: {trouble}"
    return Run(
        question=question,
        answer="\n".join(answer),
        agents=tuple(agents),
        tools=tuple(tools),
        error=error,
    )


async def _converse(
    app: App, question: str, answer: list[str], agents: list[str], tools: list[str]
) -> None:
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name=runner.app_name, user_id="owner")
    message = types.Content(role="user", parts=[types.Part(text=question)])
    async for event in runner.run_async(
        user_id="owner", session_id=session.id, new_message=message
    ):
        if not event.content:
            continue
        for part in event.content.parts or []:
            if part.function_call and part.function_call.name != TRANSFER:
                tools.append(part.function_call.name)
            if part.text and event.is_final_response():
                answer.append(part.text)
                if event.author and (not agents or agents[-1] != event.author):
                    agents.append(event.author)


async def run_case(
    case: Case, *, model: str | BaseLlm | None = None, seconds: float = SECONDS
) -> Result:
    """Ask one case and judge it."""
    started = time.monotonic()
    run = await ask(case.question, model=model, seconds=seconds)
    return Result(case, run, judge(run, case.checks), time.monotonic() - started)


async def run_cases_async(
    cases: Sequence[Case] = CASES,
    *,
    model: str | BaseLlm | None = None,
    seconds: float = SECONDS,
) -> Report:
    """Ask every case, one after another, and report."""
    started = time.monotonic()
    results = [await run_case(case, model=model, seconds=seconds) for case in cases]
    return Report(model=named(model), results=tuple(results), seconds=time.monotonic() - started)


def run_cases(
    cases: Sequence[Case] = CASES,
    *,
    model: str | BaseLlm | None = None,
    seconds: float = SECONDS,
) -> Report:
    """Ask every case from ordinary code."""
    return asyncio.run(run_cases_async(cases, model=model, seconds=seconds))


def named(model: str | BaseLlm | None) -> str:
    """What to call the model in the report."""
    if model is None:
        return os.environ.get("BATCHWARD_MODEL", DEFAULT_MODEL)
    return model if isinstance(model, str) else getattr(model, "model", type(model).__name__)


def _short(text: str, most: int = 300) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= most else one_line[: most - 1] + "…"
