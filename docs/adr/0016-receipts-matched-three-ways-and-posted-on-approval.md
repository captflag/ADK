# ADR 0016: A delivery is matched against its order and count, and posted only on approval

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

An invoice that checks as ready (ADR 0015) says what a company billed, not what
arrived or what was asked for. Short supply that nobody claims is money lost;
stock that was never ordered, or more than was ordered, ties up cash; and an
invoice posted twice doubles stock that does not exist.

Posting itself is constrained. Marg is the system of record and Batchward writes
to it only by producing files for Marg's own import, for entries a person has
approved (ADR 0006). Approvals are meant to be recorded in an approval table
(ADR 0005).

## Decision

- A delivery is matched three ways: the invoice reading, the purchase order it
  quotes, and the godown's count, batch by batch, with expiry and manufacture
  month read off the packs.
- What is posted is what arrived: counted units up to those billed, then free
  units. Billed units that did not arrive go on a draft debit note. Units
  beyond those billed and free, a product not on the order or more than was
  ordered, a pack expiry that disagrees with the invoice, a batch not counted or
  counted but not billed, and a new batch without its manufacture month all
  wait for a person.
- A receipt is posted only when a named person approves it. Approval writes a
  purchase voucher for Marg's import, with one row per batch of what arrived,
  and the debit note if there is one. Marg publishes no import layout, so the
  voucher's columns are an assumption, like the table layout the bridge reads.
- Every approval is recorded in Batchward's records database, append-only
  (ADR 0010): what was approved under a stable id (the supplier and bill
  number), who approved it and when, and a SHA-256 digest of exactly what was
  written. Approving the same thing again writes nothing. Approving a different
  version of an approved bill is refused, so a bill is never posted twice.
- The approval and the files happen together: the files are written inside the
  approval's transaction, and if they cannot be written the approval is not
  recorded.

## Consequences

- Short supply is claimed as it is found, from the count, not months later.
- The records database has a third migration, an approvals table. It is the
  audit trail ADR 0005 describes, used first by the command line.
- The approval was at first a command-line flag. It now goes through the
  graph workflow that asks for approval and resumes when it is given (ADR 0005,
  amended 2026-09-21). Approval buttons in WhatsApp are not built.
- An order delivered over several invoices was at first matched invoice by
  invoice against the whole order, so billing beyond the order across invoices
  was not caught. ADR 0017 records what each approved bill received against its
  order.
- The simulator now records the purchase orders it places and which order each
  delivery filled, and writes each delivery's count sheet and order with its
  invoices.

## Alternatives rejected

- **Posting the invoice as billed and returning short units later.** Stock would
  show units the godown never held, and the shortage would depend on someone
  remembering to raise it.
- **Writing Marg's tables directly.** Forbidden by ADR 0006.
- **Recording approval after the files are written.** A crash between the two
  would leave a posting nobody is recorded as approving.
