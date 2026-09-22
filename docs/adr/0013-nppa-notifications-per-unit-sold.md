# ADR 0013: NPPA notifications are imported from their tables and priced per pack stocked

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

The Price Guard holds ceiling prices per unit a stockist sells: a strip, a
bottle, a vial (ADR 0011). NPPA notifies them differently. Each notification is
an S.O. in the Gazette with a table: the scheduled formulation, its dosage form
and strength, the unit priced (most often "1 Tablet" or "1 ml") and the ceiling
price for that unit, excluding GST. Typing each converted price in with
`batchward ceilings add` is slow, and each conversion by hand is a chance to
multiply by the wrong pack.

NPPA publishes these tables as PDFs. Reading PDFs reliably is a separate
problem, and a person can copy a table to a spreadsheet in minutes.

## Decision

- A notification is imported from its table saved as CSV, with the columns
  named as the notification names them. The notification's number and the date
  it takes effect are given with the command. The column layout is an
  assumption, like the Marg layout (ADR 0006), until real notifications are
  loaded.
- Each row is matched to the items stocked by molecule and strength, ignoring
  letter case and spacing. Its price is multiplied out by what each item's pack
  holds in the notified unit: ₹6.42 for 1 tablet is ₹64.20 for a strip of 10
  tablets, and ₹16.50 for 1 ml is ₹165.00 for a 10 ml vial. Every pack of a
  formulation gets its own ceiling; brands of the same pack share one.
- Nothing is guessed. A pack that does not say how much it holds in the notified
  unit, such as a prefilled pen priced per ml, is listed for a person to enter
  by hand. A notified formulation no item matches is counted.
- An item that matches a notified formulation but is not marked as scheduled in
  the billing data is reported, because the Price Guard only checks scheduled
  items and would otherwise pass it silently.
- All the prices from one notification are recorded in one transaction. A price
  that contradicts one already recorded for that pack and date stops the whole
  import, and nothing from it is recorded.

## Consequences

- A notification of hundreds of rows becomes ceilings for every pack stocked in
  one command, and the dry run shows each conversion before anything is kept.
- A combination whose strength is notified differently from how the item
  records it ("500 mg + 125 mg" against "625 mg") matches no item and is only
  counted. Mapping such names needs a table of equivalents, which is not built.
- Reading the notification PDF, and fetching new notifications from NPPA, are
  not built.

## Alternatives rejected

- **Storing ceilings per tablet and converting at every check.** Every check
  would depend on parsing the pack each time, and a pack that cannot be parsed
  would fail at billing, not at import where a person can fix it.
- **Assuming a pack size when the unit does not say.** A wrong assumption would
  put a wrong ceiling on record, which is worse than none.
