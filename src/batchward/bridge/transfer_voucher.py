"""The transfer voucher Marg imports to move blocked stock out of the selling godowns.

Batchward never writes Marg's tables (ADR 0006). A transfer is posted by writing
a file for Marg's import, one row out of the godown it leaves and one row into
the godown it enters, which is how Marg records a transfer in its own ``DIS``
table: ``TO`` for the side that goes out and ``TI`` for the side that comes in.
Marg publishes no import layout, so the columns here are an assumption, like the
purchase voucher's (ADR 0016).

The voucher is written only once a person approves the transfer, and changes
nothing until somebody imports it (ADR 0029).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping

from batchward.bridge.marg_layout import format_expiry
from batchward.compliance.quarantine import Transfer
from batchward.core.models import Item

TRANSFER_COLUMNS = (
    "VNO",
    "LINE",
    "VTYPE",
    "VDATE",
    "PRODUCT CODE",
    "PRODUCT",
    "BATCH",
    "EXPIRY",
    "QTY",
    "GODOWN",
    "REASON",
)
"""Assumed columns for Marg's stock transfer import (ADR 0006)."""

OUT, IN = "TO", "TI"
"""Marg's voucher types for the two sides of a transfer."""


def render_transfer(transfer: Transfer, items: Mapping[str, Item]) -> str:
    """The transfer as rows for Marg's import: each move leaves one godown and enters another."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(TRANSFER_COLUMNS)
    line = 0
    for move in transfer.moves:
        item = items.get(move.batch.item_id)
        for kind, godown in ((OUT, move.from_location), (IN, move.to_location)):
            line += 1
            writer.writerow(
                [
                    transfer.number,
                    line,
                    kind,
                    f"{transfer.on:%d/%m/%Y}",
                    move.batch.item_id,
                    item.brand if item else "",
                    move.batch.batch_no,
                    format_expiry(move.batch.expiry),
                    move.units,
                    godown,
                    move.reason,
                ]
            )
    return out.getvalue()
