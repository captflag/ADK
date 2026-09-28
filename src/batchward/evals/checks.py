"""What an answer has to satisfy, checked in code.

A model's wording varies from run to run, so an eval that compares answers word
for word measures nothing. What can be measured is what must not vary: the
specialist the desk hands the question to, the tools that are called, the
figures the answer quotes — which are read from the tool at the moment of
judging, never written down here — and the things the team must never say,
because it cannot do them (ADR 0003, ADR 0005).

Nothing here asks a model whether an answer was good. A check that cannot be
written in code is not a check, it is an opinion, and an opinion that costs a
call to grade is worse than one that costs nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from batchward.agents import tools as agent_tools
from batchward.agents.numbers_guard import HELD_BACK

RUPEES = re.compile(r"₹\s?[\d,]+")
"""A rupee amount as the tools format it, which an answer must not invent."""


@dataclass(frozen=True, slots=True)
class Run:
    """What one question produced: the answer, who wrote it, and what it called."""

    question: str
    answer: str
    agents: tuple[str, ...]
    """Every agent that spoke, in order; the last is the one that answered."""
    tools: tuple[str, ...]
    """Every tool called, in order, with repeats."""
    error: str | None = None
    """Why the run did not finish, if it did not."""

    @property
    def answered_by(self) -> str:
        return self.agents[-1] if self.agents else ""

    @property
    def held_back(self) -> bool:
        """Whether the Numbers Guard withheld the answer for quoting a figure no tool gave."""
        return HELD_BACK in self.answer


class Check(Protocol):
    """One thing that must hold. Returns why it did not, or None if it did."""

    def judge(self, run: Run) -> str | None: ...

    @property
    def wants(self) -> str: ...


@dataclass(frozen=True, slots=True)
class AnsweredBy:
    """The desk hands the question to the right specialist."""

    agent: str

    @property
    def wants(self) -> str:
        return f"{self.agent} answers"

    def judge(self, run: Run) -> str | None:
        if run.answered_by == self.agent:
            return None
        spoke = ", ".join(run.agents) or "nobody"
        return f"{self.agent} did not answer; {spoke} spoke"


@dataclass(frozen=True, slots=True)
class Calls:
    """Every one of these tools is called. A figure has to come from somewhere."""

    tools: tuple[str, ...]

    def __init__(self, *tools: str) -> None:
        object.__setattr__(self, "tools", tools)

    @property
    def wants(self) -> str:
        return "calls " + ", ".join(self.tools)

    def judge(self, run: Run) -> str | None:
        missing = [tool for tool in self.tools if tool not in run.tools]
        if not missing:
            return None
        called = ", ".join(dict.fromkeys(run.tools)) or "nothing"
        return f"did not call {', '.join(missing)}; called {called}"


@dataclass(frozen=True, slots=True)
class Quotes:
    """A figure the tool returns appears in the answer, read from the tool as it is judged."""

    tool: str
    path: tuple[str | int, ...]
    arguments: Mapping[str, object] = field(default_factory=dict)

    @property
    def wants(self) -> str:
        return f"quotes {self.tool}.{'.'.join(str(step) for step in self.path)}"

    def judge(self, run: Run) -> str | None:
        try:
            expected = figure(self.tool, self.path, self.arguments)
        except (LookupError, TypeError, ValueError) as error:
            return f"{self.wants} could not be worked out: {error}"
        if expected is None or expected == "":
            return f"{self.wants} is not in what the tool returned"
        if str(expected) in run.answer:
            return None
        return f"does not quote {expected!r} from {self.tool}"


@dataclass(frozen=True, slots=True)
class Says:
    """Every one of these appears in the answer, whatever case it is written in."""

    phrases: tuple[str, ...]

    def __init__(self, *phrases: str) -> None:
        object.__setattr__(self, "phrases", phrases)

    @property
    def wants(self) -> str:
        return "says " + ", ".join(repr(phrase) for phrase in self.phrases)

    def judge(self, run: Run) -> str | None:
        said = run.answer.lower()
        missing = [phrase for phrase in self.phrases if phrase.lower() not in said]
        return f"does not say {', '.join(repr(phrase) for phrase in missing)}" if missing else None


@dataclass(frozen=True, slots=True)
class NeverSays:
    """None of these appears: the team advises, and must not claim to have acted."""

    phrases: tuple[str, ...]

    def __init__(self, *phrases: str) -> None:
        object.__setattr__(self, "phrases", phrases)

    @property
    def wants(self) -> str:
        return "never says " + ", ".join(repr(phrase) for phrase in self.phrases)

    def judge(self, run: Run) -> str | None:
        said = run.answer.lower()
        found = [phrase for phrase in self.phrases if phrase.lower() in said]
        return f"says {', '.join(repr(phrase) for phrase in found)}" if found else None


@dataclass(frozen=True, slots=True)
class NotHeldBack:
    """The Numbers Guard did not have to withhold the answer."""

    @property
    def wants(self) -> str:
        return "is not held back by the Numbers Guard"

    def judge(self, run: Run) -> str | None:
        return "the Numbers Guard held the answer back" if run.held_back else None


@dataclass(frozen=True, slots=True)
class NoRupees:
    """No rupee amount at all: for a question no tool can answer."""

    @property
    def wants(self) -> str:
        return "states no rupee amount"

    def judge(self, run: Run) -> str | None:
        found = RUPEES.findall(run.answer)
        return f"states {found[0]}, which no tool was asked for" if found else None


def figure(tool: str, path: Sequence[str | int], arguments: Mapping[str, object] = {}) -> object:
    """The value a tool returns at this path, for comparing against an answer."""
    called = getattr(agent_tools, tool, None)
    if called is None or not callable(called):
        raise LookupError(f"there is no tool called {tool}")
    value: object = called(**dict(arguments))
    for step in path:
        if isinstance(step, int):
            if not isinstance(value, Sequence) or isinstance(value, str) or step >= len(value):
                raise LookupError(f"{tool} returned nothing at {step}")
            value = value[step]
            continue
        if not isinstance(value, Mapping) or step not in value:
            raise LookupError(f"{tool} returned nothing at {step!r}")
        value = value[step]
    return value


def judge(run: Run, checks: Sequence[Check]) -> tuple[str, ...]:
    """Every way this run failed its checks, in the order they are written."""
    if run.error:
        return (f"the run did not finish: {run.error}",)
    return tuple(failure for check in checks if (failure := check.judge(run)) is not None)
