# ADR 0024: Units that arrive damaged are counted apart, never posted, and debited back

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

Breakage is money a stockist loses unless a company makes it good. It happens
in three places:

1. **On the way from the company**: cartons crushed in transit, bottles broken,
   strips wet. It is found when the godown counts the delivery, and the usual
   remedy is a debit note against the bill, with the damaged units kept aside
   for the company's representative to collect or have destroyed.
2. **In the godown**, in handling. That is the stockist's own loss.
3. **At chemists**, who send broken or damaged stock back. Companies that
   accept it do so under their breakage and expiry terms.

ADR 0016 matches a delivery against its bill, its order and the godown's count,
and puts units billed but not received on a debit note. The count had no way to
say that units arrived but arrived damaged, so a person either counted them as
received, and Batchward posted them as stock to sell, or left them out, and the
debit note called them not received.

ADR 0018 left breakage claims out because the ledger does not tell breakage
from expiry: Marg records a chemist's return to the breakage and expiry shelf
without saying which it was.

## Decision

- The count sheet takes an optional **Damaged** column (also read as
  "Broken" or "Breakage"): of the units counted for a batch, how many arrived
  damaged. More damaged than counted is refused, naming the line.
- Damaged units are **never posted**. Units fit to sell are posted up to those
  billed, then free units, as before.
- Billed units that arrived damaged go on the **debit note**, in a section of
  their own after the units not received, at the bill's rate less its discount,
  with GST. The note says the damaged units are kept aside for the company to
  collect or have destroyed.
- Damaged units stay **due on the order** (ADR 0017), as units not received do,
  so the company knows to send them again.
- Damaged units beyond those billed, such as damaged free units, are noted for
  the company's representative; nothing is debited for them.
- `intake receive` shows the damaged units of each batch and says the debit note
  covers units not received or received damaged.
- Breakage found later, in the godown or returned by chemists, is still not
  claimed. Marg's sales return does not say why stock came back, and inferring
  it (a return far from expiry must be breakage) would mistake near-expiry
  returns made before a company's claim window opens.

## Consequences

- Damaged stock no longer enters Marg as stock to sell, and never reaches a
  chemist or the expiry risk figures.
- The debit note carries both reasons a billed unit was not accepted, so the
  company's credit note can settle the bill in one go.
- The simulated deliveries arrive undamaged; the demo shows damage only when a
  person adds the column to a count sheet.
- Transit insurance claims against the transporter are not drafted.
- Chemists' breakage returns stay mixed with expiry on the returns shelf, and
  are claimed, if at all, as expiry when the batch's window opens (ADR 0018).
  Claiming them as breakage needs the reason recorded when they come back,
  which a later decision can add as a record of Batchward's own (ADR 0010).

## Alternatives rejected

- **Posting damaged units to the breakage and expiry shelf.** They would be
  stock bought and held, to be claimed later; debiting them on the bill means
  they were never bought, which is what the company agrees to at delivery.
- **A separate damage report beside the debit note.** The company settles a bill
  with one credit note; two documents for one bill invite one being lost.
- **Guessing chemist breakage from how far a returned batch is from expiry.**
  It would claim near-expiry returns as breakage, before a company would accept
  them as either.

## Amendment, 2026-09-24: breakage chemists send back

The reason a return came back is now recorded against the credit note, and
breakage marked that way is claimed from its company (ADR 0025). Godown handling
breakage is still the stockist's own loss.
