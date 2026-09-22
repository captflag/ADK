# ADR 0008: Holds on batches are an append-only log, separate from stock

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

ADR 0004 makes blocking a recalled batch automatic and requires both the block
and its release to be recorded. A recall report then has to answer questions
about time: when was sale stopped, was that within 24 hours of the notice, and
was anything sold after the notice but before the block, or while it was in
force?

The domain model already had a status on each batch record (live, quarantined,
blocked, recalled). A single status field answers only "what is it now", and
changing it would erase when it changed, who changed it and why. Marg's batch
table has no equivalent, and Batchward reads Marg without writing to its tables
(ADR 0006).

## Decision

Holds are records in their own append-only log. Placing a hold appends the
batch, the status it imposes, the moment, the reason, the notice or document
behind it, and who placed it (`system` for automatic blocks). Lifting a hold
appends a release with its own moment, reason and person. Nothing is edited or
deleted.

- A batch's status at any moment is its own recorded status or the most severe
  hold in force then, whichever is stricter.
- First-expiry-first-out allocation receives batch records with holds applied
  as of the moment of sale, so held stock is never picked.
- A hold never changes stock. Balances, tracing and valuation are unaffected.
- The log belongs to Batchward, not Marg.

## Consequences

- Recall reports can show when a batch was blocked, whether the stop-sale
  deadline was met, and which sales, if any, happened before or during the block.
- A wrong block is corrected by a visible release, not by deleting evidence.
- Holds were first kept only in memory. They are now persisted in Batchward's
  own records database (ADR 0010). Billing must still consult them, which a
  read-only bridge to Marg cannot enforce inside Marg itself.

## Alternatives rejected

- **Changing the status on the batch record.** Loses the time, author and
  reason for each change, and cannot answer "was it blocked at 14:05?".
- **Recording holds as stock movements.** A hold moves no stock; mixing the
  two would distort balances and tracing.
