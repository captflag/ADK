"""GST identification numbers: their layout and check character.

A GSTIN has fifteen characters: a two-digit state code, the holder's ten-
character PAN, an entity number, the letter Z by default, and a check character
computed from the first fourteen. A misread character almost always breaks the
check, which is why intake verifies it before trusting a supplier's number.
"""

from __future__ import annotations

import re

_CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LAYOUT = re.compile(r"(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])([A-Z0-9])([0-9A-Z])")
STATE_CODES = frozenset({*range(1, 39), 97, 99})
"""State and union territory codes in use, with 97 for other territory and 99 for the centre."""


def check_character(first_fourteen: str) -> str:
    """The fifteenth character a GSTIN beginning with these fourteen must end with."""
    if len(first_fourteen) != 14 or any(c not in _CHARACTERS for c in first_fourteen):
        raise ValueError("a GSTIN's first fourteen characters are digits and capital letters")
    total = 0
    for position, character in enumerate(first_fourteen):
        product = _CHARACTERS.index(character) * (1 if position % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _CHARACTERS[(36 - total % 36) % 36]


def gstin_problem(gstin: str) -> str | None:
    """Why a GSTIN cannot be right, or None if its layout and check character hold."""
    text = "".join(gstin.split()).upper()
    found = _LAYOUT.fullmatch(text)
    if not found:
        return f"GSTIN {gstin!r} does not have the fifteen-character layout"
    if int(found[1]) not in STATE_CODES:
        return f"GSTIN {gstin!r} starts with {found[1]}, which is not a state code"
    if check_character(text[:14]) != text[14]:
        return f"GSTIN {gstin!r} fails its check character, so a character is misread"
    return None


def make_gstin(state_code: int, pan: str, entity: str = "1") -> str:
    """A GSTIN for a PAN in a state, with its check character. Used by the simulator."""
    first_fourteen = f"{state_code:02d}{pan}{entity}Z"
    return first_fourteen + check_character(first_fourteen)
