import json
from collections import Counter
from dataclasses import replace
from datetime import date, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from google.adk.tools import FunctionTool

from batchward.agents import tools
from batchward.agents.data import StockData, use
from batchward.analysis.health import stock_health
from batchward.claims.submission import windows_for
from batchward.claims.terms import ReturnTerms, TermsTable
from batchward.claims.windows import WindowState
from batchward.compliance.prices import CeilingPrice
from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.core.clock import ist_datetime
from batchward.core.ledger import Ledger
from batchward.core.models import (
    Batch,
    BatchKey,
    BatchStatus,
    Item,
    Location,
    MovementType,
    StockMovement,
)
from batchward.records.recalls import receive_notice, release_hold
from batchward.records.store import RecordStore

ALL_TOOLS = sorted(
    {*tools.ANALYST_TOOLS, *tools.FORECASTER_TOOLS, *tools.REPORTER_TOOLS},
    key=lambda f: f.__name__,
)


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda f: f.__name__)
def test_every_tool_has_a_declaration_adk_can_send_to_the_model(tool):
    declaration = FunctionTool(tool)._get_declaration()
    assert declaration.name == tool.__name__
    assert declaration.description


def test_tool_parameters_and_required_arguments_are_what_the_model_is_told():
    def signature(tool):
        schema = FunctionTool(tool)._get_declaration().parameters_json_schema or {}
        return sorted(schema.get("properties", {})), sorted(schema.get("required", []))

    assert {tool.__name__: signature(tool) for tool in ALL_TOOLS} == {
        "check_item_prices": (["item_id"], ["item_id"]),
        "claims_summary": (["limit"], []),
        "find_items": (["limit", "query"], ["query"]),
        "forecast_item": (["item_id", "weeks_of_history"], ["item_id"]),
        "item_stock": (["item_id"], ["item_id"]),
        "list_dead_stock": (["limit"], []),
        "list_expiry_risks": (["limit", "within_days"], []),
        "order_suggestions": (["company_id", "limit"], []),
        "price_guard_summary": (["limit"], []),
        "recall_status": (["batch_no"], ["batch_no"]),
        "rule65_records_check": (["limit"], []),
        "stock_health_summary": ([], []),
        "trace_batch_number": (["batch_no"], ["batch_no"]),
    }


def test_every_tool_result_is_json_safe(stock):
    item_id = next(iter(stock.items))
    results = [
        tools.stock_health_summary(),
        tools.list_dead_stock(),
        tools.list_expiry_risks(),
        tools.find_items("a"),
        tools.item_stock(item_id),
        tools.forecast_item(item_id),
        tools.trace_batch_number("AZ4021"),
        tools.recall_status("AZ4021"),
        tools.price_guard_summary(),
        tools.check_item_prices(item_id),
        tools.rule65_records_check(),
        tools.claims_summary(),
        tools.order_suggestions(),
    ]
    for result in results:
        json.dumps(result)


def test_health_summary_quotes_the_same_figures_the_analysis_computes(stock):
    summary = tools.stock_health_summary()
    health = stock_health(stock.ledger, stock.locations, on=stock.today)
    assert summary["as_of"] == stock.today.isoformat()
    assert summary["stock_value"]["rupees"] == float(health.stock_value)
    assert summary["stock_value"]["formatted"].startswith("₹")
    assert summary["expiry_risk"]["batches"] == len(health.expiry_risks)
    assert summary["dead_stock"]["items"] == len(health.dead_stock)


@pytest.mark.parametrize("tool", [tools.list_dead_stock, tools.list_expiry_risks])
def test_list_tools_respect_the_limit_and_never_exceed_fifty(stock, tool):
    listed = tool(limit=2)
    key = "items" if "items" in listed else "batches"
    assert len(listed[key]) <= 2
    assert len(tool(limit=10_000)[key]) <= tools.MAX_LIMIT


def test_find_items_matches_brand_or_molecule_case_insensitively(stock):
    found = tools.find_items("AZITHRO")
    assert found["total_matches"] > 0
    assert all("azithro" in item["molecule"].lower() for item in found["items"])


def test_find_items_asks_for_a_name_when_given_nothing(stock):
    assert "error" in tools.find_items("   ")


def test_item_stock_lists_batches_earliest_expiry_first_and_adds_up(stock):
    positions = Counter(key.item_id for key, _ in stock.ledger.balances())
    item_id = positions.most_common(1)[0][0]
    batches = tools.item_stock(item_id)["batches"]
    assert len(batches) == positions[item_id]
    assert batches == sorted(batches, key=lambda b: (b["days_to_expiry"], b["batch_no"]))
    held = sum(
        units for (key, _), units in stock.ledger.balances().items() if key.item_id == item_id
    )
    assert sum(b["units"] for b in batches) == held


@pytest.mark.parametrize("tool", [tools.item_stock, tools.forecast_item])
def test_item_tools_explain_an_unknown_item_instead_of_guessing(stock, tool):
    assert "use find_items" in tool("NO-SUCH-ITEM")["error"]


def test_forecast_names_its_pattern_and_method_and_computes_cover(stock):
    sold = [m.batch.item_id for m in stock.ledger if m.kind is MovementType.SALE]
    item_id = Counter(sold).most_common(1)[0][0]
    forecast = tools.forecast_item(item_id, weeks_of_history=8)
    assert forecast["forecast_units_per_week"] > 0
    assert forecast["demand_pattern"] in {"smooth", "erratic", "intermittent", "lumpy", "no_demand"}
    assert forecast["method"]
    assert len(forecast["weekly_sales"]) == 8
    assert forecast["weeks_of_cover"] == pytest.approx(
        forecast["units_that_can_be_sold"] / forecast["forecast_units_per_week"], abs=0.1
    )


def test_trace_finds_the_recalled_batch_however_its_number_is_typed(stock, recall):
    traced = tools.trace_batch_number(" az 4021 ")
    (match,) = traced["matches"]
    assert match["chemists_supplied"] == len(recall.chemists) == 38
    assert sum(match["units_on_hand_by_location"].values()) == recall.units_on_hand
    assert {r["party_id"] for r in match["recipients"]} == recall.chemists
    assert all(r["drug_licence_no"] for r in match["recipients"])


def test_trace_says_so_when_a_batch_was_never_held(stock):
    assert (
        "no batch numbered 'ZZ0000' has ever been held"
        in tools.trace_batch_number("ZZ0000")["error"]
    )


@pytest.fixture
def recorded(stock, recall, tmp_path):
    """A records database in which the AZ4021 notice was received and the batch blocked."""
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
    with use(stock, records=path):
        yield path, notice


def test_recall_status_reports_the_block_the_clock_and_what_is_outstanding(recorded, recall):
    status = tools.recall_status("az 4021")
    json.dumps(status)
    (notice,) = status["notices"]
    assert (notice["reference"], notice["recall_class"]) == ("RN/2026/014", "I")
    assert notice["received"] == "12/02/2026 09:15 IST"
    (batch,) = notice["blocked_batches"]
    assert batch["batch_no"] == "AZ4021"
    assert [(h["blocked"], h["in_force"]) for h in batch["holds"]] == [
        ("12/02/2026 09:15 IST", True)
    ]
    assert batch["deadlines"][0] == {
        "deadline": "stop sale",
        "due": "13/02/2026 09:15 IST",
        "state": "met",
    }
    assert batch["deadlines"][1]["state"] == "overdue"
    assert (batch["chemists_supplied"], batch["units_supplied"]) == (38, 790)
    assert (batch["units_recovered"], batch["units_outstanding"]) == (0, 790)
    assert batch["units_on_hand_when_notice_arrived"] == recall.units_on_hand
    assert batch["units_sold_after_notice"] == 0
    assert batch["chemists_still_holding"] == 38
    assert len(batch["largest_outstanding"]) == 10
    assert notice["raised_for_review"] == []


def test_recall_status_shows_a_lifted_block(recorded):
    path, notice = recorded
    with RecordStore(path) as store:
        (hold,) = list(store.hold_log())
        release_hold(
            store,
            hold.id,
            at=notice.received_at + timedelta(hours=2),
            reason="notice withdrawn",
            released_by="pharmacist",
        )
    (held,) = tools.recall_status("AZ4021")["notices"][0]["blocked_batches"][0]["holds"]
    assert held["in_force"] is False
    assert held["released"] == "12/02/2026 11:15 IST by pharmacist: notice withdrawn"


def test_recall_status_says_when_no_notice_was_recorded(recorded):
    assert tools.recall_status("AZ9999") == {
        "batch_no": "AZ9999",
        "notices": [],
        "note": "no recall notice has been recorded for this batch number",
    }


def test_recall_status_without_records_says_it_cannot_check(stock, tmp_path):
    assert "no recall records are available" in tools.recall_status("AZ4021")["error"]
    with use(stock, records=tmp_path / "missing.sqlite"):
        assert "no recall records are available" in tools.recall_status("AZ4021")["error"]
    assert not (tmp_path / "missing.sqlite").exists(), "checking must not create a database"


START_DAY = date(2026, 1, 1)
"""The first day the shared simulated stockist traded (see conftest)."""


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


@pytest.fixture
def ceilings(stock, tmp_path):
    """One ceiling, five rupees below one busy scheduled brand's MRP, from the first day."""
    item = most_sold_scheduled_item(stock)
    limit = item.mrp - Decimal(5)
    ceiling = (limit / (1 + item.gst_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    price = CeilingPrice(item.molecule, item.strength, item.unit, ceiling, START_DAY, "TEST/NPPA/1")
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        store.save_ceiling(price)
    with use(stock, records=path):
        yield item, price


def test_price_guard_blocks_every_batch_on_hand_above_the_ceiling_and_totals_exposure(
    stock, ceilings
):
    item, price = ceilings
    summary = tools.price_guard_summary(limit=50)
    json.dumps(summary)
    limit = price.max_retail_price(item.gst_rate)
    over = {
        other.id: other.mrp - limit
        for other in stock.items.values()
        if (other.molecule, other.strength, other.unit) == (item.molecule, item.strength, item.unit)
        and other.mrp > limit
    }
    assert item.id in over
    on_hand = {key for (key, _), units in stock.ledger.balances().items() if units > 0}
    expected_blocked = {(key.item_id, key.batch_no) for key in on_hand if key.item_id in over}
    blocked = {(b["batch_no"], b["brand"]) for b in summary["blocked"]}
    assert len(blocked) == summary["blocked_batches_on_hand"] == len(expected_blocked)
    assert all(
        b["verdict"] == "block" and b["notification"] == "TEST/NPPA/1" for b in summary["blocked"]
    )

    sales = [
        m
        for m in stock.ledger
        if m.kind is MovementType.SALE
        and m.batch.item_id in over
        and not stock.ledger.is_reversed(m.id)
    ]
    exposure = summary["exposure"]
    assert exposure["units_sold_above_allowed_price"] == -sum(m.qty for m in sales)
    expected = sum(-m.qty * over[m.batch.item_id] for m in sales)
    assert exposure["overcharge"]["rupees"] == pytest.approx(float(expected))
    assert exposure["interest"]["rupees"] > 0
    assert exposure["by_cause"][0]["cause"] == "printed MRP above the ceiling price in force"
    assert "not legal advice" in summary["note"]


def test_price_guard_lists_scheduled_items_with_no_ceiling_to_check_by_hand(stock, ceilings):
    item, _ = ceilings
    summary = tools.price_guard_summary(limit=2)
    assert summary["items_with_no_ceiling_on_record"] > 2
    assert len(summary["check_by_hand"]) == 2
    assert item.id not in {entry["item_id"] for entry in summary["check_by_hand"]}
    assert len(summary["blocked"]) <= 2


def test_check_item_prices_shows_the_ceiling_and_the_verdict_for_each_batch(stock, ceilings):
    item, price = ceilings
    checked = tools.check_item_prices(item.id)
    assert checked["price_controlled"] is True
    assert checked["ceiling_in_force"]["notification"] == "TEST/NPPA/1"
    assert checked["ceiling_in_force"]["highest_mrp_allowed"]["rupees"] == float(
        price.max_retail_price(item.gst_rate)
    )
    assert checked["batches_on_hand"]
    assert {b["verdict"] for b in checked["batches_on_hand"]} == {"block"}


def test_check_item_prices_allows_an_item_with_no_price_control(stock, ceilings):
    free = next(i for i in stock.items.values() if not i.dpco_scheduled)
    checked = tools.check_item_prices(free.id)
    assert (checked["price_controlled"], checked["ceiling_in_force"]) == (False, None)
    assert {b["verdict"] for b in checked["batches_on_hand"]} <= {"allowed"}
    assert "use find_items" in tools.check_item_prices("NO-SUCH-ITEM")["error"]


def test_price_tools_still_check_when_no_ceiling_price_is_on_record(stock, tmp_path):
    """No ceiling on record is a reason to check scheduled items by hand, not to stop checking."""
    empty = tmp_path / "records.sqlite"
    RecordStore(empty).close()
    on_hand = {key.item_id for (key, _), units in stock.ledger.balances().items() if units > 0}
    scheduled = {i for i in on_hand if stock.items[i].dpco_scheduled}
    for records in (None, empty):
        with use(stock, records=records):
            summary = tools.price_guard_summary(limit=50)
            assert "error" not in summary
            assert summary["ceiling_prices_on_record"] == 0
            assert summary["blocked_batches_on_hand"] == 0
            assert summary["items_with_no_ceiling_on_record"] == len(scheduled)
            checked = tools.check_item_prices(next(iter(scheduled)))
            assert {b["verdict"] for b in checked["batches_on_hand"]} == {"warn"}


def test_price_tools_report_records_that_cannot_be_read_instead_of_failing(stock, tmp_path):
    not_records = tmp_path / "notes.sqlite"
    not_records.write_text("not a database", encoding="utf-8")
    with use(stock, records=not_records):
        assert "cannot be read" in tools.price_guard_summary()["error"]
        assert "cannot be read" in tools.check_item_prices(next(iter(stock.items)))["error"]
        assert "cannot be read" in tools.recall_status("AZ4021")["error"]


def test_rule65_check_finds_complete_records_in_the_simulated_books(stock):
    checked = tools.rule65_records_check()
    json.dumps(checked)
    sales = {m.document_ref for m in stock.ledger if m.kind is MovementType.SALE}
    assert checked["complete"] is True
    assert checked["sale_memos"] == len(sales)
    assert checked["gaps_by_kind"] == [] and checked["examples"] == []
    assert checked["memos_must_be_kept_from"] == "2023-04-01"
    assert "signature" in checked["not_checked"]


def test_rule65_check_names_the_buyer_whose_address_is_missing(stock):
    buyer = next(m.party_id for m in stock.ledger if m.kind is MovementType.SALE)
    parties = stock.parties | {buyer: replace(stock.parties[buyer], address=None)}
    with use(replace(stock, parties=parties)):
        checked = tools.rule65_records_check(limit=2)
    assert checked["complete"] is False
    (kind,) = checked["gaps_by_kind"]
    assert kind["gap"] == "buyer's address missing"
    assert kind["bills"] >= 1
    assert len(checked["examples"]) <= 2
    assert checked["examples"][0]["party"] == stock.parties[buyer].name


PANTOP = Item(
    id="P10",
    company_id="C01",
    brand="Pantop 40",
    molecule="Pantoprazole",
    strength="40 mg",
    unit="strip of 10 tablets",
    hsn="3004",
    gst_rate=Decimal("0.05"),
    mrp=Decimal(100),
    dpco_scheduled=True,
)


def small_stock(items, sales, today=date(2026, 3, 1)):
    """A few batches of scheduled items, each bought in 2025 and sold on the given days."""
    batches, movements = {}, []
    for item in items:
        key = BatchKey(item.company_id, item.id, f"B{item.id}", date(2028, 3, 31))
        batches[key] = Batch(key=key, manufactured=date(2025, 1, 1), mrp=item.mrp)
        when = ist_datetime(date(2025, 1, 10), time(10))
        movements.append(
            StockMovement(f"P-{item.id}", when, MovementType.PURCHASE, key, "GODOWN", 500, "PO")
        )
    for number, (item, day, units) in enumerate(sales):
        key = next(k for k in batches if k.item_id == item.id)
        when = ist_datetime(day, time(11))
        movements.append(
            StockMovement(
                f"S{number}", when, MovementType.SALE, key, "GODOWN", -units, f"INV{number}"
            )
        )
    return StockData(
        parties={},
        items={item.id: item for item in items},
        batches=batches,
        ledger=Ledger(movements),
        locations=(Location(id="GODOWN", name="GODOWN"),),
        today=today,
    )


def test_items_to_check_by_hand_are_counted_by_item_not_by_brand_name():
    other_pack = replace(PANTOP, id="P15", unit="strip of 15 tablets")
    with use(small_stock([PANTOP, other_pack], sales=[])):
        summary = tools.price_guard_summary()
    assert summary["items_with_no_ceiling_on_record"] == 2
    assert {entry["item_id"] for entry in summary["check_by_hand"]} == {"P10", "P15"}


def test_a_batch_overcharged_under_two_notifications_names_both(tmp_path):
    older = CeilingPrice(
        "Pantoprazole", "40 mg", "strip of 10 tablets", Decimal(80), date(2023, 4, 1), "N/A"
    )
    newer = replace(older, ceiling=Decimal(70), effective_from=date(2026, 1, 1), reference="N/B")
    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        store.save_ceiling(older)
        store.save_ceiling(newer)
    sales = [(PANTOP, date(2025, 6, 1), 100), (PANTOP, date(2026, 2, 1), 5)]
    with use(small_stock([PANTOP], sales), records=str(path)):
        (largest,) = tools.price_guard_summary()["exposure"]["largest_batches"]
    assert largest["units"] == 105
    assert largest["allowed_price_set_by"] == ["N/A", "N/B"]


def test_forecast_cover_leaves_out_blocked_and_expired_stock(simulated, tmp_path):
    data, recall = simulated
    item_id = recall.batch.item_id
    sellable = {location.id for location in data.locations if location.sellable}

    def can_be_sold(today, held):
        return sum(
            units
            for (key, location), units in data.ledger.balances().items()
            if key.item_id == item_id
            and location in sellable
            and key.expiry > today
            and key not in held
        )

    path = tmp_path / "records.sqlite"
    with RecordStore(path) as store:
        hold = store.hold_log().place(
            recall.batch,
            BatchStatus.BLOCKED,
            at=ist_datetime(recall.notice_date, time(9, 15)),
            reason="recall",
            reference="RN/TEST",
            placed_by="system",
        )
        store.save_hold(hold)
    with use(data, records=str(path)):
        blocked = tools.forecast_item(item_id)["units_that_can_be_sold"]
    assert blocked == can_be_sold(data.today, {recall.batch}) < can_be_sold(data.today, set())

    on_hand = [k for (k, _), u in data.ledger.balances().items() if k.item_id == item_id and u > 0]
    later = min(key.expiry for key in on_hand)
    with use(replace(data, today=later)):
        assert tools.forecast_item(item_id)["units_that_can_be_sold"] == can_be_sold(later, set())


def test_claims_summary_values_open_windows_and_ages_claims_not_yet_credited(stock, tmp_path):
    path = tmp_path / "records.sqlite"
    companies = sorted({item.company_id for item in stock.items.values()})
    terms = [ReturnTerms(c, 180, 30, Decimal(90), date(2024, 1, 1), "letter") for c in companies]
    with RecordStore(path) as store:
        for entry in terms:
            store.save_terms(entry)
    with use(stock, records=path):
        summary = tools.claims_summary(limit=3)
    claimable = [
        w
        for w in windows_for(stock, TermsTable(terms), [])
        if w.state in (WindowState.OPEN, WindowState.CLOSING)
    ]
    assert summary["claim_now"]["batches"] == len(claimable) > 0
    assert summary["claim_now"]["value"]["rupees"] == float(
        sum((w.claim_value or Decimal(0) for w in claimable), Decimal(0))
    )
    assert len(summary["batches"]) == min(3, len(claimable))
    assert summary["open_claims"]["claims"] == 0
    assert summary["companies_without_terms"] == []
    json.dumps(summary)


def test_claims_summary_without_terms_says_so(stock, tmp_path):
    with use(stock, records=tmp_path / "missing.sqlite"):
        assert "no records database" in tools.claims_summary()["error"]
    path = tmp_path / "records.sqlite"
    RecordStore(path).close()
    with use(stock, records=path):
        assert "no return terms" in tools.claims_summary()["error"]


def test_order_suggestions_total_what_needs_ordering_and_filter_by_company(stock):
    everything = tools.order_suggestions(limit=50)
    assert everything["items_to_order"] > 0
    listed = everything["items"]
    assert all(entry["order_units"] > 0 for entry in listed)
    company = listed[0]["company_id"]
    one = tools.order_suggestions(company_id=company.lower(), limit=50)
    assert {entry["company_id"] for entry in one["items"]} == {company}
    per_company = {entry["company_id"]: entry["items"] for entry in everything["companies"]}
    assert one["items_to_order"] == per_company[company]
    assert one["companies"][0]["company_id"] == company
    json.dumps(everything)
