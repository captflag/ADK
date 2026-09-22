# ADR 0010: Batchward keeps its own records in its own database

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Batchward reads stock from Marg and never writes to Marg's tables (ADR 0006).
But some records exist only because Batchward acted. A recall notice it
received and the block it placed on a batch (ADR 0004, ADR 0008) must survive a
restart. They must be there when the recall report is due days later, and when
an inspector asks months later.

These records have the same needs as the ledger. They are evidence, so nothing
may be edited or deleted. Receiving a notice and blocking its batches must
happen together or not at all. A notice may be received twice, by a nightly
job and by a person, without placing a second block.

## Decision

- Batchward's records live in a database file of their own, separate from the
  Marg database, one per business. The portfolio build uses SQLite, which needs
  no server and ships with Python.
- Notices, holds and releases are only ever added. Triggers in the database
  refuse every UPDATE and DELETE, so the rule holds even for someone editing the
  file by hand.
- Receiving a notice records it and places its holds in one transaction that
  takes the write lock at the start, so two writers cannot both see a batch
  unblocked and block it twice.
- A notice is identified by its own reference. Recording the same notice again
  is harmless: it places no second block, and never blocks again a batch a
  person released. A different notice under a used reference is refused, because a
  correction is a new notice.
- The schema version is stored in the file. A file from a newer Batchward, or a
  file that is not a Batchward records database, is refused rather than read.
- Agents read these records through a read-only tool, and never place or lift a
  hold. Reading never creates a records file, and a file that is locked, from a
  newer Batchward, or not a records database is reported, not read.

## Consequences

- A block survives restarts, and a recall's report can be produced at any time
  from Marg's ledger and these records together.
- Billing still happens in Marg, which knows nothing of these holds. Until
  Marg's own import path can mark a batch unsellable, a blocked batch is only
  visible in Batchward. The block is stopped from being forgotten, not from
  being billed.
- A multi-tenant product will need Postgres with the same rules: append-only
  tables, one transaction per notice, and row-level security per tenant.

## Alternatives rejected

- **Writing into Marg's tables.** Forbidden by ADR 0006, and Marg's schema is
  not ours to extend.
- **JSON files.** No transactions, no concurrent writers, nothing to stop edits.
- **Postgres now.** Right for a hosted product, but it would make a fresh clone
  of the portfolio build need a database server before the demo runs.
