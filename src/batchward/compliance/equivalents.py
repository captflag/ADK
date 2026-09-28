"""Notified formulation names matched to the names the item master uses, by a person.

NPPA names a formulation as the schedule names it: "Amoxycillin and Potassium
Clavulanate", "Tablet 500 mg + 125 mg". The item master names it as the office
typed it years ago: "Amoxicillin + Clavulanic acid", "625 mg". Both are the same
medicine and neither is wrong, so a notified ceiling that should bind a stocked
pack binds nothing, and the import can only count it (ADR 0013).

Batchward never decides that two names mean the same medicine. A ceiling price
is enforced at billing, so a wrong equivalence would block good stock or let an
overcharge through, and nothing about a name says which of two spellings the
notification meant. A person records the equivalence, with their name and the
date, and it is kept for ever (ADR 0028).

What is done for them is the looking: for a notified row that matches nothing,
the stocked formulations that are close are worked out, each with the reason it
is close - the parts of the strength adding up, the same strength in another
unit, a name spelled a little differently, or names sharing their words. The
person reads a short list instead of searching a catalogue of thousands.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from batchward.core.models import Item

MOST_SUGGESTIONS = 3
"""How many candidates are worth a person's time for one notified row."""
CLOSE_ENOUGH = 2
"""How many letters a name may differ by and still be offered as the same medicine."""
SHARED_ENOUGH = 0.5
"""How much of the shorter name's words the two must share to be offered."""
_UNITS = {"mg": Decimal(1), "g": Decimal(1000), "gm": Decimal(1000), "mcg": Decimal("0.001")}
"""Weights in milligrams. Volumes are handled separately, and never mixed with weights."""
_VOLUMES = {"ml": Decimal(1), "l": Decimal(1000)}
_IGNORED = {"and", "with", "the", "ip", "bp", "usp", "sodium", "acid", "salt"}
"""Words that say nothing about which medicine it is."""


class EquivalenceError(ValueError):
    """An equivalence that cannot be recorded, saying why."""


@dataclass(frozen=True, slots=True)
class Equivalent:
    """One person's record that a notified formulation is a stocked one."""

    formulation: str
    """The formulation as the notification names it."""
    strength: str
    """The strength as the notification gives it, e.g. "500 mg + 125 mg"."""
    molecule: str
    """The molecule as the item master records it."""
    item_strength: str
    noted_by: str
    noted_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        for what, value in (
            ("the notified formulation", self.formulation),
            ("the notified strength", self.strength),
            ("the molecule", self.molecule),
            ("the item's strength", self.item_strength),
            ("who recorded it", self.noted_by),
        ):
            if not value.strip():
                raise EquivalenceError(f"an equivalence needs {what}")
        if self.same_as_notified:
            raise EquivalenceError(
                f"{self.formulation} {self.strength} already matches the item master; "
                "an equivalence is only needed where the names differ"
            )

    @property
    def key(self) -> tuple[str, str]:
        """What it is looked up by: the notified name and strength, however they are typed."""
        return normal(self.formulation), normal(self.strength)

    @property
    def same_as_notified(self) -> bool:
        return normal(self.formulation) == normal(self.molecule) and normal(
            self.strength
        ) == normal(self.item_strength)

    def __str__(self) -> str:
        return (
            f"{self.formulation} {self.strength} is {self.molecule} {self.item_strength}"
            f" ({self.noted_by}, {self.noted_at:%d/%m/%Y})"
        )


class EquivalentTable:
    """Every recorded equivalence, with the latest record for a name winning.

    A mistake is not written over: a later record supersedes an earlier one and
    both stay on file, as a hold and its release do (ADR 0008).
    """

    def __init__(self, equivalents: Iterable[Equivalent] = ()) -> None:
        self._latest: dict[tuple[str, str], Equivalent] = {}
        for equivalent in equivalents:
            held = self._latest.get(equivalent.key)
            if held is None or equivalent.noted_at >= held.noted_at:
                self._latest[equivalent.key] = equivalent

    def __len__(self) -> int:
        return len(self._latest)

    def find(self, formulation: str, strength: str) -> Equivalent | None:
        """What a person says this notified formulation is, if anyone has said."""
        return self._latest.get((normal(formulation), normal(strength)))

    def all(self) -> tuple[Equivalent, ...]:
        return tuple(self._latest.values())


@dataclass(frozen=True, slots=True)
class Candidate:
    """A stocked formulation that might be what a notified row means, and why."""

    molecule: str
    strength: str
    items: int
    """How many stocked items have this molecule and strength."""
    reason: str
    closeness: float
    """Higher is closer; only for ordering one row's candidates."""


def normal(text: str) -> str:
    """A name with case and spacing taken out, as the readers compare them."""
    return "".join((text or "").lower().split())


def words(text: str) -> frozenset[str]:
    """The words of a name that say which medicine it is."""
    found = re.findall(r"[a-z]+", (text or "").lower())
    return frozenset(word for word in found if len(word) > 2 and word not in _IGNORED)


def strength_in_base(strength: str) -> tuple[Decimal, str] | None:
    """A strength as one amount in milligrams or millilitres, if it can be read that way.

    "500 mg + 125 mg" is 625 mg, and "1 g" is 1000 mg, so the two ways a
    combination is written can be compared. A strength with no unit, mixing
    weight with volume, or written as a ratio such as "100 IU/ml", is not
    reduced: nothing is guessed.
    """
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([a-zA-Zµ]+)", strength or "")
    if not parts or "/" in (strength or ""):
        return None
    total, base = Decimal(0), ""
    for amount, unit in parts:
        written = unit.lower().replace("µ", "mc")
        table = _UNITS if written in _UNITS else _VOLUMES if written in _VOLUMES else None
        if table is None:
            return None
        here = "mg" if table is _UNITS else "ml"
        if base and here != base:
            return None
        base = here
        try:
            total += Decimal(amount) * table[written]
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            return None
    return total, base


def same_strength(one: str, other: str) -> bool:
    """Whether two strengths are the same amount, however they are written."""
    if normal(one) == normal(other):
        return True
    first, second = strength_in_base(one), strength_in_base(other)
    return first is not None and first == second


def apart(one: str, other: str) -> int:
    """How many single-letter changes turn one name into the other, up to a point."""
    first, second = normal(one), normal(other)
    if abs(len(first) - len(second)) > CLOSE_ENOUGH:
        return CLOSE_ENOUGH + 1
    row = list(range(len(second) + 1))
    for index, letter in enumerate(first, start=1):
        previous, row[0] = row[0], index
        for column, against in enumerate(second, start=1):
            cost = 0 if letter == against else 1
            previous, row[column] = (
                row[column],
                min(row[column] + 1, row[column - 1] + 1, previous + cost),
            )
        if min(row) > CLOSE_ENOUGH:
            return CLOSE_ENOUGH + 1
    return row[-1]


def suggestions(
    formulation: str, strength: str, items: Sequence[Item], *, most: int = MOST_SUGGESTIONS
) -> tuple[Candidate, ...]:
    """The stocked formulations a person should look at for a notified row that matched none."""
    stocked: dict[tuple[str, str], list[Item]] = {}
    for item in items:
        if item.molecule and item.strength:
            stocked.setdefault((item.molecule, item.strength), []).append(item)

    wanted = words(formulation)
    found = []
    for (molecule, item_strength), held in stocked.items():
        reason, closeness = _why(formulation, strength, molecule, item_strength, wanted)
        if reason:
            found.append(Candidate(molecule, item_strength, len(held), reason, closeness))
    found.sort(key=lambda candidate: (-candidate.closeness, candidate.molecule))
    return tuple(found[:most])


def _why(
    formulation: str, strength: str, molecule: str, item_strength: str, wanted: frozenset[str]
) -> tuple[str, float]:
    """Why a stocked formulation might be a notified one, and how close it is.

    The reason says what the strengths have in common and what the names do, so
    that a person can see at a glance which half of it to doubt.
    """
    theirs = words(molecule)
    shared = wanted & theirs
    share = len(shared) / (min(len(wanted), len(theirs)) or 1)
    letters = apart(formulation, molecule)
    same_name = normal(formulation) == normal(molecule)
    close_name = same_name or letters <= CLOSE_ENOUGH or share >= SHARED_ENOUGH
    if not close_name:
        return "", 0.0

    about_strength, worth = _about_strength(strength, item_strength)
    if same_name:
        about_name, name_worth = "the same name", 1.0
    elif letters <= CLOSE_ENOUGH:
        about_name = f"spelled {letters} letter{'s' if letters > 1 else ''} apart"
        name_worth = 0.8
    else:
        about_name, name_worth = f"the names share {_listed(shared)}", share / 2
    return "; ".join(part for part in (about_strength, about_name) if part), worth + name_worth


def _about_strength(strength: str, item_strength: str) -> tuple[str, float]:
    """What the two strengths have in common, if anything worth saying."""
    if normal(strength) == normal(item_strength):
        return "the same strength", 3.0
    if not same_strength(strength, item_strength):
        return "", 0.0
    parts = len(re.findall(r"\d+(?:\.\d+)?", strength)) > 1
    why = "its parts add up to" if parts else "the same strength written as"
    return f"{why} {item_strength}", 3.5


def _listed(names: frozenset[str]) -> str:
    """Words in a row, the last one joined with "and"."""
    found = sorted(names)
    if len(found) < 2:
        return found[0] if found else "nothing"
    return f"{', '.join(found[:-1])} and {found[-1]}"
