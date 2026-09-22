import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from batchward.agents.numbers_guard import (
    Evidence,
    NumbersGuardPlugin,
    extract_batch_tokens,
    extract_dates,
    extract_figures,
    unsupported,
)


class TestExtractFigures:
    @pytest.mark.parametrize(
        ("text", "values"),
        [
            ("₹1,84,210 at risk", [Decimal(184210)]),
            ("stock worth 1234567.50", [Decimal("1234567.50")]),
            ("about 1.84 lakh", [Decimal(184000)]),
            ("2 crore of sales", [Decimal(20_000_000)]),
            ("₹38 lacs", [Decimal(3_800_000)]),
            ("₹38k", [Decimal(38_000)]),
            ("₹38L", [Decimal(3_800_000)]),
            ("₹1.84 लाख", [Decimal(184_000)]),
            ("₹2 करोड़", [Decimal(20_000_000)]),
            ("Rs5,00,000", [Decimal(500_000)]),
            ("changed by -790", [Decimal(-790)]),
            ("-₹4,500", [Decimal(-4500)]),
        ],
    )
    def test_reads_numbers_as_an_indian_reader_writes_them(self, text, values):
        assert [f.value for f in extract_figures(text)] == values

    def test_ignores_the_digits_inside_a_batch_number(self):
        assert extract_figures("batch AZ4021") == []
        assert extract_batch_tokens("batch AZ4021 and J4O21") == {"AZ4021", "J4O21"}

    def test_a_full_stop_after_a_number_is_not_a_decimal_point(self):
        (figure,) = extract_figures("There are 38 chemists.")
        assert (figure.number, figure.decimals) == (Decimal(38), 0)

    def test_dates_and_times_are_read_whole_not_as_numbers(self):
        assert extract_figures("expires 10/2027, as of 2026-04-01, due 13/02/2026 09:15") == []

    def test_a_range_is_two_numbers_not_a_negative_one(self):
        assert [f.value for f in extract_figures("5-12 days")] == [Decimal(5), Decimal(12)]


class TestExtractDates:
    def test_the_same_date_or_time_written_any_way_reads_the_same(self):
        text = "13/02/2026, 2026-02-13, 13 February 2026, Feb 13, 2026, 13/02/26, 09:15, 9:15 am"
        assert [m.key for m in extract_dates(text)] == [
            *["2026-02-13"] * 5,
            "09:15",
            "09:15",
        ]

    def test_an_expiry_month_reads_as_a_month(self):
        assert [m.key for m in extract_dates("10/2027 or October 2027")] == ["2027-10", "2027-10"]

    def test_a_date_without_a_year_reads_as_a_day_of_the_month(self):
        assert [m.key for m in extract_dates("3 February, Feb 13th")] == ["--02-03", "--02-13"]
        assert extract_dates("12 may expire in March") == []

    def test_something_that_is_not_a_real_date_is_left_as_numbers(self):
        assert extract_dates("38/42/2026 and 38:42 and 31 February") == []


class TestExtractBatchTokens:
    @pytest.mark.parametrize(
        ("text", "token"),
        [
            ("Batch az4o21 went", "az4o21"),
            ("Batch Az4O21 went", "Az4O21"),
            ("AZ4O21s went out", "AZ4O21s"),
            ("बैच AZ4O21को 38 केमिस्ट", "AZ4O21"),
            ("Batch AZ40210000001 was recalled.", "AZ40210000001"),
            ("batch AZ-4O2 recalled", "AZ-4O2"),
        ],
    )
    def test_recognises_a_batch_number_however_it_is_written(self, text, token):
        assert extract_batch_tokens(text) == {token}

    def test_ordinary_words_with_numbers_are_not_batch_numbers(self):
        text = "Azinil 500mg, the 12th item, top-10 list, Vitamin D3, B12, COVID-19, 38k"
        assert extract_batch_tokens(text) == set()


def evidence(*results, user=""):
    found = Evidence()
    for result in results:
        found.add_result(result)
    found.add_text(user)
    return found


TRACE = {"batch_no": "AZ4021", "chemists_supplied": 38}

RECALL_STATUS = {
    "batch_no": "AZ4021",
    "notices": [
        {
            "reference": "RN/2026/014",
            "received": "12/02/2026 09:15 IST",
            "blocked_batches": [
                {
                    "batch_no": "AZ4021",
                    "expiry": "10/2027",
                    "deadlines": [
                        {"deadline": "stop sale", "due": "13/02/2026 09:15 IST", "state": "met"},
                        {
                            "deadline": "complete the recall",
                            "due": "15/02/2026 09:15 IST",
                            "state": "overdue",
                        },
                    ],
                    "chemists_supplied": 38,
                    "units_supplied": 790,
                }
            ],
        }
    ],
}


class TestUnsupported:
    def test_a_figure_a_tool_returned_is_supported(self):
        assert unsupported("38 chemists received it", evidence({"chemists": 38})) == []

    def test_a_figure_no_tool_returned_is_not(self):
        assert unsupported("42 chemists received it", evidence({"chemists": 38})) == ["42"]

    def test_rounding_to_the_precision_written_is_supported(self):
        found = evidence({"value": {"rupees": 184210.4}, "per_day": 2.47})
        assert unsupported("₹1,84,210 at risk, about 2.5 a day", found) == []

    def test_rounding_in_lakh_is_supported_but_a_wrong_lakh_figure_is_not(self):
        found = evidence({"rupees": 184210.4})
        assert unsupported("roughly 1.84 lakh", found) == []
        assert unsupported("roughly 2.84 lakh", found) == ["2.84 lakh"]

    def test_a_faithful_answer_in_hindi_is_supported(self):
        assert unsupported("₹1.84 लाख जोखिम में", evidence({"rupees": 184210.4})) == []

    @pytest.mark.parametrize(
        ("text", "rupees"),
        [("about ₹1 crore", 5_000_000), ("about ₹1 lakh", 50_000), ("₹2", 1.50)],
    )
    def test_a_rounding_more_than_ten_percent_away_is_not_supported(self, text, rupees):
        assert unsupported(text, evidence({"rupees": rupees})) == [text.removeprefix("about ")]

    def test_a_natural_rounding_within_ten_percent_is_supported(self):
        assert unsupported("about ₹2 lakh", evidence({"rupees": 184210.4})) == []

    @pytest.mark.parametrize(
        ("text", "found", "problems"),
        [
            ("It went to forty-two chemists.", {"chemists": 38}, ["forty-two"]),
            ("It went to thirty-eight chemists.", {"chemists": 38}, []),
            ("About two lakh rupees is at risk.", {"rupees": 184210.4}, []),
            ("About five lakh rupees is at risk.", {"rupees": 184210.4}, ["five lakh"]),
            ("One lakh twenty thousand units.", {"units": 120000}, []),
            ("three batches, someone often tenders", {}, []),
        ],
    )
    def test_numbers_written_in_words_are_checked_too(self, text, found, problems):
        assert unsupported(text, evidence(found)) == problems

    def test_rounding_in_thousands_is_supported(self):
        assert unsupported("about ₹38k at risk", evidence({"rupees": 38210})) == []

    @pytest.mark.parametrize(
        ("text", "figure"),
        [
            ("₹38 lacs at risk", "₹38 lacs"),
            ("about ₹38k at risk", "₹38k"),
            ("₹38L at risk", "₹38L"),
            ("₹38 लाख का माल", "₹38 लाख"),
            ("₹38 करोड़ का माल", "₹38 करोड़"),
        ],
    )
    def test_a_scale_word_is_never_read_as_a_bare_number(self, text, figure):
        assert unsupported(text, evidence(TRACE)) == [figure]

    def test_a_number_straight_after_letters_is_checked(self):
        assert unsupported("Exposure is Rs5,00,000.", evidence(TRACE)) == ["Rs5,00,000"]
        assert unsupported("Exposure is Rs5,00,000.", evidence({"rupees": 500000})) == []

    def test_a_sign_flip_is_not_supported(self):
        assert unsupported("Stock changed by -790 units", evidence({"units": 790})) == ["-790"]
        assert unsupported("Stock changed by -790 units", evidence({"units": -790})) == []
        assert unsupported("Stock fell by 790 units", evidence({"units": -790})) == []

    def test_a_small_rupee_amount_is_still_checked(self):
        found = evidence(TRACE, {"due": "13/02/2026 09:15 IST"})
        assert unsupported("Refund ₹9 per strip", found) == ["₹9"]

    def test_a_plain_number_may_name_part_of_a_date_a_tool_gave(self):
        found = evidence({"due": "13/02/2026 09:15 IST"})
        assert unsupported("Due on the 13th, by 09:15.", found) == []
        assert unsupported("₹13 due", found) == ["₹13"]

    def test_numbers_inside_tool_strings_count_as_evidence(self):
        found = evidence({"formatted": "₹1,84,210", "expiry": "10/2027"})
        assert unsupported("₹1,84,210, expiring 10/2027", found) == []

    def test_small_whole_numbers_are_allowed_for_numbering_and_prose(self):
        assert unsupported("1. Order now\n2. Clear dead stock", evidence()) == []

    def test_small_numbers_above_the_limit_are_checked(self):
        assert unsupported("11 items", evidence()) == ["11"]

    def test_the_users_own_figures_may_be_repeated(self):
        found = evidence(user="What expires in the next 45 days?")
        assert unsupported("Nothing expires in the next 45 days.", found) == []

    def test_a_batch_number_must_appear_exactly_as_a_tool_gave_it(self):
        found = evidence({"batch_no": "AZ4021"})
        assert unsupported("Batch AZ4021 went to them.", found) == []
        assert unsupported("Batch AZ4O21 went to them.", found) == ["AZ4O21"]

    @pytest.mark.parametrize(
        ("text", "token"),
        [
            ("Batch az4o21 went to 38 chemists.", "az4o21"),
            ("Batch Az4O21 went to 38 chemists.", "Az4O21"),
            ("AZ4O21s went out to 38 chemists.", "AZ4O21s"),
            ("बैच AZ4O21को 38 केमिस्ट", "AZ4O21"),
            ("Batch AZ40210000001 was recalled.", "AZ40210000001"),
            ("batch AZ-4O2 recalled", "AZ-4O2"),
        ],
    )
    def test_a_misread_batch_number_is_caught_however_it_is_written(self, text, token):
        assert unsupported(text, evidence(TRACE)) == [token]

    def test_a_batch_number_followed_by_hindi_is_still_supported(self):
        assert unsupported("बैच AZ4021को 38 केमिस्ट", evidence(TRACE)) == []

    def test_dates_and_times_a_tool_gave_are_supported_in_any_form(self):
        found = evidence(RECALL_STATUS, {"as_of": "2026-04-01"})
        faithful = (
            "Notice received 12/02/2026 09:15 IST; stop sale was due 13 February 2026 at "
            "9:15 am; as of 01/04/2026 it has been overdue since 2026-02-15. Expiry 10/2027."
        )
        assert unsupported(faithful, found) == []

    def test_a_made_up_date_is_not_supported_even_when_its_numbers_are(self):
        found = evidence(RECALL_STATUS)
        invented = "Notice received 02/02/2026 09:15 IST; stop sale was due 03/02/2026 09:15 IST."
        assert unsupported(invented, found) == ["02/02/2026", "03/02/2026"]
        invented = "Stop sale was due 13/02/2026 at 09:15, completion is due 10/03/2026."
        assert unsupported(invented, found) == ["10/03/2026"]
        assert unsupported("Blocked at 10:15 on 3 February 2026.", found) == [
            "10:15",
            "3 February 2026",
        ]
        assert unsupported("It expires 07/2027.", found) == ["07/2027"]
        assert unsupported("Stop sale was due 3 February, completion 15 Feb.", found) == [
            "3 February"
        ]

    def test_a_failed_tool_call_vouches_for_nothing_it_was_given_or_said(self):
        found = evidence(TRACE)
        found.add_tool_call(
            {"batch_no": "AZ4O21"}, {"error": "no batch numbered 'AZ4O21' has ever been held"}
        )
        assert unsupported("Batch AZ4O21 went to 38 chemists.", found) == ["AZ4O21"]

    def test_a_successful_tool_call_counts_its_result_but_not_its_arguments(self):
        found = Evidence()
        found.add_tool_call({"within_days": 90}, {"batches": 14})
        assert unsupported("14 batches", found) == []
        assert unsupported("14 batches within 90 days", found) == ["90"]

    def test_a_lookup_that_finds_nothing_vouches_for_nothing(self):
        found = evidence(TRACE)
        found.add_tool_call(
            {"batch_no": "AZ4O21"},
            {
                "batch_no": "AZ4O21",
                "notices": [],
                "note": "no recall notice has been recorded for this batch number",
            },
        )
        text = "No recall notice is recorded, but batch AZ4O21 went to 38 chemists."
        assert unsupported(text, found) == ["AZ4O21"]

    def test_a_tool_repeating_a_figure_it_was_given_does_not_vouch_for_it(self):
        found = Evidence()
        found.add_tool_call(
            {"query": "9,99,999"}, {"query": "9,99,999", "total_matches": 0, "items": []}
        )
        found.add_tool_call(
            {"item_id": "C01-001", "weeks_of_history": 42},
            {"item_id": "C01-001", "weeks_of_history": 42, "forecast_units_per_week": 12.5},
        )
        assert unsupported("Stock worth ₹9,99,999 is at risk of expiry.", found) == ["₹9,99,999"]
        assert unsupported("42 weeks of sales, 12.5 a week", found) == ["42"]

    def test_a_batch_number_a_lookup_found_is_evidence(self):
        found = Evidence()
        found.add_tool_call(
            {"batch_no": "AZ4021"},
            {"batch_no": "AZ4021", "matches": [{"batch_no": "AZ4021", "chemists_supplied": 38}]},
        )
        assert unsupported("Batch AZ4021 went to 38 chemists.", found) == []


def run(coroutine):
    return asyncio.run(coroutine)


def context(invocation_id="inv-1"):
    return SimpleNamespace(invocation_id=invocation_id)


def answer(text, *, partial=False):
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]), partial=partial
    )


def call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


class TestPlugin:
    def test_lets_through_an_answer_whose_figures_all_came_from_tools(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None, tool_args={}, tool_context=context(), result={"chemists": 38}
            )
        )
        assert (
            run(
                guard.after_model_callback(
                    callback_context=context(), llm_response=answer("38 chemists.")
                )
            )
            is None
        )

    def test_holds_back_an_answer_with_an_invented_figure_without_repeating_it(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None, tool_args={}, tool_context=context(), result={"chemists": 38}
            )
        )
        replaced = run(
            guard.after_model_callback(
                callback_context=context(), llm_response=answer("42 chemists, ₹9,99,999.")
            )
        )
        (shown,) = replaced.content.parts
        assert "held it back" in shown.text
        assert "42" not in shown.text and "9,99,999" not in shown.text
        assert replaced.custom_metadata == {"numbers_guard": {"held_back": True}}
        (held,) = guard.held_back
        assert (held.text, held.unsupported) == ("42 chemists, ₹9,99,999.", ("42", "₹9,99,999"))

    def test_the_users_message_counts_as_evidence_for_the_run(self):
        guard = NumbersGuardPlugin()
        message = types.Content(
            role="user", parts=[types.Part(text="anything expiring in 45 days?")]
        )
        run(guard.on_user_message_callback(invocation_context=context(), user_message=message))
        assert (
            run(
                guard.after_model_callback(
                    callback_context=context(), llm_response=answer("None in 45 days.")
                )
            )
            is None
        )

    def test_arguments_the_model_passed_to_a_tool_are_not_evidence(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None, tool_args={"within_days": 90}, tool_context=context(), result={}
            )
        )
        replaced = run(
            guard.after_model_callback(
                callback_context=context(), llm_response=answer("Within 90 days:")
            )
        )
        assert guard.held_back[-1].unsupported == ("90",)
        assert replaced.custom_metadata == {"numbers_guard": {"held_back": True}}

    def test_holds_back_a_batch_number_known_only_from_a_failed_lookup(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None,
                tool_args={"batch_no": "AZ4021"},
                tool_context=context(),
                result={"batch_no": "AZ4021", "matches": [TRACE]},
            )
        )
        run(
            guard.after_tool_callback(
                tool=None,
                tool_args={"batch_no": "AZ4O21"},
                tool_context=context(),
                result={"error": "no batch numbered 'AZ4O21' has ever been held"},
            )
        )
        replaced = run(
            guard.after_model_callback(
                callback_context=context(),
                llm_response=answer("Batch AZ4O21 went to 38 chemists."),
            )
        )
        assert guard.held_back[-1].unsupported == ("AZ4O21",)
        assert replaced.custom_metadata == {"numbers_guard": {"held_back": True}}

    def test_the_user_can_still_be_told_their_batch_number_was_not_found(self):
        guard = NumbersGuardPlugin()
        message = types.Content(role="user", parts=[types.Part(text="Who got batch AZ4O21?")])
        run(guard.on_user_message_callback(invocation_context=context(), user_message=message))
        run(
            guard.after_tool_callback(
                tool=None,
                tool_args={"batch_no": "AZ4O21"},
                tool_context=context(),
                result={"error": "no batch numbered 'AZ4O21' has ever been held"},
            )
        )
        reply = answer("Batch AZ4O21 has never been held.")
        assert (
            run(guard.after_model_callback(callback_context=context(), llm_response=reply)) is None
        )

    def test_evidence_does_not_leak_between_runs(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None, tool_args={}, tool_context=context("a"), result={"units": 210}
            )
        )
        assert (
            run(
                guard.after_model_callback(
                    callback_context=context("b"), llm_response=answer("210 units")
                )
            )
            is not None
        )

    def test_evidence_is_dropped_when_the_run_ends(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None, tool_args={}, tool_context=context(), result={"units": 210}
            )
        )
        run(guard.after_run_callback(invocation_context=context()))
        assert (
            run(
                guard.after_model_callback(
                    callback_context=context(), llm_response=answer("210 units")
                )
            )
            is not None
        )

    def test_a_tool_call_alone_passes_untouched(self):
        guard = NumbersGuardPlugin()
        response = LlmResponse(
            content=types.Content(role="model", parts=[call("list_dead_stock", limit=50)])
        )
        assert (
            run(guard.after_model_callback(callback_context=context(), llm_response=response))
            is None
        )

    def test_text_beside_a_tool_call_is_checked_and_dropped_but_the_call_goes_ahead(self):
        guard = NumbersGuardPlugin()
        text = "About ₹9,99,999 is still with 42 chemists; passing you to the analyst."
        transfer = call("transfer_to_agent", agent_name="analyst")
        response = LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=text), transfer])
        )
        replaced = run(
            guard.after_model_callback(callback_context=context(), llm_response=response)
        )
        assert replaced.content.parts == [transfer]
        assert replaced.custom_metadata == {"numbers_guard": {"held_back": True}}
        assert (guard.held_back[-1].text, guard.held_back[-1].unsupported) == (
            text,
            ("₹9,99,999", "42"),
        )

    def test_supported_text_beside_a_tool_call_passes_untouched(self):
        guard = NumbersGuardPlugin()
        response = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="Checking now."), call("list_dead_stock", limit=5)],
            )
        )
        assert (
            run(guard.after_model_callback(callback_context=context(), llm_response=response))
            is None
        )

    def test_streamed_chunks_carry_no_answer_text(self):
        guard = NumbersGuardPlugin()
        thinking = types.Part(text="Looking at 999 units", thought=True)
        chunk = LlmResponse(
            content=types.Content(role="model", parts=[thinking, types.Part(text="999 units")]),
            partial=True,
        )
        stripped = run(guard.after_model_callback(callback_context=context(), llm_response=chunk))
        assert stripped.partial is True
        assert stripped.content.parts == [thinking]
        text_only = run(
            guard.after_model_callback(
                callback_context=context(), llm_response=answer("38 units", partial=True)
            )
        )
        assert text_only.content is None

    def test_thinking_is_not_checked(self):
        guard = NumbersGuardPlugin()
        run(
            guard.after_tool_callback(
                tool=None, tool_args={}, tool_context=context(), result={"chemists": 38}
            )
        )
        response = LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="38 of 40 is 95%", thought=True),
                    types.Part(text="38 chemists."),
                ],
            )
        )
        assert (
            run(guard.after_model_callback(callback_context=context(), llm_response=response))
            is None
        )
