from datetime import date
from decimal import Decimal

from batchward.reporting.brief import Brief, Line, Topic, Waiting

ON = date(2026, 9, 1)


def line(topic, rupees, text=None):
    return Line(topic, Decimal(rupees), text or f"{topic} line")


def waiting(number, days=1):
    return Waiting(f"A-{number:04d}", "purchase order", f"PO/C0{number}/260901 on a company", days)


def test_the_brief_numbers_five_lines_and_names_the_rest_with_their_figures():
    lines = tuple(line(topic, 1000 * (n + 1)) for n, topic in enumerate(Topic))
    text = Brief(ON, lines, ()).text()
    assert text.splitlines() == [
        "Batchward brief for Tue 01/09/2026",
        "1. recall line",
        "2. do not bill line",
        "3. claims closing line",
        "4. to order line",
        "5. credit owed line",
        "Also: expiring ₹6,000; dead stock ₹7,000; overcharged ₹8,000",
    ]


def test_requests_waiting_end_the_brief_with_how_to_answer_them():
    brief = Brief(ON, (line(Topic.ORDERS, 500),), (waiting(3, days=2), waiting(4, days=0)))
    assert brief.text().splitlines()[2:] == [
        "Waiting for approval:",
        "- A-0003 (purchase order, 2 days): PO/C03/260901 on a company",
        "- A-0004 (purchase order, today): PO/C04/260901 on a company",
        'Reply "approve A-0003", or "reject A-0003" and the reason.',
    ]
    many = Brief(ON, (), tuple(waiting(n) for n in range(1, 13)))
    listed = many.text().splitlines()
    assert listed[1] == "Nothing needs acting on today."
    assert sum(entry.startswith("- A-") for entry in listed) == 10
    assert "- and 2 more" in listed


def test_what_was_not_checked_is_said_last():
    brief = Brief(ON, (), (), ("claim windows, as no return terms are on record",))
    assert brief.text().splitlines()[-1] == (
        "Not checked: claim windows, as no return terms are on record."
    )


def test_the_headline_is_one_line_of_the_topics_shown_and_what_waits():
    lines = tuple(line(topic, 150000) for topic in Topic)
    headline = Brief(ON, lines, (waiting(1), waiting(2))).headline()
    assert headline == (
        "Recall ₹1,50,000 · Do not bill ₹1,50,000 · Claims closing ₹1,50,000 · "
        "To order ₹1,50,000 · Credit owed ₹1,50,000 · 2 waiting for approval"
    )
    assert Brief(ON, (), ()).headline() == "Nothing needs acting on today"
    assert "\n" not in headline
