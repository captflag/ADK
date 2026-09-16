"""The append-only stock ledger (ADR 0001).

Stock levels are never stored as facts; they are derived from movements.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime

from batchward.core.models import BatchKey, StockMovement

type Position = tuple[BatchKey, str]
"""A batch at a location."""


class LedgerError(Exception):
    """A movement the ledger refuses to record."""


class DuplicateMovementError(LedgerError):
    pass


class UnknownMovementError(LedgerError, KeyError):
    pass


class InsufficientStockError(LedgerError):
    pass


class Ledger:
    def __init__(self, movements: Iterable[StockMovement] = ()) -> None:
        self._movements: list[StockMovement] = []
        self._by_id: dict[str, StockMovement] = {}
        self._by_batch: defaultdict[BatchKey, list[StockMovement]] = defaultdict(list)
        self._by_position: defaultdict[Position, list[StockMovement]] = defaultdict(list)
        self._current: defaultdict[Position, int] = defaultdict(int)
        self._latest_at: dict[Position, datetime] = {}
        for movement in movements:
            self.append(movement)

    def __len__(self) -> int:
        return len(self._movements)

    def __iter__(self) -> Iterator[StockMovement]:
        """Movements in the order they were appended."""
        return iter(self._movements)

    def get(self, movement_id: str) -> StockMovement:
        try:
            return self._by_id[movement_id]
        except KeyError:
            raise UnknownMovementError(movement_id) from None

    def append(self, movement: StockMovement) -> StockMovement:
        if movement.id in self._by_id:
            raise DuplicateMovementError(f"movement {movement.id} is already recorded")
        position = (movement.batch, movement.location_id)
        self._ensure_never_negative(position, movement)
        self._movements.append(movement)
        self._by_id[movement.id] = movement
        self._by_batch[movement.batch].append(movement)
        self._by_position[position].append(movement)
        self._current[position] += movement.qty
        latest = self._latest_at.get(position)
        if latest is None or movement.at > latest:
            self._latest_at[position] = movement.at
        return movement

    def _ensure_never_negative(self, position: Position, movement: StockMovement) -> None:
        """Refuse a movement that would take the position below zero at any time.

        Movements at the same instant are applied in the order they were appended.
        """
        if movement.qty > 0:
            return
        latest = self._latest_at.get(position)
        if latest is None or movement.at >= latest:
            if self._current.get(position, 0) + movement.qty < 0:
                self._refuse(movement, self._current.get(position, 0) + movement.qty, movement.at)
            return

        # Backdated: replay the position with the new movement slotted into time order.
        history = [*self._by_position[position], movement]
        running = 0
        for m in sorted(history, key=lambda m: m.at):
            running += m.qty
            if running < 0:
                self._refuse(movement, running, m.at)

    @staticmethod
    def _refuse(movement: StockMovement, shortfall_balance: int, when: datetime) -> None:
        raise InsufficientStockError(
            f"{movement.kind} of {abs(movement.qty)} units of batch {movement.batch.batch_no} "
            f"at {movement.location_id} would leave {shortfall_balance} units "
            f"at {when.isoformat()}"
        )

    def balance(self, batch: BatchKey, location_id: str, as_of: datetime | None = None) -> int:
        """Units of a batch at a location, now or at the end of ``as_of``."""
        position = (batch, location_id)
        if as_of is None:
            return self._current.get(position, 0)
        return sum(m.qty for m in self._by_position.get(position, ()) if m.at <= as_of)

    def balances(self, as_of: datetime | None = None) -> dict[Position, int]:
        """Every non-zero balance, now or at the end of ``as_of``."""
        if as_of is None:
            return {position: qty for position, qty in self._current.items() if qty}
        totals: defaultdict[Position, int] = defaultdict(int)
        for m in self._movements:
            if m.at <= as_of:
                totals[(m.batch, m.location_id)] += m.qty
        return {position: qty for position, qty in totals.items() if qty}

    def movements_for(self, batch: BatchKey) -> list[StockMovement]:
        """Every movement of a batch at any location, oldest first."""
        return sorted(self._by_batch.get(batch, ()), key=lambda m: m.at)
