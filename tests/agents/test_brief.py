import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from batchward.agents import tools
from batchward.agents.data import use
from batchward.analysis.costs import batch_costs
from batchward.analysis.health import stock_health
from batchward.claims.claim import Claim, ClaimLine, Settlement
from batchward.claims.terms import ReturnTerms
from batchward.compliance.prices import CeilingPrice
from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.core.approvals import Approval, Decision
from batchward.core.clock import end_of_day, ist_datetime
from batchward.core.models import MovementType
from batchward.core.orders import OrderLine, PurchaseOrder
from batchward.records.recalls import recall_status, receive_notice
from batchward.records.store import RecordStore
from batchward.reporting.brief import Topic, morning_brief
from batchward.reporting.inr import format_inr

NOW = datetime(2026, 4, 1, 2, 30, tzinfo=UTC)
"""Eight in the morning in India on the simulated day after the last movement."""


@pytest.fixture
def records(stock, recall, tmp_path):
    """The AZ4021 notice received, a ceiling below a busy brand's MRP, and return terms."""
    item = stock.items[recall.batch.item_id]
    company = stock.parties[recall.batch.company_id]
    notice = RecallNotice(
        reference="RN/2026/014",
        source=company.name,
        recall_class=RecallClass.I,
        received_at=ist_datetime(recall.notice_date, time(9, 15)),
        batch_no="AZ4021",
        manufacturer=company.name,
        product=f"{item.molecule} {item.strength}",
        expiry=recall.batch.expiry,
    )
    busy = most_sold_scheduled_item(stock)
    ceiling = ((busy.mrp - 5) / (1 + busy.gst_rate)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        receive_notice(
            store,
            notice,
            ledger=stock.ledger,
            items=stock.items,
            parties=stock.parties,
            at=notice.received_at,
        )
        store.save_ceiling(
            CeilingPrice(busy.molecule, busy.strength, busy.unit, ceiling, date(2026, 1, 1), "T/1")
        )
        for company_id in sorted({i.company_id for i in stock.items.values()}):
            store.save_terms(
                ReturnTerms(company_id, 180, 30, Decimal(90), date(2024, 1, 1), "letter")
            )
    return path


def most_sold_scheduled_item(stock):
    on_hand = Counter()
    for (key, _), units in stock.ledger.balances().items():
        on_hand[key.item_id] += units
    sold = Counter(
        m.batch.item_id
        for m in stock.ledger
        if m.kind is MovementType.SALE and stock.items[m.batch.item_id].dpco_scheduled
    )
    return next(stock.items[item_id] for item_id, _ in sold.most_common() if on_hand[item_id] > 0)


def brief_of(stock, path):
    with RecordStore(path, create=False) as store:
        return morning_brief(stock, store, now=NOW)


def test_lines_keep_the_fixed_order_of_urgency_and_each_quotes_its_figure(stock, records):
    brief = brief_of(stock, records)
    topics = [line.topic for line in brief.lines]
    assert topics == sorted(topics, key=list(Topic).index)
    assert topics[:2] == [Topic.RECALL, Topic.PRICE]
    assert all(format_inr(line.rupees) in line.text for line in brief.lines)
    assert brief.shown == brief.lines[:5]
    assert brief.not_checked == ()
    assert brief.on == stock.today


def test_the_recall_line_values_the_units_still_with_chemists_at_cost(stock, recall, records):
    (line,) = [line for line in brief_of(stock, records).lines if line.topic is Topic.RECALL]
    with RecordStore(records, create=False) as store:
        (notice,) = store.notices()
        status = recall_status(
            store,
            notice.reference,
            ledger=stock.ledger,
            items=stock.items,
            parties=stock.parties,
            as_of=max(ist_datetime(stock.today, time(0, 0)), notice.received_at),
            batches=stock.batches,
        )
    (report,) = status.reports
    assert report.outstanding > 0
    cost = batch_costs(stock.ledger, as_of=end_of_day(stock.today))[recall.batch]
    assert line.rupees == cost * report.outstanding
    holding = sum(1 for chemist in report.chemists if chemist.outstanding)
    assert line.text.startswith(
        f"Recall RN/2026/014: {report.outstanding} units of batch AZ4021 still with "
        f"{holding} chemists, {format_inr(line.rupees)} at cost; completing it was due "
    )


def test_each_figure_is_the_one_the_analysis_and_the_tools_give(stock, records):
    by_topic = {line.topic: line for line in brief_of(stock, records).lines}
    health = stock_health(stock.ledger, stock.locations, on=stock.today)
    with use(stock, records=records):
        orders = tools.order_suggestions()
        claims = tools.claims_summary()
        prices = tools.price_guard_summary(limit=50)
    assert by_topic[Topic.EXPIRY].rupees == health.expiry_value_at_risk
    assert float(by_topic[Topic.ORDERS].rupees) == orders["total_value"]["rupees"]
    assert f"To order today: {orders['items_to_order']} products" in by_topic[Topic.ORDERS].text
    blocked = prices["blocked_batches_on_hand"]
    assert f"Do not bill {blocked} batch" in by_topic[Topic.PRICE].text
    closing = claims["closing_soon"]["value"]["rupees"]
    assert float(by_topic[Topic.CLAIMS].rupees if Topic.CLAIMS in by_topic else 0) == closing
    exposure = prices["exposure"]["total"]["rupees"]
    assert float(by_topic[Topic.OVERCHARGE].rupees) == exposure > 0
    assert Topic.DEAD_STOCK not in by_topic  # nothing can be idle 120 days in 90 days of trading


def test_without_records_the_brief_says_what_it_could_not_check(stock):
    brief = morning_brief(stock, None, now=NOW)
    topics = {line.topic for line in brief.lines}
    assert Topic.RECALL not in topics and Topic.ORDERS in topics
    assert brief.waiting == ()
    assert brief.not_checked == (
        "recalls, claims, orders placed and approvals, as no records database was given",
    )


def test_claim_windows_are_not_checked_without_return_terms(stock, tmp_path):
    path = tmp_path / "records.sqlite"
    RecordStore(path).close()
    brief = brief_of(stock, path)
    assert brief.not_checked == ("claim windows, as no return terms are on record",)
    assert Topic.CLAIMS not in {line.topic for line in brief.lines}


def test_requests_waiting_are_listed_oldest_first_with_the_days_they_waited(stock, tmp_path):
    path = tmp_path / "records.sqlite"

    def ask(store, number, hours_ago):
        return store.request_approval(
            approval_id=f"order:PO/C0{number}/260401",
            kind="purchase order",
            digest="0" * 64,
            summary=f"PO/C0{number}/260401 on a company: 10 units of 1 product",
            at=NOW - timedelta(hours=hours_ago),
            session_id=f"s{number}",
        )

    with RecordStore(path) as store:
        answered = ask(store, 1, 120)
        store.save_decision(Decision(answered.number, False, "Ravi", NOW, "wrong company"))
        recent = ask(store, 2, 1)
        old = ask(store, 3, 72)
    brief = brief_of(stock, path)
    assert [(w.number, w.days) for w in brief.waiting] == [(old.number, 3), (recent.number, 0)]
    assert brief.waiting[0].text() == f"{old.number} (purchase order, 3 days): {old.summary}"
    assert brief.waiting[1].text().startswith(f"{recent.number} (purchase order, today): ")


def test_an_order_never_delivered_after_thirty_days_is_named_in_the_order_line(stock, tmp_path):
    item = next(iter(stock.items.values()))
    company = stock.parties[item.company_id]
    placed = stock.today - timedelta(days=40)
    order = PurchaseOrder(f"PO/{company.id}/{placed:%y%m%d}", company.id, placed,
                          (OrderLine(item.id, 12),))  # fmt: skip
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        store.save_approval(Approval(f"order:{order.number}", "purchase order", "Ravi",
                                     NOW - timedelta(days=40), "0" * 64, "an order"))  # fmt: skip
        store.save_order(order, f"order:{order.number}")
    (line,) = [line for line in brief_of(stock, path).lines if line.topic is Topic.ORDERS]
    assert "1 order over 30 days old waits for 12 units, " in line.text
    assert line.text.endswith(f": ask {company.name}.")


def test_credit_still_owed_on_claims_is_totalled_with_the_oldest_named(stock, tmp_path):
    key = next(key for key in stock.batches)
    company = stock.parties[key.company_id]
    line = ClaimLine(key, "GODOWN", 10, Decimal("20.00"), Decimal(100), Decimal("0.05"))
    claim = Claim(f"CL/{company.id}/260310", company.id, date(2026, 3, 10), (line,))
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        store.save_approval(Approval(f"claim:{claim.number}", "expiry claim", "Ravi",
                                     NOW - timedelta(days=22), "0" * 64, "a claim"))  # fmt: skip
        store.save_claim(claim, f"claim:{claim.number}")
        store.save_settlement(
            Settlement(claim.number, "CN-1", Decimal("100.00"), date(2026, 3, 20), "Ravi")
        )
    (owed,) = [line for line in brief_of(stock, path).lines if line.topic is Topic.CREDIT]
    assert owed.rupees == Decimal("110.00")
    assert owed.text == (
        f"Companies owe ₹110 of credit on 1 claim; the oldest, {claim.number} on "
        f"{company.name}, is 22 days old."
    )


def test_the_brief_tool_gives_the_lines_in_order_with_their_figures(stock, records):
    with use(stock, records=records):
        answer = tools.morning_brief()
    brief = brief_of(stock, records)
    assert [entry["text"] for entry in answer["lines"]] == [line.text for line in brief.shown]
    assert [entry["figure"]["formatted"] for entry in answer["lines"]] == [
        format_inr(line.rupees) for line in brief.shown
    ]
    assert [entry["topic"] for entry in answer["also"]] == [str(line.topic) for line in brief.also]
    assert answer["requests_waiting"] == 0 and answer["not_checked"] == []
    json.dumps(answer)
    with use(stock):
        assert "no records database" in tools.morning_brief()["not_checked"][0]


def test_several_open_recalls_share_one_line_led_by_the_first_deadline(stock, recall, records):
    supplied = Counter(
        m.batch
        for m in stock.ledger
        if m.kind is MovementType.SALE and m.batch != recall.batch and m.party_id
    )
    batch = next(key for key, _ in supplied.most_common())
    item, company = stock.items[batch.item_id], stock.parties[batch.company_id]
    notice = RecallNotice(
        reference="CDSCO/NSQ/2026/03",
        source="CDSCO",
        recall_class=RecallClass.II,
        received_at=ist_datetime(stock.today, time(0, 0)) - timedelta(days=1),
        batch_no=batch.batch_no,
        manufacturer=company.name,
        product=f"{item.molecule} {item.strength}",
        expiry=batch.expiry,
    )
    with RecordStore(records) as store:
        receive_notice(
            store,
            notice,
            ledger=stock.ledger,
            items=stock.items,
            parties=stock.parties,
            at=notice.received_at,
        )
    (line,) = [line for line in brief_of(stock, records).lines if line.topic is Topic.RECALL]
    assert line.text.startswith("2 recalls are open: ")
    assert "; the first to complete was due by " in line.text
