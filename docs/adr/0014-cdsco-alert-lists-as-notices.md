# ADR 0014: CDSCO alert lists are imported row by row as recall notices

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

Most recalls a stockist acts on do not arrive as a company's letter. Every
month CDSCO publishes the samples State and Central laboratories found not of
standard quality, or spurious, as a table with dozens or hundreds of rows: the
product, batch number, dates of manufacture and expiry, the manufacturer and
the result. Checking each row by hand against every batch ever held is exactly
the kind of work that gets skipped.

The lists give no recall class, write dates in many ways ("Oct-2027",
"10/2027", "15/10/2027"), and are published as PDFs.

## Decision

- An alert list is imported from its table saved as CSV, columns named as the
  list names them. The layout is an assumption, like the Marg layout (ADR 0006),
  until real lists are loaded.
- Each row becomes a recall notice matched as strictly as any other (ADR 0002,
  ADR 0004). Only a row whose batch number, manufacturer, product and expiry
  month all agree with a batch blocks it; every other resemblance is raised for
  review.
- A row's reference is the list's name and the row's own number, so importing
  the same list again records nothing new and places no second block.
- The person importing a list chooses the recall class. The list does not give
  one, and choosing a class sets the deadlines, which is a judgement Batchward
  does not make.
- An expiry that cannot be read is left out of the notice rather than guessed,
  so that row can only raise batches for review, and the import says which rows
  those were.
- Each row is received in its own transaction. A row that cannot be recorded,
  such as a changed row under a number already recorded, is reported and the
  other rows still go ahead, because one bad row must not delay blocking a
  recalled batch named in another.

## Consequences

- A whole monthly list is checked against every batch ever held in one command,
  with the same guarantees as a notice entered by hand.
- One class applies to a whole import. A list mixing spurious drugs (usually
  Class I) with minor failures needs importing in parts, or a person must enter
  the serious rows separately.
- Reading the PDF, and fetching new lists from CDSCO, are not built.

## Alternatives rejected

- **Blocking on batch number alone for alert lists.** Batch numbers repeat
  across manufacturers; strict matching is what keeps good stock selling.
- **A default recall class.** A default would set deadlines nobody chose.
