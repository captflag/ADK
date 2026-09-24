"""Argument types shared by the commands, so bad input is refused before any work starts."""

from __future__ import annotations

import argparse
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path


def positive_int(text: str) -> int:
    value = _int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def non_negative_int(text: str) -> int:
    value = _int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must not be negative, not {value}")
    return value


def rupees(text: str) -> Decimal:
    """A finite amount of money, such as 64.21."""
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"not an amount: {text!r}") from error
    if not value.is_finite():
        raise argparse.ArgumentTypeError(f"not an amount: {text!r}")
    return value


def env_path(name: str) -> Path | None:
    """A path from an environment variable, usually set in ``.env``, or None if it is not set."""
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from error
