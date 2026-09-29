"""Blocked stock moved out of the godowns stock is billed from, so billing cannot pick it.

A hold stops Batchward selling a batch and stops nothing else. Marg does the
billing (ADR 0006) and knows nothing of Batchward's holds, so a recalled batch
sitting in the main godown is one keystroke away from going out on a bill. What
Marg does understand is a transfer between godowns, which is how a stockist has
always taken stock out of circulation.

So a block is carried into billing as a transfer: every unit of a held batch
still in a godown stock is sold from moves to a quarantine godown, and every
unit whose hold has been lifted moves back to the godown it came from. Nothing
is written into Marg's tables: the voucher is a file for Marg's import, made
only once a person approves it (ADR 0005), and it does nothing whatever until
somebody imports it - which is why `recall quarantine list` goes on reporting
the gap until the stock has moved (ADR 0029).

Units that came back from a chemist after the transfer was made are not in it:
the next transfer picks them up, which is why the list is worth reading again.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from batchward.core.approvals import Posting, digest
from batchward.core.holds import HoldLog
from batchward.core.ledger import Ledger
from batchward.core.models import BatchKey, Item, Location

KIND = "quarantine transfer"
PREFIX = "QT"
GODOWN = "QUAR"
"""The godown blocked stock is moved to, unless another is given."""


class QuarantineError(ValueError):
    """A transfer that cannot be drafted, saying why."""


@dataclass(frozen=True, slots=True)
class Move:
    """Units of one batch leaving one godown for another."""

    batch: BatchKey
    from_location: str
    to_location: str
    units: int
    hold_id: str = ""
    """The hold that put the stock in quarantine, or whose release takes it out."""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.units <= 0:
            raise QuarantineError("a move must move at least one unit")
        if self.from_location == self.to_location:
            raise QuarantineError(f"a move must leave {self.from_location} for somewhere else")


@dataclass(frozen=True, slots=True)
class Transfer:
    """One day's moves into quarantine and back out of it."""

    number: str
    godown: str
    on: date
    into: tuple[Move, ...] = ()
    back: tuple[Move, ...] = ()
    stranded: tuple[Move, ...] = ()
    """Units sitting in quarantine with no hold and no record of where they came from."""

    @property
    def moves(self) -> tuple[Move, ...]:
        return self.into + self.back

    @property
    def units(self) -> int:
        return sum(move.units for move in self.moves)

    @property
    def batches(self) -> tuple[BatchKey, ...]:
        return tuple(dict.fromkeys(move.batch for move in self.moves))

    @property
    def empty(self) -> bool:
        return not self.moves

    def summary(self) -> str:
        parts = []
        if self.into:
            parts.append(f"{sum(m.units for m in self.into)} units to {self.godown}")
        if self.back:
            parts.append(f"{sum(m.units for m in self.back)} units back")
        held = f"{len(self.batches)} batch{'' if len(self.batches) == 1 else 'es'}"
        return f"{self.number}: {', '.join(parts) or 'nothing to move'} ({held})"


def transfer_number(on: date, *, prefix: str = PREFIX) -> str:
    """One transfer a day, so drafting it again the same day drafts the same one."""
    return f"{prefix}/{on:%y%m%d}"


def blocked_and_sellable(
    log: HoldLog,
    ledger: Ledger,
    locations: Iterable[Location],
    *,
    godown: str = GODOWN,
    at: datetime | None = None,
) -> tuple[Move, ...]:
    """Every unit of a held batch still sitting where stock is billed from."""
    sellable = {location.id for location in locations if location.sellable}
    found = []
    for (batch, location), units in ledger.balances().items():
        if units <= 0 or location == godown or location not in sellable:
            continue
        holds = log.active(batch, at)
        if not holds:
            continue
        hold = holds[0]
        found.append(
            Move(
                batch=batch,
                from_location=location,
                to_location=godown,
                units=units,
                hold_id=hold.id,
                reason=hold.reason,
            )
        )
    return tuple(sorted(found, key=_in_order))


def released_in_quarantine(
    log: HoldLog,
    ledger: Ledger,
    *,
    godown: str = GODOWN,
    moved: Sequence[Move] = (),
    at: datetime | None = None,
) -> tuple[tuple[Move, ...], tuple[Move, ...]]:
    """Units in quarantine whose hold is lifted, sent back where they came from.

    Where they came from is read from the moves already made. Units nothing was
    recorded for come back as stranded: a person decides where they belong,
    because guessing a godown would put recalled stock back on sale.
    """
    came_from: dict[BatchKey, list[Move]] = {}
    for move in moved:
        if move.to_location == godown:
            came_from.setdefault(move.batch, []).append(move)
    back, stranded = [], []
    for (batch, location), units in ledger.balances().items():
        if units <= 0 or location != godown or log.active(batch, at):
            continue
        left = units
        for earlier in reversed(came_from.get(batch, [])):
            if left <= 0:
                break
            units_back = min(left, earlier.units)
            back.append(
                Move(
                    batch=batch,
                    from_location=godown,
                    to_location=earlier.from_location,
                    units=units_back,
                    hold_id=earlier.hold_id,
                    reason="the hold on it has been lifted",
                )
            )
            left -= units_back
        if left > 0:
            stranded.append(
                Move(
                    batch=batch,
                    from_location=godown,
                    to_location="?",
                    units=left,
                    reason="nothing on record says which godown these units came from",
                )
            )
    return tuple(sorted(back, key=_out_order)), tuple(sorted(stranded, key=_out_order))


def draft_transfer(
    log: HoldLog,
    ledger: Ledger,
    locations: Iterable[Location],
    *,
    on: date,
    godown: str = GODOWN,
    moved: Sequence[Move] = (),
    at: datetime | None = None,
    prefix: str = PREFIX,
) -> Transfer:
    """Today's transfer: blocked stock out of the sellable godowns, released stock back."""
    listed = list(locations)
    known = {location.id: location for location in listed}
    if godown not in known or known[godown].sellable:
        raise QuarantineError(
            f"{godown} is not known to be a godown stock is never sold from, so moving blocked "
            f"stock into it would achieve nothing: name it in BATCHWARD_UNSELLABLE_LOCATIONS, "
            f"and make it a non-selling godown in Marg, before drawing up a transfer"
        )
    back, stranded = released_in_quarantine(log, ledger, godown=godown, moved=moved, at=at)
    return Transfer(
        number=transfer_number(on, prefix=prefix),
        godown=godown,
        on=on,
        into=blocked_and_sellable(log, ledger, listed, godown=godown, at=at),
        back=back,
        stranded=stranded,
    )


def transfer_posting(
    transfer: Transfer, items: Mapping[str, Item], *, kind: str = KIND, voucher: str = ""
) -> Posting:
    """What approving a transfer writes: the voucher for Marg's import, and its sheet."""
    if transfer.empty:
        raise QuarantineError("there is nothing to move")
    stem = transfer.number.replace("/", "-")
    files = {
        f"{stem}.marg-transfer.csv": voucher,
        f"{stem}.quarantine.txt": sheet(transfer, items),
    }
    return Posting(
        approval_id=f"quarantine:{transfer.number}",
        files=files,
        summary=transfer.summary(),
        digest=digest("".join(files[name] for name in sorted(files))),
    )


def sheet(transfer: Transfer, items: Mapping[str, Item]) -> str:
    """What is being moved and why, for the person approving it."""
    lines = [
        f"QUARANTINE TRANSFER {transfer.number}",
        f"Drawn up {transfer.on:%d/%m/%Y} for godown {transfer.godown}",
        "",
    ]
    sections = (("Out of the godowns stock is billed from", transfer.into), ("Back", transfer.back))
    for title, moves in sections:
        if not moves:
            continue
        lines.append(f"{title}:")
        for move in moves:
            brand = items[move.batch.item_id].brand if move.batch.item_id in items else "?"
            lines.append(
                f"  {move.units:>6}  {brand} batch {move.batch.batch_no}"
                f" expiry {move.batch.expiry:%m/%Y}"
                f"  {move.from_location} -> {move.to_location}"
                + (f"  ({move.reason})" if move.reason else "")
            )
        lines.append("")
    if transfer.stranded:
        lines.append("Left where it is, for a person to place:")
        for move in transfer.stranded:
            lines.append(f"  {move.units:>6}  batch {move.batch.batch_no}: {move.reason}")
        lines.append("")
    lines.append("Marg does not act on this until the transfer voucher is imported.")
    return "\n".join(lines)


def _in_order(move: Move) -> tuple:
    return (
        move.batch.company_id,
        move.batch.item_id,
        move.batch.batch_no,
        move.batch.expiry,
        move.from_location,
    )


def _out_order(move: Move) -> tuple:
    return (*_in_order(move), move.to_location)
