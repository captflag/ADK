"""The recall drill: a capability test with a hard pass mark.

The drill takes a simulated stockist, seeds the recalled batch AZ4021 and three
batches built to be mistaken for it, delivers the manufacturer's Class I
notice, and requires Batchward to:

- block the recalled batch within the stop-sale deadline, and block nothing else;
- raise every look-alike batch for a person to review;
- find every chemist the batch was supplied to, and no one else;
- account for every unit received;
- record no sale after the notice;
- produce the report the pharmacist signs, naming each chemist's drug licence;
- do the matching, blocking and reporting within two minutes of computing time.

Chemists then send the batch back — most promptly, some late, some never — so
the report has something real to reconcile. How quickly chemists respond is
reported, but it is not something Batchward can pass or fail on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

from batchward.compliance.recall import Recall, open_recall
from batchward.compliance.recall_report import DeadlineState, RecallReport, recall_report
from batchward.core.holds import HoldLog
from batchward.core.models import BatchKey
from batchward.reporting.recall import format_moment, render_recall_report
from batchward.sim.business import Business
from batchward.sim.scenarios import (
    RecallReturns,
    RecallScenario,
    recall_notice,
    seed_recall,
    seed_recall_look_alikes,
    seed_recall_returns,
)

TIME_LIMIT = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class DrillCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DrillResult:
    scenario: RecallScenario
    look_alikes: tuple[BatchKey, ...]
    recall: Recall
    returns: RecallReturns
    report: RecallReport
    report_text: str
    seconds_to_block: float
    seconds_to_report: float
    checks: tuple[DrillCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def run_recall_drill(
    business: Business, *, report_after: timedelta = timedelta(days=10)
) -> DrillResult:
    """Run the drill against ``business``, which gains the recall's batches and movements."""
    scenario = seed_recall(business)
    look_alikes = seed_recall_look_alikes(business, scenario)
    notice = recall_notice(business, scenario)
    items = {item.id: item for item in business.catalogue.items}
    parties = {party.id: party for party in (*business.catalogue.companies, *business.chemists)}
    holds = HoldLog()

    started = time.perf_counter()
    recall = open_recall(
        notice,
        ledger=business.ledger,
        items=items,
        parties=parties,
        holds=holds,
        at=notice.received_at,
        batches=business.batches,
    )
    seconds_to_block = time.perf_counter() - started

    returns = seed_recall_returns(business, scenario, notice_received=notice.received_at)

    started = time.perf_counter()
    report = recall_report(
        notice,
        scenario.batch,
        ledger=business.ledger,
        holds=holds,
        as_of=notice.received_at + report_after,
    )
    text = render_recall_report(report, items=items, parties=parties)
    seconds_to_report = time.perf_counter() - started

    licences = [parties[chemist].drug_licence_no or "" for chemist in scenario.chemists]
    others_blocked = set(recall.match.exact) - {scenario.batch}
    reviewed = {candidate.batch for candidate in recall.match.review}
    found = {chemist.party_id for chemist in report.chemists if chemist.supplied}
    seconds = seconds_to_block + seconds_to_report
    stop_sale = report.stop_sale

    checks = (
        DrillCheck(
            "The recalled batch is blocked within the stop-sale deadline",
            scenario.batch in recall.match.exact
            and stop_sale is not None
            and stop_sale.state is DeadlineState.MET,
            "not blocked"
            if report.blocked_at is None or stop_sale is None
            else (
                f"blocked {format_moment(report.blocked_at)}, "
                f"deadline {format_moment(stop_sale.due)}"
            ),
        ),
        DrillCheck(
            "Nothing else is blocked",
            not others_blocked,
            f"{len(others_blocked)} other batches blocked",
        ),
        DrillCheck(
            "Every look-alike batch is raised for review",
            set(look_alikes) <= reviewed,
            f"{len(set(look_alikes) & reviewed)} of {len(look_alikes)} raised",
        ),
        DrillCheck(
            "Every chemist supplied is found, and no one else",
            found == scenario.chemists,
            f"{len(found & scenario.chemists)} of {len(scenario.chemists)} found, "
            f"{len(found - scenario.chemists)} listed wrongly",
        ),
        DrillCheck(
            "Every unit received is accounted for",
            report.received == scenario.units_received
            and report.supplied == scenario.units_supplied
            and sum(report.on_hand_at_notice.values()) == scenario.units_on_hand
            and report.untraceable_at_notice == 0,
            f"{report.received} received = {report.supplied} supplied "
            f"+ {sum(report.on_hand_at_notice.values())} on hand",
        ),
        DrillCheck(
            "No sale after the notice",
            report.sold_after_notice == 0,
            f"{report.sold_after_notice} units sold after the notice",
        ),
        DrillCheck(
            "The report names every chemist's drug licence",
            all(licence and licence in text for licence in licences),
            f"{sum(1 for licence in licences if licence and licence in text)} of "
            f"{len(licences)} licences",
        ),
        DrillCheck(
            f"Matching, blocking and reporting take under {TIME_LIMIT.seconds // 60} minutes",
            seconds < TIME_LIMIT.total_seconds(),
            f"{seconds:.2f} seconds",
        ),
    )
    return DrillResult(
        scenario=scenario,
        look_alikes=look_alikes,
        recall=recall,
        returns=returns,
        report=report,
        report_text=text,
        seconds_to_block=seconds_to_block,
        seconds_to_report=seconds_to_report,
        checks=checks,
    )
