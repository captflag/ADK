"""A claim on one company: what is sent back, from where, and the credit asked for.

An expiry claim is drafted from the batches whose windows are open (ADR 0018),
and a breakage claim from what chemists sent back broken (ADR 0025); both are
written the same way, and a breakage claim's number begins BR. Each line is
units of one batch taken from one place, valued at the batch's cost times the
share the company credits, with GST at the item's rate. Expired goods returned
this way are credited under section 34 of the CGST Act, and the stockist reverses
the input credit it took; stock bought before the rate cut of 22 September 2025
carries the rate on its original bill, which the claim flags for checking.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from batchward.analysis.costs import PAISA
from batchward.bridge.marg_layout import format_expiry
from batchward.claims.windows import ClaimWindow, WindowState
from batchward.core.approvals import Posting, digest
from batchward.core.models import BatchKey, Item, Party
from batchward.reporting.inr import format_inr

KIND = "expiry claim"
GST_RATE_CUT = date(2025, 9, 22)
"""Most medicines moved from 12% to 5% GST on this day."""


@dataclass(frozen=True, slots=True)
class ClaimLine:
    batch: BatchKey
    location_id: str
    units: int
    rate: Decimal
    """The batch's cost per unit."""
    credit_percent: Decimal
    gst_rate: Decimal
    """A fraction, as on the item."""
    bought_on: date | None = None
    """When the batch was first bought, to flag stock bought before the GST rate cut."""

    @property
    def taxable_value(self) -> Decimal:
        return (self.rate * self.units * self.credit_percent / 100).quantize(PAISA)

    @property
    def tax_amount(self) -> Decimal:
        return (self.taxable_value * self.gst_rate).quantize(PAISA)

    @property
    def check_gst_rate(self) -> bool:
        return self.bought_on is not None and self.bought_on < GST_RATE_CUT


@dataclass(frozen=True, slots=True)
class Claim:
    number: str
    company_id: str
    made_on: date
    lines: tuple[ClaimLine, ...]

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("a claim needs at least one line")
        if any(line.batch.company_id != self.company_id for line in self.lines):
            raise ValueError(f"claim {self.number} includes another company's batch")

    @property
    def units(self) -> int:
        return sum(line.units for line in self.lines)

    @property
    def taxable_value(self) -> Decimal:
        return sum((line.taxable_value for line in self.lines), Decimal(0))

    @property
    def tax_amount(self) -> Decimal:
        return sum((line.tax_amount for line in self.lines), Decimal(0))

    @property
    def total(self) -> Decimal:
        return self.taxable_value + self.tax_amount

    @property
    def batches(self) -> int:
        return len({line.batch for line in self.lines})


@dataclass(frozen=True, slots=True)
class Settlement:
    """A company's credit note against a claim."""

    claim: str
    credit_note: str
    amount: Decimal
    received_on: date
    recorded_by: str

    def __post_init__(self) -> None:
        if not self.credit_note.strip() or not self.recorded_by.strip():
            raise ValueError("a settlement needs the credit note number and who recorded it")
        if self.amount <= 0:
            raise ValueError("a credit note must be for more than nothing")


def claim_number(company_id: str, on: date, *, prefix: str = "CL") -> str:
    """One claim per company per day, so drafting it again the same day drafts the same claim."""
    return f"{prefix}/{company_id}/{on:%y%m%d}"


def draft_claim(
    windows: Iterable[ClaimWindow],
    items: Mapping[str, Item],
    *,
    company_id: str,
    on: date,
    bought_on: Mapping[BatchKey, date] | None = None,
) -> Claim | None:
    """A claim for everything of one company's that can be claimed today, or None.

    Only open windows are claimed, and only batches with a cost on record, since
    the claim is valued at cost.
    """
    lines = []
    for window in windows:
        if window.company_id != company_id or window.terms is None or window.cost is None:
            continue
        if window.state not in (WindowState.OPEN, WindowState.CLOSING):
            continue
        for location_id, units in window.by_location:
            lines.append(
                ClaimLine(
                    batch=window.batch,
                    location_id=location_id,
                    units=units,
                    rate=window.cost,
                    credit_percent=window.terms.credit_percent,
                    gst_rate=items[window.batch.item_id].gst_rate,
                    bought_on=(bought_on or {}).get(window.batch),
                )
            )
    if not lines:
        return None
    lines.sort(key=lambda line: (line.batch.item_id, line.batch.expiry, line.batch.batch_no))
    return Claim(claim_number(company_id, on), company_id, on, tuple(lines))


CLAIM_COLUMNS = (
    "CLAIM NO", "DATE", "PRODUCT CODE", "PRODUCT", "BATCH", "EXPIRY", "UNITS", "RATE",
    "CREDIT %", "TAXABLE", "GST %", "GST", "TOTAL",
)  # fmt: skip
RETURN_COLUMNS = (
    "SUPPLIER GSTIN", "SUPPLIER", "RETURN NO", "RETURN DATE", "PRODUCT CODE", "PRODUCT",
    "BATCH", "EXPIRY", "GODOWN", "QTY", "RATE", "CREDIT %", "GST %", "TAXABLE", "GST AMOUNT",
)  # fmt: skip


def claim_posting(
    claim: Claim, company: Party, items: Mapping[str, Item], *, kind: str = KIND
) -> Posting:
    """What approving a claim writes: the claim sheet, its letter, and Marg's return voucher.

    ``kind`` is what the claim is for, which its letter says. Marg publishes no
    import layout, so the return voucher's columns are an assumption, like the
    purchase voucher's (ADR 0016).
    """
    stem = claim.number.replace("/", "-")
    files = {
        f"{stem}.claim.csv": _sheet(claim, items),
        f"{stem}.claim-letter.txt": _letter(claim, company, kind),
        f"{stem}.marg-purchase-return.csv": _return_voucher(claim, company, items),
    }
    summary = (
        f"{claim.number} on {company.name}: {claim.units} units of "
        f"{claim.batches} {'batch' if claim.batches == 1 else 'batches'}, "
        f"{format_inr(claim.total, paise=True)}"
    )
    return Posting(
        approval_id=f"claim:{claim.number}",
        files=files,
        summary=summary,
        digest=digest("".join(files[name] for name in sorted(files))),
    )


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _sheet(claim: Claim, items: Mapping[str, Item]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CLAIM_COLUMNS)
    for line in claim.lines:
        item = items[line.batch.item_id]
        writer.writerow(
            (
                claim.number,
                f"{claim.made_on:%d/%m/%Y}",
                item.id,
                item.brand,
                line.batch.batch_no,
                format_expiry(line.batch.expiry),
                line.units,
                f"{line.rate:.2f}",
                _percent(line.credit_percent),
                f"{line.taxable_value:.2f}",
                _percent(line.gst_rate * 100),
                f"{line.tax_amount:.2f}",
                f"{line.taxable_value + line.tax_amount:.2f}",
            )
        )
    return out.getvalue()


def _letter(claim: Claim, company: Party, kind: str = KIND) -> str:
    breakage = kind != KIND
    lines = [
        "BREAKAGE CLAIM" if breakage else "EXPIRY CLAIM",
        "Draft for the stockist to sign and send. Nothing has been sent.",
        "",
        f"{'To':12}{company.name}",
        f"{'GSTIN':12}{company.gstin or '-'}",
        f"{'Claim no':12}{claim.number}",
        f"{'Date':12}{claim.made_on:%d/%m/%Y}",
        "",
        f"We return {claim.units} units of {claim.batches} "
        f"{'batch' if claim.batches == 1 else 'batches'} "
        + (
            "that chemists sent back broken or damaged,\nunder your breakage terms, "
            if breakage
            else "for expiry under your return terms, "
        )
        + "as listed in the claim sheet.",
        f"{'Taxable value':24}{format_inr(claim.taxable_value, paise=True):>16}",
        f"{'GST':24}{format_inr(claim.tax_amount, paise=True):>16}",
        f"{'Credit asked for':24}{format_inr(claim.total, paise=True):>16}",
        "",
        "Please issue a credit note for this amount under section 34 of the CGST Act. We",
        "will reverse the input tax credit taken on these goods when it is received.",
    ]
    flagged = sorted({line.batch.batch_no for line in claim.lines if line.check_gst_rate})
    if flagged:
        lines += [
            "",
            "Bought before the GST rate cut of 22/09/2025, so the rate on the original bill",
            f"applies; check it before sending: {', '.join(flagged)}.",
        ]
    lines += ["", "Signature: ______________________"]
    return "\n".join(lines) + "\n"


def _return_voucher(claim: Claim, company: Party, items: Mapping[str, Item]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(RETURN_COLUMNS)
    for line in claim.lines:
        item = items[line.batch.item_id]
        writer.writerow(
            (
                company.gstin or "",
                company.name,
                claim.number,
                f"{claim.made_on:%d/%m/%Y}",
                item.id,
                item.brand,
                line.batch.batch_no,
                format_expiry(line.batch.expiry),
                line.location_id,
                line.units,
                f"{line.rate:.2f}",
                _percent(line.credit_percent),
                _percent(line.gst_rate * 100),
                f"{line.taxable_value:.2f}",
                f"{line.tax_amount:.2f}",
            )
        )
    return out.getvalue()


def settled(claim_total: Decimal, settlements: Iterable[Settlement]) -> tuple[Decimal, Decimal]:
    """What the company has credited so far, and what is still owed on the claim."""
    credited = sum((s.amount for s in settlements), Decimal(0))
    return credited, max(Decimal(0), claim_total - credited)
