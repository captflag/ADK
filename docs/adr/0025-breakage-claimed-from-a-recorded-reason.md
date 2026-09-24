# ADR 0025: Breakage is claimed from a reason recorded against the credit note

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

ADR 0018 claims stock from a company when it will not sell before it expires.
ADR 0024 debits back units that arrive damaged. Between them sits the breakage a
chemist sends back: strips crushed in a delivery van, a bottle broken on their
shelf. It comes back on one of the stockist's own credit notes, onto the same
breakage and expiry shelf as near-expiry returns, and companies that take it
back credit it under their breakage and expiry terms.

ADR 0018 left it out because Marg's sale return does not say why stock came
back, and ADR 0024 confirmed the shortcut does not work: a return far from
expiry is not proof of breakage, because chemists send near-expiry stock back
months before a company's claim window opens. Until the reason is recorded,
breakage sits on the returns shelf and is claimed, if ever, as expiry when the
batch's window opens — often a year later, and sometimes never.

## Decision

- The reason a return came back is a record of Batchward's own (ADR 0010), in an
  eleventh migration: the credit note, whether it is **breakage**, **expiry** or
  **other**, who recorded it, when, and a note. A credit note is matched
  however it is typed, ignoring spaces and letter case. A second, different
  reason for the same credit note is refused; only a person corrects a mistake.
- `claims returns mark <credit note> --reason breakage --by <name>` records one,
  and `claims returns list` shows what is recorded and, with `--marg`, the
  returns in Marg that nobody has marked yet.
- Units that came back on a credit note marked breakage and are still in stock
  are claimable from their company **now**, whatever their expiry, at the share
  of value its return terms credit. What has since left the shelf, written off
  or sent back, is not counted again: what came back is capped at what is still
  there, and a reversed return counts for nothing.
- Those units are taken out of the expiry claim windows, so the same units are
  never claimed twice.
- Breakage still sitting where stock is sold from is listed apart and not
  claimed: it has to come off the selling shelf in Marg first, or it will be
  sold.
- A breakage claim is drafted, approved and recorded exactly as an expiry claim
  is (ADR 0005, 0018), through a workflow of its own, and is numbered
  `BR/<company>/<yymmdd>` against the expiry claim's `CL`. Its letter says the
  goods came back broken from chemists and asks for a credit note under section
  34 of the CGST Act; the sheet and the Marg return voucher are the same.
- `claims breakage list` shows breakage in stock by company at cost, and
  `claims breakage draft <company>` puts a claim up for approval. The analyst's
  claims tool reports it too.
- The simulated business now sends a few chemists' breakage back on credit notes
  of its own, on batches with well over six months left, so nothing in it can be
  mistaken for a near-expiry return; the demo records those credit notes as
  breakage.

## Consequences

- Breakage is claimed in the month it happens instead of waiting for an expiry
  window, and what was claimed as what is on record.
- A claim is only as good as the marking. An unmarked credit note is claimed as
  nothing, which is what happened before this decision, and marking a
  near-expiry return as breakage would claim it early. Who marked it, and when,
  is recorded.
- The reason covers a whole credit note. A credit note that mixes broken and
  near-expiry stock has to be split by the person raising it, as companies
  expect anyway.
- The terms record one share of value a company credits, and a breakage claim
  uses it. A company that credits breakage at a different rate than expiry
  needs that rate recorded separately, which this decision does not do.
- A company may refuse breakage it has not seen, or want it produced for
  inspection. The claim is a draft for a person to send, as every claim is.
- Godown handling breakage is not claimed: it is the stockist's own loss, and
  nothing in Marg tells it from a chemist's return but the credit note it came
  back on.

## Alternatives rejected

- **Guessing breakage from distance to expiry.** Rejected in ADR 0024, and
  measured here: chemists' near-expiry returns arrive before some companies'
  windows open, so the guess claims them as breakage.
- **A reason per line of a credit note.** More faithful, but a credit note is
  raised for one reason in practice, and per-line marking is work for the
  person and complexity here for no claim that changes.
- **Claiming breakage through the expiry windows with the window forced open.**
  The window is what the company agreed for expiring stock; breakage is a
  different promise, and a claim letter that calls breakage expiry invites the
  company to refuse it.
- **Making the stockist's own godown breakage claimable.** No company agrees to
  that, and Batchward would be drafting a claim that cannot be sent.
