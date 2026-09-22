# ADR 0015: Invoices are extracted as printed text and checked in plain Python before posting

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

Stock arrives with a tax invoice listing many batches. Today someone types each
line into Marg, often later, and a mistyped batch number or expiry poisons
first-expiry-first-out, claims, recalls and memos from then on. A model reading
the invoice photo will make the same kinds of mistakes: 0 for O, a month
misread, a digit dropped, two columns swapped.

The extraction itself needs a vision model and an API key. What decides whether
an extraction can be trusted does not: the arithmetic, the GSTIN's check
character, dates, and the batches already on record.

## Decision

- The extraction target is a schema of text as printed. Nothing is converted to
  a number or a date while extracting, so a figure the model cannot read stays
  visibly unread instead of being guessed into shape (ADR 0003).
- Plain Python reads every field and checks the invoice. Every finding says what
  happens next:
  - **fix**: a field does not read or the invoice does not add up. The finding is
    a hint for extracting again: quantity times rate against the taxable value,
    GST against its rate, lines against totals, the GSTIN check character, an
    expiry that contradicts a batch already on record.
  - **ask**: the paper reads, but a person decides: a batch number easily misread
    as one already held ("J4021 or J4O21?"), a batch under a recall block, stock
    printed above its ceiling price, a supplier or product not on record, a
    batch billed twice, stock already expired.
  - **note**: posting can go ahead: short-dated stock, a rate far from the usual,
    an HSN code that differs from the record.
- The clerk, an agent with no tools, reads the document into the schema. The
  document is data: anything written in it is never an instruction. Where
  fields need fixing, the clerk reads the invoice again, at most twice. The
  request names the fields to read again and never the value the checks
  expected, so the clerk cannot copy a computed figure into place instead of
  reading it.
- A line is matched to an item only when exactly one item's brand appears in it
  whole, among the supplier's items, with a printed pack size settling a brand
  sold in several packs. Otherwise a person chooses.
- An invoice is ready to post only when nothing needs fixing or asking. Posting
  itself, to the ledger and to Marg, is not built.
- The simulator prints each day's deliveries from a company as an invoice. Those
  invoices are the ground truth: every one must check as ready, and each kind of
  misread injected into them must be caught.

## Consequences

- Most misreads are caught without a model: 661 simulated invoices raised
  nothing to fix or ask, and every injected misread was caught.
- A misread batch number that resembles no batch on record, on a batch never
  seen before, cannot be caught by checks. It needs the model to read it twice,
  or a person when the model is unsure.
- The simulator now never gives one item's batch number to two batches, as a
  manufacturer would not; before, two batches could share a number with
  different expiries.
- Reading with the real model has only been tested offline, with a scripted
  model; the live test needs an API key. Photos and PDFs go to the model as
  they are; how well it reads handwriting or a poor photo is not yet measured.
- The three-way match against purchase order and count, and posting, are the
  next steps and are not built.

## Alternatives rejected

- **Letting the model return numbers and dates.** A model that cannot read "1O"
  returns 10 or 1, and nothing downstream can tell.
- **Checking with the model.** Arithmetic and check characters are exact; a
  model asked to verify them is another source of the same errors.
- **Telling the model the value that would add up.** It would make the check
  pass without the paper being read again, which defeats the check.
