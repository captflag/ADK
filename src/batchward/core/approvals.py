"""Approvals: a person's yes to an action, recorded before the action happens (ADR 0005).

An approval names what was approved by a stable id, who approved it and when,
and a digest of exactly what they saw. Approving the same thing again changes
nothing; approving something different under the same id is refused, so an
entry can never be posted twice, nor posted in a form nobody approved.

An action waits for its approval as a numbered request, which a person answers
later with a decision: yes, or no and why.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    """What was approved, stable across attempts, e.g. "purchase:<GSTIN>:<invoice no>"."""
    kind: str
    approved_by: str
    at: datetime
    digest: str
    """SHA-256 of exactly what was approved."""
    summary: str

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        if not all(text.strip() for text in (self.id, self.kind, self.approved_by, self.summary)):
            raise ValueError("an approval needs what was approved, by whom, and a summary")
        if len(self.digest) != 64:
            raise ValueError("an approval needs the SHA-256 digest of what was approved")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """An action waiting for a person's decision, and the paused run the decision resumes."""

    number: str
    """What a person types to answer it, e.g. "A-0012"."""
    approval_id: str
    kind: str
    digest: str
    summary: str
    requested_at: datetime
    session_id: str
    """The session of the paused workflow run."""

    def __post_init__(self) -> None:
        if self.requested_at.tzinfo is None:
            raise ValueError("requested_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Decision:
    """A person's answer to a request."""

    request: str
    approved: bool
    decided_by: str
    at: datetime
    note: str = ""
    """Why it was rejected, or why an approved action could not be carried out."""

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        if not self.decided_by.strip():
            raise ValueError("a decision needs the person who made it")


class RequestState(StrEnum):
    WAITING = "waiting"
    APPROVED = "approved"
    """Approved, and carried out exactly as approved."""
    NOT_CARRIED_OUT = "approved, not carried out"
    REJECTED = "rejected"
    REPLACED = "replaced"
    """A later request for the same action, in another form, replaced it."""
    OVERTAKEN = "overtaken"
    """The same action was approved in another form."""


@dataclass(frozen=True, slots=True)
class Posting:
    """What approving an action writes, and the digest of it a person approves."""

    approval_id: str
    files: dict[str, str]
    """File name to text."""
    summary: str
    digest: str


@dataclass(frozen=True, slots=True)
class Posted:
    approval: Approval
    """The approval recorded, or the one recorded earlier for the same posting."""
    written: tuple[Path, ...]
    """Files written now; empty if the same posting was approved before."""


def write_files(out: Path, files: Mapping[str, str]) -> tuple[Path, ...]:
    """Write a posting's files into ``out``, which is made if missing."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in files.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(written)


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
