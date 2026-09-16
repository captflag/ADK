# ADR 0002: A batch is identified by company, item, batch number and expiry

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Batch numbers are assigned by each manufacturer to its own production runs.
Nothing stops two companies — or one company, years apart — printing the same
batch code. A recall notice names a product, a manufacturer and a batch; matching
on the batch string alone would freeze unrelated stock.

## Decision

A batch's identity is the tuple (company, item, batch number, expiry date).
Batch numbers are normalised only for whitespace and letter case. Characters
that are easily confused on paper, such as `0` and `O`, are **not** normalised:
that ambiguity is resolved by a person at intake, not guessed by the system.

## Consequences

- A recall matches exactly the stock it names, and nothing else.
- Intake must capture all four fields; a line missing any of them cannot become
  stock.
- Two records differing only in a misread character are distinct batches until
  a person merges them with a reversing correction.
