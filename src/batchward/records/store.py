"""A SQLite database of the records only Batchward keeps.

Stock lives in Marg, which Batchward reads but never writes (ADR 0006). Some
records exist only because Batchward keeps them: the recall notices it
received, the holds it placed on batches and their release, the ceiling
prices it checks bills against (ADR 0011), the requests for approval and the
approvals people gave before anything was written for Marg to import (ADR 0005),
what each approved bill received against its purchase order (ADR 0017), and
each company's return terms with the expiry claims made on it and the credit
notes that settle them (ADR 0018), the messages approvers sent (ADR 0019),
and the purchase orders placed on approval (ADR 0020). They are kept here, in a
database file of their own (ADR 0010).

Like the ledger (ADR 0001) these records are only ever added to. The database
enforces that itself: triggers refuse every UPDATE and DELETE, so a mistake is
corrected by recording a release or a later-dated price, never by rewriting
what happened.

The schema grows by numbered migrations, each applied once and never edited, so
a database written by an earlier Batchward is brought up to date when opened.
Times are stored as ISO 8601 text with their UTC offset, dates as ISO dates,
and money as decimal text.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from batchward.claims.claim import Claim, ClaimLine, Settlement
from batchward.claims.terms import ReturnTerms, TermsTable
from batchward.compliance.prices import CeilingPrice, CeilingTable
from batchward.compliance.recall import RecallClass, RecallNotice
from batchward.core.approvals import Approval, ApprovalRequest, Decision, RequestState
from batchward.core.holds import Hold, HoldError, HoldLog, Release
from batchward.core.models import BatchKey, BatchStatus
from batchward.core.orders import OrderLine, PurchaseOrder


def _only_ever_added_to(*tables: str) -> str:
    return "".join(
        f"""
CREATE TRIGGER {table}_never_updated BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} are only ever added to'); END;
CREATE TRIGGER {table}_never_deleted BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} are only ever added to'); END;
"""
        for table in tables
    )


_RECALLS = """
CREATE TABLE notices (
    reference TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    recall_class TEXT NOT NULL,
    received_at TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    manufacturer TEXT,
    product TEXT,
    expiry TEXT
);
CREATE TABLE holds (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    expiry TEXT NOT NULL,
    status TEXT NOT NULL,
    at TEXT NOT NULL,
    reason TEXT NOT NULL,
    reference TEXT NOT NULL,
    placed_by TEXT NOT NULL
);
CREATE TABLE releases (
    hold_id TEXT PRIMARY KEY REFERENCES holds (id),
    at TEXT NOT NULL,
    reason TEXT NOT NULL,
    released_by TEXT NOT NULL
);
""" + _only_ever_added_to("notices", "holds", "releases")

_CEILINGS = """
CREATE TABLE ceiling_prices (
    formulation TEXT NOT NULL,
    molecule TEXT NOT NULL,
    strength TEXT NOT NULL,
    unit TEXT NOT NULL,
    ceiling TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    reference TEXT NOT NULL,
    PRIMARY KEY (formulation, effective_from)
);
""" + _only_ever_added_to("ceiling_prices")

_APPROVALS = """
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    at TEXT NOT NULL,
    digest TEXT NOT NULL,
    summary TEXT NOT NULL
);
""" + _only_ever_added_to("approvals")

_RECEIVED = """
CREATE TABLE received_on_orders (
    approval_id TEXT NOT NULL REFERENCES approvals (id),
    order_no TEXT NOT NULL,
    item_id TEXT NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    PRIMARY KEY (approval_id, item_id)
);
CREATE INDEX received_by_order ON received_on_orders (order_no);
""" + _only_ever_added_to("received_on_orders")

_REQUESTS = """
CREATE TABLE approval_requests (
    number TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    digest TEXT NOT NULL,
    summary TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE
);
CREATE INDEX requests_by_approval ON approval_requests (approval_id);
CREATE TABLE decisions (
    request TEXT PRIMARY KEY REFERENCES approval_requests (number),
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    decided_by TEXT NOT NULL,
    at TEXT NOT NULL,
    note TEXT NOT NULL
);
""" + _only_ever_added_to("approval_requests", "decisions")

_CLAIMS = """
CREATE TABLE return_terms (
    company_id TEXT NOT NULL,
    opens_days_before INTEGER NOT NULL,
    closes_days_after INTEGER NOT NULL,
    credit_percent TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    reference TEXT NOT NULL,
    PRIMARY KEY (company_id, effective_from)
);
CREATE TABLE claims (
    number TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE REFERENCES approvals (id),
    company_id TEXT NOT NULL,
    made_on TEXT NOT NULL
);
CREATE TABLE claim_lines (
    claim TEXT NOT NULL REFERENCES claims (number),
    company_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    expiry TEXT NOT NULL,
    location_id TEXT NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    rate TEXT NOT NULL,
    credit_percent TEXT NOT NULL,
    gst_rate TEXT NOT NULL,
    bought_on TEXT,
    PRIMARY KEY (claim, company_id, item_id, batch_no, expiry, location_id)
);
CREATE TABLE claim_settlements (
    claim TEXT NOT NULL REFERENCES claims (number),
    credit_note TEXT NOT NULL,
    amount TEXT NOT NULL,
    received_on TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    PRIMARY KEY (claim, credit_note)
);
""" + _only_ever_added_to("return_terms", "claims", "claim_lines", "claim_settlements")

_MESSAGES = """
CREATE TABLE channel_messages (
    channel TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (channel, message_id)
);
""" + _only_ever_added_to("channel_messages")

_ORDERS = """
CREATE TABLE purchase_orders (
    number TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE REFERENCES approvals (id),
    company_id TEXT NOT NULL,
    placed_on TEXT NOT NULL
);
CREATE TABLE purchase_order_lines (
    order_no TEXT NOT NULL REFERENCES purchase_orders (number),
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_no, item_id)
);
""" + _only_ever_added_to("purchase_orders", "purchase_order_lines")

MIGRATIONS: tuple[str, ...] = (
    _RECALLS,
    _CEILINGS,
    _APPROVALS,
    _RECEIVED,
    _REQUESTS,
    _CLAIMS,
    _MESSAGES,
    _ORDERS,
)
"""Each entry takes the database from the schema before it to the next. Never edit one."""
SCHEMA_VERSION = len(MIGRATIONS)
_TABLES: tuple[set[str], ...] = (
    {"notices", "holds", "releases"},
    {"ceiling_prices"},
    {"approvals"},
    {"received_on_orders"},
    {"approval_requests", "decisions"},
    {"return_terms", "claims", "claim_lines", "claim_settlements"},
    {"channel_messages"},
    {"purchase_orders", "purchase_order_lines"},
)
"""The tables each migration creates, to recognise a file that only claims to be one."""


class RecordsError(Exception):
    """The records cannot be read or would be contradicted."""


class RecordStore:
    """Batchward's records in one SQLite file. Use as a context manager, one per thread.

    With ``create=False`` the file must already hold Batchward's records, so
    something that only reads them, such as an agent's tool, never leaves a new
    file behind.
    """

    def __init__(self, path: Path | str, *, create: bool = True) -> None:
        self.path = Path(path)
        self._in_transaction = False
        self._create = create
        if not create and not self.path.is_file():
            raise RecordsError(f"no records database at {self.path}")
        try:
            self._connection = sqlite3.connect(self.path, isolation_level=None)
        except sqlite3.Error as error:
            raise RecordsError(f"{self.path} cannot be opened: {error}") from error
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except sqlite3.DatabaseError as error:
            self._connection.close()
            raise RecordsError(f"{self.path} is not a usable records database: {error}") from error
        except BaseException:
            self._connection.close()
            raise

    def __enter__(self) -> RecordStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[RecordStore]:
        """Everything inside is recorded together or not at all.

        The write lock is taken at the start, so two processes receiving notices at
        the same moment cannot both read the holds and place the same one twice.
        """
        if self._in_transaction:
            raise RecordsError("transactions cannot be nested")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise RecordsError(
                f"{self.path} is busy: another process is writing to it ({error}); try again"
            ) from error
        self._in_transaction = True
        try:
            yield self
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")
        finally:
            self._in_transaction = False

    def save_notice(self, notice: RecallNotice) -> bool:
        """Record a notice. Returns False if the identical notice is already recorded.

        A different notice under a reference already used is refused: references
        identify notices, and a correction is a new notice with its own reference.
        """
        with self.atomically():
            existing = self.notice(notice.reference)
            if existing is not None:
                if existing != notice:
                    raise RecordsError(
                        f"a different notice is already recorded under reference {notice.reference}"
                    )
                return False
            self._connection.execute(
                "INSERT INTO notices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    notice.reference,
                    notice.source,
                    str(notice.recall_class),
                    notice.received_at.isoformat(),
                    notice.batch_no,
                    notice.manufacturer,
                    notice.product,
                    notice.expiry.isoformat() if notice.expiry else None,
                ),
            )
            return True

    def notice(self, reference: str) -> RecallNotice | None:
        row = self._connection.execute(
            "SELECT * FROM notices WHERE reference = ?", (reference,)
        ).fetchone()
        return None if row is None else _read(_notice, row, "notice")

    def notices(self) -> list[RecallNotice]:
        """Every notice, earliest received first."""
        rows = self._connection.execute("SELECT * FROM notices").fetchall()
        notices = (_read(_notice, row, "notice") for row in rows)
        return sorted(notices, key=lambda n: (n.received_at, n.reference))

    def notices_naming(self, batch_no: str) -> list[RecallNotice]:
        """Notices whose batch number is this one, ignoring spaces and letter case only."""
        wanted = _normalise(batch_no)
        return [n for n in self.notices() if _normalise(n.batch_no) == wanted]

    def save_hold(self, hold: Hold) -> None:
        key = hold.batch
        self._insert(
            "INSERT INTO holds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hold.id,
                key.company_id,
                key.item_id,
                key.batch_no,
                key.expiry.isoformat(),
                str(hold.status),
                hold.at.isoformat(),
                hold.reason,
                hold.reference,
                hold.placed_by,
            ),
            f"hold {hold.id}",
        )

    def save_release(self, release: Release) -> None:
        self._insert(
            "INSERT INTO releases VALUES (?, ?, ?, ?)",
            (release.hold_id, release.at.isoformat(), release.reason, release.released_by),
            f"release of hold {release.hold_id}",
        )

    def hold_log(self) -> HoldLog:
        """Every hold and release, read back into a log ready to place more."""
        log = HoldLog()
        for row in self._connection.execute("SELECT * FROM holds ORDER BY id"):
            log.record(_read(_hold, row, "hold"))
        for row in self._connection.execute("SELECT * FROM releases ORDER BY hold_id"):
            _read(lambda row: log.record_release(_release(row)), row, "release")
        return log

    def save_ceiling(self, price: CeilingPrice) -> bool:
        """Record a ceiling price. Returns False if the same one is already recorded.

        A different price for a formulation on a date already recorded is refused;
        a revision takes effect on its own date.
        """
        with self.atomically():
            # A recorded price is found by comparing formulations as the Price Guard does,
            # not by the stored key, so one keyed by an older Batchward is found too.
            same_day = self._connection.execute(
                "SELECT molecule, strength, unit, ceiling, effective_from, reference "
                "FROM ceiling_prices WHERE effective_from = ?",
                (price.effective_from.isoformat(),),
            ).fetchall()
            for row in same_day:
                recorded = _read(_ceiling, row, "ceiling price")
                if recorded.formulation != price.formulation:
                    continue
                if (recorded.ceiling, recorded.reference) != (price.ceiling, price.reference):
                    raise RecordsError(
                        f"a different ceiling price for {price.molecule} {price.strength} "
                        f"{price.unit} from {price.effective_from.isoformat()} is already recorded"
                    )
                return False
            self._connection.execute(
                "INSERT INTO ceiling_prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _formulation_key(price),
                    price.molecule,
                    price.strength,
                    price.unit,
                    str(price.ceiling),
                    price.effective_from.isoformat(),
                    price.reference,
                ),
            )
            return True

    def ceiling_prices(self) -> list[CeilingPrice]:
        """Every ceiling price recorded, by formulation and then date."""
        rows = self._connection.execute(
            "SELECT molecule, strength, unit, ceiling, effective_from, reference "
            "FROM ceiling_prices ORDER BY formulation, effective_from"
        )
        return [_read(_ceiling, row, "ceiling price") for row in rows]

    def ceiling_table(self) -> CeilingTable:
        return CeilingTable(self.ceiling_prices())

    def save_approval(self, approval: Approval) -> bool:
        """Record an approval. Returns False if the same approval is already recorded.

        An approval under an id already used, for something with a different digest, is
        refused: what was approved has changed, and approving it again would post twice.
        """
        with self.atomically():
            existing = self.approval(approval.id)
            if existing is not None:
                if existing.digest != approval.digest:
                    raise RecordsError(
                        f"{approval.id} was already approved by {existing.approved_by} on "
                        f"{existing.at:%d/%m/%Y %H:%M}, in a different form"
                    )
                return False
            self._connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
                (
                    approval.id,
                    approval.kind,
                    approval.approved_by,
                    approval.at.isoformat(),
                    approval.digest,
                    approval.summary,
                ),
            )
            return True

    def approval(self, approval_id: str) -> Approval | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        return None if row is None else _read(_approval, row, "approval")

    def approvals(self) -> list[Approval]:
        """Every approval, earliest first."""
        rows = self._connection.execute("SELECT * FROM approvals").fetchall()
        return sorted((_read(_approval, row, "approval") for row in rows), key=lambda a: a.at)

    def request_approval(
        self,
        *,
        approval_id: str,
        kind: str,
        digest: str,
        summary: str,
        at: datetime,
        session_id: str,
    ) -> ApprovalRequest:
        """Record that an action waits for approval, and number the request.

        The same paused run asking again gets its request back, so a workflow node
        that runs again when resumed does not ask twice.
        """
        with self.atomically():
            existing = self.request_for_session(session_id)
            if existing is not None:
                if (existing.approval_id, existing.digest) != (approval_id, digest):
                    raise RecordsError(
                        f"session {session_id} already asked for approval of something else"
                    )
                return existing
            (count,) = self._connection.execute("SELECT count(*) FROM approval_requests").fetchone()
            request = ApprovalRequest(
                number=f"A-{count + 1:04d}",
                approval_id=approval_id,
                kind=kind,
                digest=digest,
                summary=summary,
                requested_at=at,
                session_id=session_id,
            )
            self._connection.execute(
                "INSERT INTO approval_requests VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request.number, approval_id, kind, digest, summary, at.isoformat(), session_id),
            )
            return request

    def request(self, number: str) -> ApprovalRequest | None:
        row = self._connection.execute(
            "SELECT * FROM approval_requests WHERE number = ?", (_normalise(number),)
        ).fetchone()
        return None if row is None else _read(_approval_request, row, "approval request")

    def request_for_session(self, session_id: str) -> ApprovalRequest | None:
        row = self._connection.execute(
            "SELECT * FROM approval_requests WHERE session_id = ?", (session_id,)
        ).fetchone()
        return None if row is None else _read(_approval_request, row, "approval request")

    def requests(self) -> list[ApprovalRequest]:
        """Every request for approval, in the order they were made."""
        rows = self._connection.execute("SELECT * FROM approval_requests ORDER BY rowid")
        return [_read(_approval_request, row, "approval request") for row in rows]

    def save_decision(self, decision: Decision) -> bool:
        """Record a person's answer to a request. Returns False if it is already recorded.

        A request is answered once. The same answer again, as when a workflow node runs
        again after a crash, is harmless; a different answer is refused.
        """
        with self.atomically():
            existing = self.decision(decision.request)
            if existing is not None:
                if (existing.approved, existing.decided_by) != (
                    decision.approved,
                    decision.decided_by,
                ):
                    raise RecordsError(
                        f"{decision.request} was already "
                        f"{'approved' if existing.approved else 'rejected'} by "
                        f"{existing.decided_by} on {existing.at:%d/%m/%Y %H:%M}"
                    )
                return False
            self._insert(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?)",
                (
                    decision.request,
                    int(decision.approved),
                    decision.decided_by,
                    decision.at.isoformat(),
                    decision.note,
                ),
                f"a decision on {decision.request}",
            )
            return True

    def decision(self, request: str) -> Decision | None:
        row = self._connection.execute(
            "SELECT * FROM decisions WHERE request = ?", (_normalise(request),)
        ).fetchone()
        return None if row is None else _read(_decision, row, "decision")

    def request_state(self, request: ApprovalRequest) -> RequestState:
        """Where a request stands, from its decision and what has been approved since."""
        decision = self.decision(request.number)
        approval = self.approval(request.approval_id)
        carried_out = approval is not None and approval.digest == request.digest
        if decision is not None:
            if not decision.approved:
                return RequestState.REJECTED
            return RequestState.APPROVED if carried_out else RequestState.NOT_CARRIED_OUT
        if approval is not None:
            return RequestState.APPROVED if carried_out else RequestState.OVERTAKEN
        later = self._connection.execute(
            "SELECT 1 FROM approval_requests WHERE approval_id = ? AND rowid > "
            "(SELECT rowid FROM approval_requests WHERE number = ?)",
            (request.approval_id, request.number),
        ).fetchone()
        return RequestState.REPLACED if later else RequestState.WAITING

    def save_terms(self, terms: ReturnTerms) -> bool:
        """Record a company's return terms. Returns False if the same terms are recorded.

        Different terms for a company from a date already recorded are refused; a
        change takes effect on its own date.
        """
        with self.atomically():
            row = self._connection.execute(
                "SELECT * FROM return_terms WHERE company_id = ? AND effective_from = ?",
                (terms.company_id, terms.effective_from.isoformat()),
            ).fetchone()
            if row is not None:
                if _read(_terms, row, "return terms") != terms:
                    raise RecordsError(
                        f"different return terms for {terms.company_id} from "
                        f"{terms.effective_from.isoformat()} are already recorded"
                    )
                return False
            self._connection.execute(
                "INSERT INTO return_terms VALUES (?, ?, ?, ?, ?, ?)",
                (
                    terms.company_id,
                    terms.opens_days_before_expiry,
                    terms.closes_days_after_expiry,
                    str(terms.credit_percent),
                    terms.effective_from.isoformat(),
                    terms.reference,
                ),
            )
            return True

    def return_terms(self) -> list[ReturnTerms]:
        """Every company's return terms, by company and then date."""
        rows = self._connection.execute(
            "SELECT * FROM return_terms ORDER BY company_id, effective_from"
        )
        return [_read(_terms, row, "return terms") for row in rows]

    def terms_table(self) -> TermsTable:
        return TermsTable(self.return_terms())

    def save_claim(self, claim: Claim, approval_id: str) -> None:
        """Record a claim made on its approval, with every line; refused if already recorded."""
        with self.atomically():
            self._insert(
                "INSERT INTO claims VALUES (?, ?, ?, ?)",
                (claim.number, approval_id, claim.company_id, claim.made_on.isoformat()),
                f"claim {claim.number}",
            )
            for line in claim.lines:
                key = line.batch
                self._insert(
                    "INSERT INTO claim_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        claim.number,
                        key.company_id,
                        key.item_id,
                        key.batch_no,
                        key.expiry.isoformat(),
                        line.location_id,
                        line.units,
                        str(line.rate),
                        str(line.credit_percent),
                        str(line.gst_rate),
                        None if line.bought_on is None else line.bought_on.isoformat(),
                    ),
                    f"a line of claim {claim.number}",
                )

    def claims(self) -> list[Claim]:
        """Every claim made, earliest first."""
        lines: dict[str, list[ClaimLine]] = {}
        for row in self._connection.execute("SELECT * FROM claim_lines ORDER BY rowid"):
            lines.setdefault(row[0], []).append(_read(_claim_line, row, "claim line"))
        rows = self._connection.execute(
            "SELECT number, company_id, made_on FROM claims ORDER BY made_on, number"
        )
        return [
            _read(
                lambda row: Claim(
                    row[0], row[1], date.fromisoformat(row[2]), tuple(lines.get(row[0], ()))
                ),
                row,
                "claim",
            )
            for row in rows
        ]

    def claim(self, number: str) -> Claim | None:
        wanted = _normalise(number)
        return next((c for c in self.claims() if _normalise(c.number) == wanted), None)

    def save_settlement(self, settlement: Settlement) -> bool:
        """Record a credit note against a claim. Returns False if it is already recorded.

        A credit note is recorded once per claim; the same number with a different
        amount or date is refused.
        """
        with self.atomically():
            if self.claim(settlement.claim) is None:
                raise RecordsError(f"no claim {settlement.claim} is recorded")
            for recorded in self.settlements(settlement.claim):
                if _normalise(recorded.credit_note) != _normalise(settlement.credit_note):
                    continue
                if (recorded.amount, recorded.received_on) != (
                    settlement.amount,
                    settlement.received_on,
                ):
                    raise RecordsError(
                        f"credit note {settlement.credit_note} is already recorded against "
                        f"{settlement.claim} for {recorded.amount} on "
                        f"{recorded.received_on:%d/%m/%Y}"
                    )
                return False
            self._connection.execute(
                "INSERT INTO claim_settlements VALUES (?, ?, ?, ?, ?)",
                (
                    settlement.claim,
                    settlement.credit_note,
                    str(settlement.amount),
                    settlement.received_on.isoformat(),
                    settlement.recorded_by,
                ),
            )
            return True

    def settlements(self, claim: str | None = None) -> list[Settlement]:
        """Credit notes received, for one claim or all, earliest first."""
        rows = self._connection.execute(
            "SELECT * FROM claim_settlements ORDER BY received_on, rowid"
        ).fetchall()
        found = [_read(_settlement, row, "settlement") for row in rows]
        if claim is None:
            return found
        return [s for s in found if _normalise(s.claim) == _normalise(claim)]

    def save_order(self, order: PurchaseOrder, approval_id: str) -> None:
        """Record an order placed on its approval; refused if already recorded."""
        with self.atomically():
            self._insert(
                "INSERT INTO purchase_orders VALUES (?, ?, ?, ?)",
                (order.number, approval_id, order.company_id, order.placed_on.isoformat()),
                f"order {order.number}",
            )
            for line in order.lines:
                self._insert(
                    "INSERT INTO purchase_order_lines VALUES (?, ?, ?)",
                    (order.number, line.item_id, line.quantity),
                    f"a line of order {order.number}",
                )

    def orders(self) -> list[PurchaseOrder]:
        """Every order placed through Batchward, earliest first."""
        lines: dict[str, list[OrderLine]] = {}
        for order_no, item_id, quantity in self._connection.execute(
            "SELECT * FROM purchase_order_lines ORDER BY rowid"
        ):
            lines.setdefault(order_no, []).append(OrderLine(item_id, int(quantity)))
        rows = self._connection.execute(
            "SELECT number, company_id, placed_on FROM purchase_orders ORDER BY placed_on, number"
        )
        return [
            _read(
                lambda row: PurchaseOrder(
                    row[0], row[1], date.fromisoformat(row[2]), tuple(lines.get(row[0], ()))
                ),
                row,
                "purchase order",
            )
            for row in rows
        ]

    def order(self, number: str) -> PurchaseOrder | None:
        """An order by its number, ignoring spaces and letter case."""
        wanted = _normalise(number)
        return next((o for o in self.orders() if _normalise(o.number) == wanted), None)

    def save_message(
        self, channel: str, message_id: str, sender: str, body: str, at: datetime
    ) -> bool:
        """Record a message a person sent on a channel. Returns False if it is already recorded.

        Channels may deliver a message more than once; its id says it is the same one.
        """
        if at.tzinfo is None:
            raise ValueError("a message's time must be timezone-aware")
        with self.atomically():
            seen = self._connection.execute(
                "SELECT 1 FROM channel_messages WHERE channel = ? AND message_id = ?",
                (channel, message_id),
            ).fetchone()
            if seen is not None:
                return False
            self._connection.execute(
                "INSERT INTO channel_messages VALUES (?, ?, ?, ?, ?)",
                (channel, message_id, sender, body, at.isoformat()),
            )
            return True

    def messages(self) -> list[tuple[str, str, str, str, datetime]]:
        """Every message recorded, as (channel, id, sender, body, time), in arrival order."""
        rows = self._connection.execute("SELECT * FROM channel_messages ORDER BY rowid")
        return [(c, i, s, b, datetime.fromisoformat(t)) for c, i, s, b, t in rows]

    def save_received(self, approval_id: str, order_no: str, units: Mapping[str, int]) -> None:
        """Record the units of each item an approved bill received against a purchase order.

        Saved with the approval, in the same transaction, so a later bill on the same
        order is matched against what is still due (ADR 0017).
        """
        with self.atomically():
            for item_id, count in sorted(units.items()):
                self._insert(
                    "INSERT INTO received_on_orders VALUES (?, ?, ?, ?)",
                    (approval_id, _normalise(order_no), item_id, count),
                    f"{count} units of {item_id} received on order {order_no}",
                )

    def received_against(self, order_no: str, *, excluding: str | None = None) -> dict[str, int]:
        """Units of each item approved bills have received against this order.

        Order numbers match ignoring spaces and letter case. ``excluding`` leaves out
        one approval's bill, so a bill being matched again is not counted against itself.
        """
        rows = self._connection.execute(
            "SELECT item_id, units FROM received_on_orders WHERE order_no = ? AND approval_id != ?",
            (_normalise(order_no), excluding or ""),
        )
        received: dict[str, int] = {}
        for item_id, units in rows:
            received[item_id] = received.get(item_id, 0) + units
        return received

    def _insert(self, sql: str, values: tuple, what: str) -> None:
        try:
            self._connection.execute(sql, values)
        except sqlite3.IntegrityError as error:
            raise RecordsError(f"cannot record {what}: {error}") from error

    @contextmanager
    def atomically(self) -> Iterator[None]:
        """Inside a caller's transaction, nothing more; otherwise a transaction of its own."""
        if self._in_transaction:
            yield
        else:
            with self.transaction():
                yield

    def _schema_version(self) -> int:
        return self._connection.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        if self._schema_version() == SCHEMA_VERSION:
            self._check_tables(SCHEMA_VERSION)
            return
        # Another process may be creating or upgrading this file at the same moment, so
        # the version is read again, and acted on, only while holding the write lock.
        with self.transaction():
            version = self._schema_version()
            if version > SCHEMA_VERSION:
                raise RecordsError(
                    f"{self.path} was written by a newer Batchward (schema {version}); "
                    f"this version reads schema {SCHEMA_VERSION}"
                )
            if version == 0:
                tables = self._connection.execute("SELECT count(*) FROM sqlite_master")
                if tables.fetchone()[0]:
                    raise RecordsError(f"{self.path} is not a Batchward records database")
                if not self._create:
                    raise RecordsError(f"{self.path} holds no Batchward records")
            self._check_tables(version)
            for number in range(version, SCHEMA_VERSION):
                for statement in _statements(MIGRATIONS[number]):
                    self._connection.execute(statement)
                self._connection.execute(f"PRAGMA user_version = {number + 1}")

    def _check_tables(self, version: int) -> None:
        """Refuse a file whose schema version promises tables it does not have."""
        rows = self._connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        present = {name for (name,) in rows}
        if not set().union(*_TABLES[:version]) <= present:
            raise RecordsError(f"{self.path} is not a Batchward records database")


def _read[T](build: Callable[[tuple], T], row: tuple, what: str) -> T:
    """A record built from a stored row, or RecordsError if the row no longer makes one."""
    try:
        return build(row)
    except (ValueError, TypeError, HoldError) as error:
        raise RecordsError(f"a stored {what} cannot be read ({row[0]!r}): {error}") from error


def _formulation_key(price: CeilingPrice) -> str:
    """The formulation as one text, escaped so two different formulations never share it."""
    return "|".join(part.replace("\\", "\\\\").replace("|", "\\|") for part in price.formulation)


def _approval(row: tuple) -> Approval:
    return Approval(
        id=row[0],
        kind=row[1],
        approved_by=row[2],
        at=datetime.fromisoformat(row[3]),
        digest=row[4],
        summary=row[5],
    )


def _terms(row: tuple) -> ReturnTerms:
    return ReturnTerms(
        company_id=row[0],
        opens_days_before_expiry=int(row[1]),
        closes_days_after_expiry=int(row[2]),
        credit_percent=Decimal(row[3]),
        effective_from=date.fromisoformat(row[4]),
        reference=row[5],
    )


def _claim_line(row: tuple) -> ClaimLine:
    return ClaimLine(
        batch=BatchKey(row[1], row[2], row[3], date.fromisoformat(row[4])),
        location_id=row[5],
        units=int(row[6]),
        rate=Decimal(row[7]),
        credit_percent=Decimal(row[8]),
        gst_rate=Decimal(row[9]),
        bought_on=date.fromisoformat(row[10]) if row[10] else None,
    )


def _settlement(row: tuple) -> Settlement:
    return Settlement(
        claim=row[0],
        credit_note=row[1],
        amount=Decimal(row[2]),
        received_on=date.fromisoformat(row[3]),
        recorded_by=row[4],
    )


def _approval_request(row: tuple) -> ApprovalRequest:
    return ApprovalRequest(
        number=row[0],
        approval_id=row[1],
        kind=row[2],
        digest=row[3],
        summary=row[4],
        requested_at=datetime.fromisoformat(row[5]),
        session_id=row[6],
    )


def _decision(row: tuple) -> Decision:
    return Decision(
        request=row[0],
        approved=bool(row[1]),
        decided_by=row[2],
        at=datetime.fromisoformat(row[3]),
        note=row[4],
    )


def _hold(row: tuple) -> Hold:
    return Hold(
        id=row[0],
        batch=BatchKey(row[1], row[2], row[3], date.fromisoformat(row[4])),
        status=BatchStatus(row[5]),
        at=datetime.fromisoformat(row[6]),
        reason=row[7],
        reference=row[8],
        placed_by=row[9],
    )


def _release(row: tuple) -> Release:
    return Release(
        hold_id=row[0], at=datetime.fromisoformat(row[1]), reason=row[2], released_by=row[3]
    )


def _ceiling(row: tuple) -> CeilingPrice:
    return CeilingPrice(
        molecule=row[0],
        strength=row[1],
        unit=row[2],
        ceiling=Decimal(row[3]),
        effective_from=date.fromisoformat(row[4]),
        reference=row[5],
    )


def _notice(row: tuple) -> RecallNotice:
    return RecallNotice(
        reference=row[0],
        source=row[1],
        recall_class=RecallClass(row[2]),
        received_at=datetime.fromisoformat(row[3]),
        batch_no=row[4],
        manufacturer=row[5],
        product=row[6],
        expiry=date.fromisoformat(row[7]) if row[7] else None,
    )


def _normalise(number: str) -> str:
    return "".join(number.split()).upper()


def _statements(script: str) -> Iterator[str]:
    """Split a migration into single statements, keeping trigger bodies whole."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        raise RecordsError("a migration ends with an incomplete statement")
