"""The recall report a pharmacist signs and a Drugs Inspector reads.

Plain text, so it can be printed, attached to an email or pasted into a
message unchanged. It is a draft: the competent person checks and signs it
(ADR 0004), and nothing here sends it anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from batchward.compliance.recall_report import Deadline, DeadlineState, RecallReport
from batchward.core.clock import IST
from batchward.core.models import Item, Party
from batchward.reporting.inr import group_indian


def render_recall_report(
    report: RecallReport,
    *,
    items: Mapping[str, Item],
    parties: Mapping[str, Party],
) -> str:
    notice, batch = report.notice, report.batch
    item = items.get(batch.item_id)
    company = parties.get(batch.company_id)
    lines = [
        "RECALL: STOCK POSITION AND RECONCILIATION",
        f"As of {format_moment(report.as_of)}. Draft for the competent person to check and sign.",
        "",
        f"{'Notice':14}{notice.reference}, Class {notice.recall_class}, from {notice.source}",
        f"{'Received':14}{format_moment(notice.received_at)}",
        f"{'Product':14}"
        + (
            f"{item.brand} ({item.molecule} {item.strength}), {item.unit}"
            if item
            else batch.item_id
        ),
        f"{'Manufacturer':14}{company.name if company else batch.company_id}",
        f"{'Batch':14}{batch.batch_no}, expiry {batch.expiry:%m/%Y}",
        "",
        "Deadlines",
    ]
    for deadline in (report.stop_sale, report.completion):
        if deadline is not None:
            lines.append(_deadline(deadline))
    if report.stop_sale is None:
        blocked = format_moment(report.blocked_at) if report.blocked_at else "not blocked"
        lines.append(f"  {'Sale stopped':32}{blocked}")

    lines += [
        "",
        "Position when the notice arrived",
        f"  {'Received':38}{_n(report.received):>8}",
        f"  {f'Supplied to {len(_supplied(report))} chemists':38}{_n(report.supplied):>8}",
        f"  {'On hand':38}{_n(sum(report.on_hand_at_notice.values())):>8}"
        f"  {_locations(report.on_hand_at_notice)}",
        f"  {'Sold with no recorded buyer':38}{_n(report.untraceable_at_notice):>8}",
        "",
        "Recovery from chemists",
        f"  {'Chemist':40}{'Licence':18}{'Supplied':>9}{'Returned':>9}{'Pending':>9}  Bills",
    ]
    for chemist in report.chemists:
        party = parties.get(chemist.party_id)
        name = party.name if party else chemist.party_id
        licence = (party.drug_licence_no if party else None) or "-"
        # Names and licence numbers are never cut short in a report someone signs.
        lines.append(
            f"  {name:39} {licence:17} {_n(chemist.due):>9}"
            f"{_n(chemist.recovered):>9}{_n(chemist.outstanding):>9}  {', '.join(chemist.bills)}"
        )
    lines.append(
        f"  {'Total':58}{_n(sum(c.due for c in report.chemists)):>9}{_n(report.recovered):>9}"
        f"{_n(report.outstanding):>9}"
    )

    lines += [
        "",
        "Since the notice",
        f"  {'Recovered from chemists':38}{_n(report.recovered):>8}",
        f"  {'Returned to the company':38}{_n(report.returned_to_company):>8}",
        f"  {'Written off':38}{_n(report.written_off):>8}",
        f"  {'Sold after the notice':38}{_n(report.sold_after_notice):>8}",
        f"  {'On hand now':38}{_n(sum(report.on_hand.values())):>8}  {_locations(report.on_hand)}",
    ]
    if report.in_transit:
        lines.append(f"  {'Transferred, not yet arrived':38}{_n(report.in_transit):>8}")

    problems = _problems(report)
    lines += ["", "Needs attention" if problems else "Nothing needs attention."]
    lines += [f"  - {problem}" for problem in problems]
    return "\n".join(lines)


def _problems(report: RecallReport) -> list[str]:
    problems = []
    for m in report.sales_while_blocked:
        problems.append(
            f"sold {_n(-m.qty)} units on bill {m.document_ref} while the batch was blocked"
        )
    for m in report.sales_before_block:
        problems.append(
            f"sold {_n(-m.qty)} units on bill {m.document_ref} after the notice, before the block"
        )
    for m in report.sales_after_release:
        problems.append(
            f"sold {_n(-m.qty)} units on bill {m.document_ref} after the block was lifted"
        )
    for release in report.releases:
        problems.append(
            f"block {release.hold_id} was lifted {format_moment(release.at)} by "
            f"{release.released_by}: {release.reason}"
        )
    if report.untraceable:
        problems.append(
            f"{_n(report.untraceable)} units were sold with no recorded buyer and "
            "cannot be recalled by name"
        )
    if report.in_transit:
        problems.append(
            f"{_n(report.in_transit)} units were transferred out with no transfer in recorded"
        )
    for chemist in report.chemists:
        if chemist.excess:
            problems.append(
                f"{chemist.party_id} returned {_n(chemist.excess)} more units than the records "
                "show were supplied"
            )
    if report.outstanding and report.completion.state is DeadlineState.OVERDUE:
        pending = sum(1 for c in report.chemists if c.outstanding)
        problems.append(
            f"{_n(report.outstanding)} units are still with {pending} chemists after the "
            "recall deadline"
        )
    return problems


def _supplied(report: RecallReport) -> list[str]:
    return [c.party_id for c in report.chemists if c.supplied]


def _deadline(deadline: Deadline) -> str:
    detail = f"due {format_moment(deadline.due)}"
    if deadline.done_at is not None:
        detail += f", done {format_moment(deadline.done_at)}"
    return f"  {deadline.name.capitalize():22}{deadline.state.upper():10}{detail}"


def _locations(on_hand: Mapping[str, int]) -> str:
    return ", ".join(f"{location} {_n(units)}" for location, units in sorted(on_hand.items()))


def format_moment(moment: datetime) -> str:
    return f"{moment.astimezone(IST):%d/%m/%Y %H:%M} IST"


def _n(units: int) -> str:
    sign = "-" if units < 0 else ""
    return sign + group_indian(str(abs(units)))
