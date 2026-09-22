"""Purchase orders: what the stockist asked a company to send.

An order is the first of the three documents a delivery is matched against: what
was ordered, what the invoice bills, and what the godown counted (ADR 0016). A
company may deliver an order over several days, on several invoices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class OrderLine:
    item_id: str
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("an order line must ask for at least one unit")


@dataclass(frozen=True, slots=True)
class PurchaseOrder:
    number: str
    company_id: str
    placed_on: date
    lines: tuple[OrderLine, ...]

    def __post_init__(self) -> None:
        if not self.number.strip():
            raise ValueError("a purchase order needs a number")
        items = [line.item_id for line in self.lines]
        if len(items) != len(set(items)):
            raise ValueError(f"order {self.number} lists an item twice")

    def ordered(self, item_id: str) -> int:
        return sum(line.quantity for line in self.lines if line.item_id == item_id)

    def to_json(self) -> str:
        return json.dumps(
            {
                "number": self.number,
                "company_id": self.company_id,
                "placed_on": self.placed_on.isoformat(),
                "lines": [{"item_id": o.item_id, "quantity": o.quantity} for o in self.lines],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> PurchaseOrder:
        """An order written by ``to_json``; ValueError if the text is not one."""
        try:
            data = json.loads(text)
            return cls(
                number=data["number"],
                company_id=data["company_id"],
                placed_on=date.fromisoformat(data["placed_on"]),
                lines=tuple(
                    OrderLine(line["item_id"], int(line["quantity"])) for line in data["lines"]
                ),
            )
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
            raise ValueError(f"not a purchase order: {error}") from error
