# ADR 0011: Ceiling prices are dated data, and billing above them is blocked

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

Under the Drugs (Prices Control) Order 2013, a scheduled formulation's maximum
retail price may not exceed its notified ceiling price plus GST. NPPA revises
ceilings every 1 April and notifies new ones during the year. A batch whose
printed MRP was lawful when it was made can therefore be above the ceiling in
force on the day it is sold. A non-scheduled formulation's MRP may not rise
more than 10% in twelve months.

Since the amendment of 30 June 2026, once a manufacturer shows it circulated a
revision, the overcharge is recoverable from the distributor or stockist who
sold above the notified price, with 15% simple interest a year. A stockist
needs to know before billing, and needs to know what past sales have cost.

## Decision

- Every ceiling price is a record with its formulation, its price per unit
  sold excluding GST, the date it takes effect and the notification it comes
  from. A ceiling is in force from its date until a later one replaces it, and
  every check looks up the ceiling in force on the day in question.
  Formulations are compared ignoring letter case and spacing, so "10MG" is
  "10 mg". A ceiling must lie between one paisa and one crore rupees a unit.
- Before billing, a batch of a scheduled formulation is **blocked** when its
  printed MRP is above the ceiling plus GST in force that day, and the block
  names the notification. When no ceiling is on record the batch is **warned**,
  not allowed, because a missing record is not proof of compliance.
- Exposure counts every past sale of a blocked batch, and of a batch whose MRP
  rose more than 10% above the lowest MRP of the same item made in the twelve
  months before. The 10% limit is compared unrounded, so a rise over it by less
  than half a paisa still counts; the allowed MRP is the limit rounded down to
  the paisa. The overcharge is the MRP above the allowed price, for every unit
  sold, with simple interest from the sale date.
- With no ceiling on record the agents' price tools still check: every
  scheduled item is warned and price rises are still found.
- These are Batchward's reading of the rules, to be checked by a lawyer before
  they are relied on. The system prepares and flags; people decide.

## Consequences

- A block can always show where the limit came from and since when.
- Returns are not netted against sales, so exposure errs high rather than low.
- Ceilings are held per unit sold. NPPA notifies most ceilings per tablet, so
  loading real notifications needs a conversion by pack size, which is not
  built. Nor are dated GST rates: the item's current rate is used.
- Billing happens in Marg (ADR 0006), so the block is a check Batchward runs,
  not a stop inside Marg's billing screen.

## Alternatives rejected

- **Checking against today's ceiling only.** It would call past sales wrong
  that were lawful when made, and miss past sales that were not.
- **Allowing a scheduled formulation when no ceiling is found.** A gap in the
  data would read as compliance.
