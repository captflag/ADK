# ADR 0020: Purchase orders are drafted from the forecast and placed only on approval

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

A stockist orders from each company every week, from memory and a glance at the
shelves. The simulated owner tops stock up when it falls below half of 21 days'
cover, from its recent sales, counting stock that will expire before it sells as
if it will sell. Batchward already forecasts every item's demand by its pattern (ADR 0007), knows what will expire before it sells,
knows which batches are held (ADR 0008), and matches deliveries against the
orders they fill (ADR 0016, 0017). It did not say what to order.

## Decision

- An item's position is the stock that will actually be there to sell: units on
  sellable shelves, less what the forecast says will expire first and anything
  under a hold, plus what is still due on orders placed through Batchward.
- Orders follow a reorder point and an order-up-to level, both from the routed
  forecast per day: order when the position falls below the demand over the
  lead time and half the cover, up to the demand over the lead time and the full
  cover. The defaults are 4 days' lead time and 21 days' cover, the simulated
  owner's own habit, and both can be changed per run.
- An item that has not sold is never ordered, whatever its stock.
- An order covers one company on one day, numbered `PO/<company>/<yymmdd>` from
  the two, so drafting it again that day drafts the same order. It is valued at
  each item's last purchase rate. Approving it (ADR 0005) writes the order as a
  sheet and as a message for the company, and records it in the records database
  (ADR 0010), in an eighth migration.
- Units still due on orders placed in the last 30 days count as coming. An order
  older than that with units never delivered is not counted; it is listed as
  overdue, for someone to chase.
- A delivery that quotes an order placed through Batchward is matched against it
  without an order file (ADR 0016), and what it receives reduces what is due
  (ADR 0017).
- The forecaster answers "what should I order?" from a tool, and the morning
  brief gives a line on what to order today (ADR 0003).

## Consequences

- What to order is worked out in units, company by company, with the reason for
  each line: the forecast per day, the usable units and the units already due.
- Orders placed outside Batchward, by phone or on a company portal, are not
  known, so their units are not counted as coming. Placing orders through
  Batchward is what makes the count right.
- Quantities are not rounded to case sizes or minimum orders, which Marg's item
  master does not carry; a person rounds them when approving.
- Schemes (free units for buying more), company credit limits and budgets are
  not considered.
- One cover for every item. Classifying items by value and variability
  (ABC-XYZ) could set cover item by item; the classification exists, the policy
  does not use it yet.

## Alternatives rejected

- **Ordering to the forecast without subtracting what will expire.** Stock that
  expires on the shelf would count as cover, and the item would run out.
- **Counting every order ever placed as coming.** A company that never delivers
  would block the item from being ordered again forever.
- **Placing orders without approval.** An order commits money; it is a draft
  until a person says yes (ADR 0005).

## Amendment, 2026-09-22: cover by class

One cover for every item is no longer the default. Each item is ordered to the
cover of its ABC-XYZ class, safety days by XYZ and days per order by ABC,
chosen by replaying simulated demand (ADR 0022). `--cover-days` still orders
every item to one cover.

## Amendment, 2026-09-22: whole cases

Quantities are now rounded up to whole cases where a product's case size is on
record in Batchward's records, and stay in units where it is not (ADR 0023).
