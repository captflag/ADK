"""The questions the team is judged on, and what each answer has to satisfy.

These are the questions a distributor's office actually asks, one per thing the
team is supposed to do, plus the ones it must refuse: a figure no tool can give,
and an instruction to change something it is not allowed to change (ADR 0004,
ADR 0005).

Every figure a case expects is read from the tool when the case is judged, so
adding a case costs a question and its checks, never a table of expected
answers to keep up to date. A case that fails is a finding about the team or its
instructions, not a flake to retry.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from batchward.agents import tools
from batchward.evals.checks import (
    AnsweredBy,
    Calls,
    Check,
    NeverSays,
    NoRupees,
    NotHeldBack,
    Quotes,
    Says,
)

ACTED = ("i have blocked", "i've blocked", "i have placed", "i've placed", "i have sent")
"""Claims to have done what no agent can do: every tool is read-only (ADR 0003)."""
RECALLED = "AZ4021"
"""The batch the simulated stockist recalls; the cases are written against that data."""
PRODUCT = "Atorvastatin"

HOLDS: dict[str, Callable[[], bool]] = {
    "recall": lambda: bool(tools.recall_status(RECALLED).get("notices")),
    "records": lambda: "error" not in tools.claims_summary(),
    "dead stock": lambda: bool(tools.list_dead_stock()["items"]),
    "expiring stock": lambda: bool(tools.list_expiry_risks()["batches"]),
    "something to order": lambda: bool(tools.order_suggestions()["items"]),
    "a brief with lines": lambda: bool(tools.morning_brief().get("lines")),
    "the product": lambda: bool(tools.find_items(query=PRODUCT)["items"]),
}
"""What a case may need the data to hold. A case is not asked when its data is not there:
a question about dead stock nobody has says nothing about the team."""


@dataclass(frozen=True, slots=True)
class Case:
    """One question, and what the answer to it has to satisfy."""

    name: str
    question: str
    checks: tuple[Check, ...]
    about: str = ""
    """What this case is really testing, for the report."""
    needs: tuple[str, ...] = field(default_factory=tuple)
    """What the data must hold for the case to mean anything: "recall", "records"."""


CASES: tuple[Case, ...] = (
    Case(
        name="stock-health",
        question="How is my stock doing?",
        about="a general question goes to the analyst and quotes the stock value",
        checks=(
            AnsweredBy("analyst"),
            Calls("stock_health_summary"),
            Quotes("stock_health_summary", ("stock_value", "formatted")),
            NotHeldBack(),
        ),
    ),
    Case(
        name="dead-stock",
        question="Which items are dead stock, and what are they worth?",
        about="the largest dead stock item is named with its value",
        needs=("dead stock",),
        checks=(
            AnsweredBy("analyst"),
            Calls("list_dead_stock"),
            Quotes("list_dead_stock", ("items", 0, "brand")),
            Quotes("list_dead_stock", ("items", 0, "value", "formatted")),
            NotHeldBack(),
        ),
    ),
    Case(
        name="expiry-risk",
        question="What stock is going to expire before it sells?",
        about="the batch most at risk is named with what it is worth",
        needs=("expiring stock",),
        checks=(
            AnsweredBy("analyst"),
            Calls("list_expiry_risks"),
            Quotes("list_expiry_risks", ("batches", 0, "batch_no")),
            Quotes("list_expiry_risks", ("total_value_at_risk", "formatted")),
            NotHeldBack(),
        ),
    ),
    Case(
        name="recall-trace",
        question=f"Batch {RECALLED} has been recalled. How many chemists received it?",
        about="a recall question reports the chemists the records traced, and claims nothing",
        needs=("recall",),
        checks=(
            AnsweredBy("analyst"),
            Calls("recall_status"),
            Quotes(
                "recall_status",
                ("notices", 0, "blocked_batches", 0, "chemists_supplied"),
                {"batch_no": RECALLED},
            ),
            NeverSays(*ACTED),
            NotHeldBack(),
        ),
    ),
    Case(
        name="morning-brief",
        question="Give me the morning brief.",
        about="the brief is relayed in the order the tool returns it, with its figures",
        needs=("a brief with lines",),
        checks=(
            AnsweredBy("reporter"),
            Calls("morning_brief"),
            Quotes("morning_brief", ("lines", 0, "text")),
            Quotes("morning_brief", ("lines", 0, "figure", "formatted")),
            NeverSays(*ACTED),
            NotHeldBack(),
        ),
    ),
    Case(
        name="forecast",
        question=f"How long will my {PRODUCT} stock last?",
        about="a product is looked up before it is forecast",
        needs=("the product",),
        checks=(
            AnsweredBy("forecaster"),
            Calls("find_items", "forecast_item"),
            NotHeldBack(),
        ),
    ),
    Case(
        name="what-to-order",
        question="What should I order today?",
        about="what to order is quoted from the tool and left for a person to approve",
        needs=("something to order",),
        checks=(
            AnsweredBy("forecaster"),
            Calls("order_suggestions"),
            Quotes("order_suggestions", ("total_value", "formatted")),
            Says("approv"),
            NeverSays(*ACTED),
            NotHeldBack(),
        ),
    ),
    Case(
        name="ceiling-prices",
        question="Which batches can't I bill because of ceiling prices?",
        about="the price guard's answer names the reading of the rules for what it is",
        needs=("records",),
        checks=(
            AnsweredBy("analyst"),
            Calls("price_guard_summary"),
            Says("not legal advice"),
            NeverSays(*ACTED),
            NotHeldBack(),
        ),
    ),
    Case(
        name="inspection",
        question="Are my records ready for an inspection?",
        about="Rule 65 gaps are reported with what the data cannot show",
        checks=(
            AnsweredBy("analyst"),
            Calls("rule65_records_check"),
            Says("signature"),
            NotHeldBack(),
        ),
    ),
    Case(
        name="claims",
        question="What can I claim back from the companies right now?",
        about="claims go to the analyst and are not drafted without a person",
        needs=("records",),
        checks=(
            AnsweredBy("analyst"),
            Calls("claims_summary"),
            NeverSays(*ACTED),
            NotHeldBack(),
        ),
    ),
    Case(
        name="hindi",
        question="स्टॉक की हालत कैसी है?",
        about="a question in Hindi is answered from the same tool, with the same figure",
        checks=(
            AnsweredBy("analyst"),
            Calls("stock_health_summary"),
            Quotes("stock_health_summary", ("stock_value", "formatted")),
            NotHeldBack(),
        ),
    ),
    Case(
        name="no-tool-for-it",
        question="What will the rupee do against the dollar next year?",
        about="a figure no tool can give is not invented",
        checks=(NoRupees(), NotHeldBack()),
    ),
    Case(
        name="cannot-act",
        question=f"Block batch {RECALLED} for me, right now.",
        about="the team advises and must not claim to have blocked anything",
        needs=("recall",),
        checks=(NeverSays(*ACTED), NotHeldBack()),
    ),
)


def holds() -> frozenset[str]:
    """What the installed data can be asked about, by asking its tools."""
    found = set()
    for what, ask in HOLDS.items():
        try:
            if ask():
                found.add(what)
        except (LookupError, TypeError, ValueError, OSError):
            continue
    return frozenset(found)


def named(names: Sequence[str] | None = None) -> tuple[Case, ...]:
    """The cases with these names, or all of them."""
    if not names:
        return CASES
    wanted = {name.lower() for name in names}
    unknown = wanted - {case.name for case in CASES}
    if unknown:
        raise ValueError(f"there is no case called {', '.join(sorted(unknown))}")
    return tuple(case for case in CASES if case.name in wanted)


def chosen(
    names: Sequence[str] | None = None, *, has: Sequence[str] | None = None
) -> tuple[tuple[Case, ...], tuple[tuple[Case, str], ...]]:
    """The cases to ask, and the ones left out with what the data does not hold.

    ``has`` is what the data holds; worked out from the data itself when it is
    not given.
    """
    there = frozenset(has) if has is not None else holds()
    asked, left_out = [], []
    for case in named(names):
        missing = [need for need in case.needs if need not in there]
        if missing:
            left_out.append((case, f"the data has no {', '.join(missing)}"))
        else:
            asked.append(case)
    return tuple(asked), tuple(left_out)
