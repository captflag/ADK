# ADR 0001: Stock is an append-only ledger of batch movements

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

A pharma distributor has to answer questions about the past, not just the
present: which chemists received a batch that has just been recalled, what stock
was on hand on 31 March, why the books and the shelf disagree. A mutable table
of stock levels can only answer "how much now", and every correction destroys
the evidence of what happened.

## Decision

Every change to stock — purchase, sale, return, transfer, adjustment, write-off —
is recorded as an immutable movement against a batch at a location, citing the
document behind it. Stock levels are never stored; they are derived by replaying
movements. A mistake is corrected by appending a reversing movement, never by
editing or deleting the original.

The ledger refuses any movement that would drive a batch's balance at a location
below zero at any point in its history, including movements that arrive
backdated.

## Consequences

- Recall tracing, "stock as of a date" and reconciliation with the ERP become
  queries over one table.
- The full history is an audit trail by construction.
- Balances cost a replay. Current balances are maintained incrementally;
  historical balances replay on demand, and can be snapshotted later if that
  becomes slow.
