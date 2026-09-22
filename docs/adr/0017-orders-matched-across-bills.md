# ADR 0017: An order delivered on several bills is matched against what is still due

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

Companies rarely deliver a purchase order in one go. Products come on different
days, each on its own bill, and a short supply may follow later on a bill of its
own. ADR 0016 matched each bill against the whole order, so a company could bill
an order in full twice, on two bills, and each would pass.

What arrived against an order cannot be read back from Marg: its purchase
entries do not keep the stockist's order number. It is known only to Batchward,
when a person approves a receipt.

## Decision

- When a receipt is approved, Batchward records the units of each item the bill
  received against its order, in the same transaction as the approval, in a
  fourth migration of the records database (ADR 0010). Like every record there,
  these rows are only ever added to.
- What counts against an order is the billed units that arrived. Units on the
  debit note do not count, so a company may send the shortfall on a later bill,
  and free units do not count, since an order asks for units at the invoice
  rate.
- A bill is matched against what earlier approved bills received on the same
  order. A bill that takes an item beyond what was ordered, counting those
  earlier bills, waits for a person. The bill's own earlier approval is left
  out, so matching an approved bill again does not count it twice.
- If another bill on the same order is approved between matching and approving,
  the approval is refused and the bill is matched again.
- `intake receive` shows the order line by line: ordered, received on earlier
  bills, received now, and still due.

## Consequences

- Billing an order twice over several bills is caught before it is posted.
- The stockist sees what each order still waits for as each bill arrives.
- Matching without the records database, which cannot approve anyway, matches
  the bill against the whole order and notes that earlier bills were not looked
  up.
- Bills posted to Marg before Batchward, or without its approval, are not
  counted: Batchward knows only the receipts it approved.
- Order numbers are matched ignoring spaces and letter case, as bill and batch
  numbers are.

## Alternatives rejected

- **Reading earlier receipts back from Marg.** Its purchase entries carry no
  order number to match on.
- **Counting billed units rather than those that arrived.** A company that sends
  a debited shortfall on a later bill would be flagged for billing beyond the
  order.
- **Keeping a running balance per order line.** A balance is a record that
  changes, and the records database is only ever added to. The balance is
  worked out from the receipts instead.
