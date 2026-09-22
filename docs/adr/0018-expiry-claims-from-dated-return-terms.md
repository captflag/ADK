# ADR 0018: Expiry claims are drafted from dated return terms and made only on approval

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

A stockist is owed money on every batch that will not sell before it expires:
each company takes back expiring stock and credits it, but only inside a window
around the batch's expiry, and at a share of its value that differs by company.
The windows close unnoticed, and stock that could have been credited is written
off instead. Claims that are sent sit with companies for months, and nobody adds
up how much.

No company publishes its terms centrally. They are agreed with the area manager,
sent as letters and circulars, and they change. Expired goods returned for credit
also carry GST: the company issues a credit note under section 34 and the
stockist reverses the input credit, at the rate on the original bill, which for
stock bought before the rate cut of 22 September 2025 may be higher than today's.

## Decision

- Each company's return terms are dated records, like ceiling prices (ADR 0011):
  how many days before expiry its window opens, how many days after it closes,
  and the share it credits. The terms in force on a day are the latest to take
  effect on or before it. The stockist imports them from a file; the simulator
  makes up plausible terms per company.
- What is worth claiming is the stock that will not sell before it expires, as
  the expiry risk already finds it (ADR 0003): everything on the breakage and
  expiry shelf, stock past expiry, and what the forecast leaves over on sellable
  shelves. It is valued at cost times the company's credit share, before GST.
- `claims windows` shows, per batch, whether its window is open, closing within
  15 days, not yet open or closed, and adds up what was written off in the last
  90 days while its window was still open: claims lost.
- A claim covers one company on one day, numbered from the two, so drafting it
  again that day drafts the same claim. It lists each batch by where its units
  are, returns shelf first. Approving it (ADR 0005) writes the claim sheet, a
  letter asking for a section 34 credit note, and a purchase-return voucher for
  Marg's import (ADR 0006). Stock bought before the GST rate cut is flagged in the
  letter for checking against its original bill.
- Units already claimed are not claimed again until Marg shows them returned, as
  a purchase return under the claim's number.
- Claims and the credit notes that settle them are kept in the records database
  (ADR 0010), in a sixth migration, with the return terms. `claims list` shows
  what each company still owes on each claim, and its age.
- The approval workflow of ADR 0005 is shared: asking, rejecting and resuming
  are the same for every kind of action, and each kind supplies only the node
  that carries it out.

## Consequences

- A claim is drafted in rupees before its window closes, and a write-off inside
  an open window shows as money lost.
- The analyst and the morning brief report claim windows closing soon and money
  still owed on claims, from a tool, like every figure (ADR 0003).
- Claim values are at cost. A company that credits at another price, such as the
  price to retailer, is under-claimed by this reckoning until the terms carry a
  price basis.
- Breakage claims are not built: the ledger does not tell breakage from expiry.
- Marg publishes no import layout, so the return voucher's columns are an
  assumption, like the purchase voucher's (ADR 0016), and so is Marg keeping the
  claim number on the purchase return it imports.
- The simulator's terms are made up within plausible ranges and are unverified.

## Alternatives rejected

- **Claiming all stock near expiry.** Stock that will sell before it expires
  earns more sold than credited; the forecast decides what will not.
- **One set of terms for every company.** Windows and credit shares differ, and a
  single rule would claim too early from some and too late from others.
- **Numbering claims in a sequence.** The claim's number is in its files, and the
  files are what a person approves, so it must be known when the claim is
  drafted, not assigned when it is approved.
