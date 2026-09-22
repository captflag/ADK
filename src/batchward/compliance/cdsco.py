"""CDSCO drug alert lists, read as recall notices.

Every month CDSCO publishes the drug samples State and Central laboratories
found not of standard quality, or spurious, as a table: the product, its batch
number, dates of manufacture and expiry, the manufacturer, and the result. A
stockist must check every row against what it holds. Each row here becomes a
recall notice, matched as strictly as any other (ADR 0004): only a row naming
the batch number, manufacturer, product and expiry that all agree blocks
anything; the rest are raised for review.

The lists give no recall class, so the person importing one chooses it. The
layout assumed is the published table copied to CSV (ADR 0014).
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.core.printed import expiry_month

_COLUMNS = {
    "sno": "serial",
    "slno": "serial",
    "nameofdrugs": "product",
    "nameofdrug": "product",
    "nameofdrugsmedicaldevicecosmetic": "product",
    "nameofdrugsmedicaldevicescosmetics": "product",
    "batchno": "batch_no",
    "batchnumber": "batch_no",
    "dateofmanufacture": "manufactured",
    "dateofexpiry": "expiry",
    "manufacturedby": "manufacturer",
    "nsqresult": "result",
    "reasonforrequest": "result",
    "result": "result",
}
"""Header text with everything but letters removed, for each column read."""
_REQUIRED = {
    "product": "Name of Drugs",
    "batch_no": "Batch No.",
    "expiry": "Date of Expiry",
    "manufacturer": "Manufactured By",
}


class AlertListError(ValueError):
    """An alert list that cannot be read, naming the line."""


@dataclass(frozen=True, slots=True)
class Alert:
    line: int
    """The line of the table it came from, counting the header as line 1."""
    serial: str
    """The row's own number in the list, or its line when the list has none."""
    product: str
    batch_no: str
    expiry: date | None
    """The last day of the expiry month, or None if the list's date cannot be read."""
    expiry_as_listed: str
    manufacturer: str
    result: str

    def notice(
        self, *, list_reference: str, received_at: datetime, recall_class: RecallClass
    ) -> RecallNotice:
        """This row as a recall notice. Its reference is the list's plus the row's number, so
        importing the same list again records nothing new."""
        return RecallNotice(
            reference=f"{list_reference} #{self.serial}",
            source="CDSCO drug alert" + (f": {self.result}" if self.result else ""),
            recall_class=recall_class,
            received_at=received_at,
            batch_no=self.batch_no,
            manufacturer=self.manufacturer,
            product=self.product,
            expiry=self.expiry,
        )


def read_alert_list(lines: Iterable[str]) -> list[Alert]:
    """The rows of an alert list in CSV, with a header naming its columns."""
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise AlertListError("the alert list is empty")
    positions: dict[str, int] = {}
    for index, title in enumerate(header):
        key = _COLUMNS.get(re.sub(r"[^a-z]", "", title.lower()))
        if key is not None:
            positions.setdefault(key, index)
    missing = [title for key, title in _REQUIRED.items() if key not in positions]
    if missing:
        raise AlertListError(f"the header has no column {', '.join(map(repr, missing))}")

    alerts = []
    for line, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        values = {
            key: " ".join(cells[index].split()) if index < len(cells) else ""
            for key, index in positions.items()
        }
        blank = [_REQUIRED[key] for key in _REQUIRED if not values[key]]
        if blank:
            raise AlertListError(f"line {line} has no {', '.join(map(repr, blank))}")
        alerts.append(
            Alert(
                line=line,
                serial=values.get("serial") or str(line),
                product=values["product"],
                batch_no=values["batch_no"],
                expiry=expiry_month(values["expiry"]),
                expiry_as_listed=values["expiry"],
                manufacturer=values["manufacturer"],
                result=values.get("result", ""),
            )
        )
    serials = [alert.serial for alert in alerts]
    repeated = sorted({serial for serial in serials if serials.count(serial) > 1})
    if repeated:
        raise AlertListError(f"the list numbers more than one row {repeated[0]}")
    return alerts
