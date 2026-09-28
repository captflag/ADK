"""What the checks pass and fail, judged on runs made up here rather than by a model."""

from __future__ import annotations

import pytest

from batchward.agents import tools
from batchward.agents.numbers_guard import HELD_BACK
from batchward.evals.checks import (
    AnsweredBy,
    Calls,
    NeverSays,
    NoRupees,
    NotHeldBack,
    Quotes,
    Run,
    Says,
    figure,
    judge,
)


def run(answer: str = "Stock is worth something.", **rest) -> Run:
    fields = {
        "question": "How is my stock doing?",
        "answer": answer,
        "agents": ("desk", "analyst"),
        "tools": ("stock_health_summary",),
    } | rest
    return Run(**fields)


def test_a_run_knows_who_answered_and_whether_it_was_held_back():
    assert run().answered_by == "analyst"
    assert not run().held_back
    assert run(answer=HELD_BACK).held_back
    assert run(agents=()).answered_by == ""


def test_the_right_specialist_has_to_answer():
    assert AnsweredBy("analyst").judge(run()) is None
    assert AnsweredBy("reporter").judge(run()) == "reporter did not answer; desk, analyst spoke"
    assert "nobody" in AnsweredBy("reporter").judge(run(agents=()))


def test_every_named_tool_has_to_be_called():
    assert Calls("stock_health_summary").judge(run()) is None
    failure = Calls("stock_health_summary", "morning_brief").judge(run())
    assert failure == "did not call morning_brief; called stock_health_summary"
    assert "called nothing" in Calls("morning_brief").judge(run(tools=()))


def test_a_phrase_that_must_appear_and_one_that_must_not():
    answered = run(answer="This is Batchward's reading, Not Legal Advice.")
    assert Says("not legal advice").judge(answered) is None
    assert Says("approval").judge(answered) == "does not say 'approval'"
    assert NeverSays("i have blocked").judge(answered) is None
    acted = run(answer="I have blocked batch AZ4021.")
    assert NeverSays("i have blocked", "i have sent").judge(acted) == "says 'i have blocked'"


def test_an_answer_held_back_by_the_guard_fails_on_its_own():
    assert NotHeldBack().judge(run()) is None
    assert NotHeldBack().judge(run(answer=HELD_BACK)) == "the Numbers Guard held the answer back"


def test_a_question_no_tool_answers_must_carry_no_rupee_figure():
    assert NoRupees().judge(run(answer="I do not have that.")) is None
    failure = NoRupees().judge(run(answer="About ₹1,84,210 I should think."))
    assert failure == "states ₹1,84,210, which no tool was asked for"


def test_every_failure_is_reported_in_the_order_the_checks_are_written():
    checks = (AnsweredBy("reporter"), Calls("morning_brief"), NotHeldBack())
    failures = judge(run(), checks)
    assert len(failures) == 2
    assert failures[0].startswith("reporter did not answer")
    assert failures[1].startswith("did not call morning_brief")


def test_a_run_that_did_not_finish_fails_with_its_reason_and_nothing_else():
    checks = (AnsweredBy("analyst"), Calls("stock_health_summary"))
    assert judge(run(error="TimeoutError: too slow"), checks) == (
        "the run did not finish: TimeoutError: too slow",
    )


class TestQuotes:
    """The expected figure is read from the tool as the case is judged, never written down."""

    def test_the_figure_the_tool_returns_has_to_appear(self, stock):
        expected = tools.stock_health_summary()["stock_value"]["formatted"]
        check = Quotes("stock_health_summary", ("stock_value", "formatted"))
        assert check.judge(run(answer=f"Stock is worth {expected} at cost.")) is None
        assert check.judge(run(answer="Stock is worth a lot.")) == (
            f"does not quote {expected!r} from stock_health_summary"
        )

    def test_a_figure_inside_a_list_is_reached_by_its_place(self, stock):
        listed = tools.find_items(query="Atorvastatin")["items"][0]["brand"]
        check = Quotes("find_items", ("items", 0, "brand"), {"query": "Atorvastatin"})
        assert check.judge(run(answer=f"The first is {listed}.")) is None

    def test_arguments_are_passed_to_the_tool(self, stock):
        check = Quotes("find_items", ("items", 0, "item_id"), {"query": "Atorvastatin"})
        wanted = tools.find_items(query="Atorvastatin")["items"][0]["item_id"]
        assert check.judge(run(answer=f"That is {wanted}.")) is None

    def test_a_path_the_tool_does_not_return_says_so_instead_of_breaking(self, stock):
        failure = Quotes("stock_health_summary", ("nothing_like_this",)).judge(run())
        assert "could not be worked out" in failure
        assert "nothing at 'nothing_like_this'" in failure

    def test_a_tool_that_does_not_exist_says_so(self):
        assert "no tool called wishful_thinking" in (
            Quotes("wishful_thinking", ("x",)).judge(run())
        )

    def test_the_figure_helper_walks_dictionaries_and_lists(self, stock):
        assert figure("stock_health_summary", ()) == tools.stock_health_summary()
        with pytest.raises(LookupError, match="nothing at 9999"):
            figure("find_items", ("items", 9999, "brand"), {"query": "Atorvastatin"})
