# ADR 0004: Blocking a recalled batch is the only automatic action

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Agents draft and people approve: purchase orders, claims, write-offs, credit
blocks and messages to chemists all wait for a human yes. Recalls are the
exception worth examining. For a Class I recall, CDSCO's recall guidelines expect
sale to stop within 24 hours. An approval that waits for an owner who is asleep
or travelling makes the business less compliant, not more.

## Decision

When an alert matches a batch exactly (ADR 0002), the system immediately blocks
further sale of that batch and reports that it has done so. Everything that
follows — notices to chemists, the report to the Drugs Inspector — is drafted
for the pharmacist to approve. A human can lift the block in one action, and the
block and its release are both recorded.

- The block is dated when it is placed, not when the notice arrived, so a notice
  recorded late shows its stop-sale deadline met late.
- Receiving the same notice again never blocks a batch a person has released.
- A product named with a strength matches only an item of exactly that strength:
  2.5 mg is not 5 mg, and a combination naming a second molecule's strength is
  not the single-molecule item. A decimal or a hyphenated name is read whole.

## Consequences

- The worst case of a false match is a temporarily blocked batch, which is
  cheap and visible; the worst case of waiting is continued sale of a recalled
  drug.
- The matching rule must be strict: fuzzy or partial matches raise a review
  task instead of blocking.
