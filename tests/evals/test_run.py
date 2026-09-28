"""The harness itself, driven by a scripted model so no call is made to Gemini.

The team, its tools, the transfers between agents and the Numbers Guard are all
real here; only the model is played from a script. That is what makes it
possible to check that a failing case is reported as failing, and why, without
paying for a model to misbehave on purpose.
"""

from __future__ import annotations

from batchward.agents import tools
from batchward.evals.cases import CASES
from batchward.evals.run import run_cases
from scripted_model import Scripted, calls, hands_to, says

STOCK_HEALTH = next(case for case in CASES if case.name == "stock-health")


def report(script, cases=(STOCK_HEALTH,)):
    return run_cases(cases, model=Scripted(model="scripted", script=script), seconds=60)


def answering(answer: str, *, tool: str = "stock_health_summary", by: str = "analyst") -> dict:
    """A desk that hands the question on, and a specialist that calls one tool and answers."""
    return {"desk": [[hands_to(by)]], by: [[calls(tool)], [says(answer)]]}


def test_a_good_answer_passes_every_check(stock):
    figure = tools.stock_health_summary()["stock_value"]["formatted"]
    given = report(answering(f"Stock at cost is {figure}. Nothing looks wrong."))
    (result,) = given.results
    assert result.passed, result.failures
    assert result.run.answered_by == "analyst"
    assert result.run.tools == ("stock_health_summary",)
    assert given.model == "scripted"
    assert len(given.passed) == 1 and given.failed == ()


def test_an_answer_without_the_figure_fails_on_the_figure(stock):
    given = report(answering("Your stock is in good shape overall."))
    (result,) = given.results
    assert not result.passed
    assert any("does not quote" in failure for failure in result.failures)


def test_a_figure_no_tool_returned_is_held_back_and_reported(stock):
    given = report(answering("Stock at cost is ₹9,99,99,999."))
    (result,) = given.results
    assert "the Numbers Guard held the answer back" in result.failures
    assert result.run.held_back


def test_the_desk_answering_a_stock_question_itself_fails_on_the_specialist(stock):
    figure = tools.stock_health_summary()["stock_value"]["formatted"]
    given = report({"desk": [[says(f"Stock at cost is {figure}.")]]})
    (result,) = given.results
    assert not result.passed
    assert result.failures[0] == "analyst did not answer; desk spoke"


def test_an_answer_with_no_tool_behind_it_fails_on_the_tool(stock):
    given = report({"desk": [[hands_to("analyst")]], "analyst": [[says("All good.")]]})
    (result,) = given.results
    assert "did not call stock_health_summary; called nothing" in result.failures


def test_a_run_the_script_cannot_answer_is_a_failure_not_a_crash(stock):
    given = report({"desk": []})
    (result,) = given.results
    assert result.failures[0].startswith("the run did not finish:")
    assert "desk" in result.failures[0]


def test_the_report_prints_every_case_and_explains_the_failures(stock):
    given = report(answering("Your stock is in good shape overall."))
    printed = given.text()
    assert "1 cases against scripted, 4 checks" in printed
    assert "FAIL  stock-health" in printed
    assert "0 of 1 cases passed" in printed
    assert "  asked    How is my stock doing?" in printed
    assert "  by       analyst" in printed
    assert "  called   stock_health_summary" in printed


def test_the_report_can_be_kept_as_plain_data(stock):
    figure = tools.stock_health_summary()["stock_value"]["formatted"]
    kept = report(answering(f"Stock at cost is {figure}.")).as_dict()
    assert (kept["model"], kept["cases"], kept["passed"]) == ("scripted", 1, 1)
    (result,) = kept["results"]
    assert (result["case"], result["passed"], result["failures"]) == ("stock-health", True, [])
    assert result["tools"] == ["stock_health_summary"]
