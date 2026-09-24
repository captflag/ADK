# ADR 0023: Orders are rounded up to whole cases, from case sizes kept in the records

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

Companies ship most products in whole cases: 10, 25 or 50 strips to a case.
An order for 90 strips of a product packed in 25s is filled as 75 or 100, or
sent back to be corrected. Batchward drafted orders in units, and ADR 0020 left
rounding to the person approving, because Marg's item master does not say how
many units a case holds.

Rounding changes what the covers of ADR 0022 do. Rounding up brings in more
than the cover asked for, which for a slow mover in a big case can be months of
stock. Rounding to the nearest case keeps closer to the cover, but sometimes
orders less than the cover asked for. The simulated companies supply whole
cases, and their case sizes are known, so both rules can be replayed.

## Decision

- Case sizes are kept in the records database (ADR 0010), in a tenth
  migration: product, units per case, the date the size is in force from, and
  where it comes from (a price list, a delivery). A later date records a change
  of packing; a different size for a date already recorded is refused. `orders
  cases import` reads them from CSV and `orders cases list` shows them.
- An order is **rounded up** to whole cases where the product's size is on
  record, and stays in units where it is not.
- A line where one case holds more than 90 days of the item's forecast demand
  is pointed out, for a person to check it will sell before it expires.
- Order sheets say how many cases and of what size, and the message to the
  company says "100, 4 cases of 25".
- The demo records every simulated product's case size.
- `batchward cover-backtest --cases up` and `--cases nearest` replay the
  choice of ADR 0022 with every order rounded. On the nine months the table was
  checked on (seed 42):

  | | One cover of 21 days | Batchward's table (ADR 0022) |
  |---|---|---|
  | In units, as ADR 0022 replayed | ₹15,80,458; 10,200 lines | ₹11,36,446; 9,997 lines |
  | Rounded up to whole cases | ₹19,35,995; 6,618 lines | ₹14,96,350; 6,954 lines |
  | Rounded to the nearest case | ₹18,61,010; 7,626 lines | ₹14,20,474; 8,179 lines |

  Every row fills at least 99.98% of the units asked for.
- Rounding up is chosen over rounding to the nearest case. Under Batchward's
  table, the nearest case held 5.1% less stock but placed 17.6% more order
  lines and served a little less (99.993% against 99.994%); seeds 7 and 2024
  showed the same, at 4.3% and 4.4% less stock for 16.0% and 15.5% more lines.
  Rounding up never orders less than the cover asked for.
- ADR 0022's table stays. Rounded up to whole cases, it holds 22.7% less stock
  than one cover rounded the same way (seeds 7 and 2024: 24.5% and 22.4% less),
  at the same fill to within a hundredth of a percent. It places 5% to 7% more
  order lines than one cover once orders are in whole cases, which ADR 0022's
  rule would not allow; choosing again under whole cases gives tables that
  differ from seed to seed (days per order of 14 and 28, or 21 and 21, for B and
  C), each holding 1.7% to 3.5% more stock than this one. The extra lines are A
  items ordered weekly, on orders placed per company in any case.

## Consequences

- Orders can be sent as drafted, without a person rounding each line.
- Whole cases raise the stock held: by a fifth to a third against ordering
  exact units, in the simulation. That was always the real position; ordering in
  units only looked leaner.
- An item with no case size on record is ordered in units, as before, and a
  wrong size on record gives wrong orders until a later one is recorded.
- Minimum order values, loose units some companies sell alongside cases, and
  schemes are not considered.
- The flag for a case lasting more than 90 days does not stop the order; the
  replay cannot see expiry, so a person decides.

## Alternatives rejected

- **Rounding to the nearest case.** About 5% less stock, bought with 15% to
  18% more order lines and orders that sometimes fall short of the cover.
- **Reading case sizes from Marg.** Marg's item master has no field for them
  (ADR 0006); keeping them in the records keeps Marg read-only.
- **One case size per product, without dates.** A change of packing would
  overwrite the old size, and the records are only ever added to (ADR 0010).
- **Choosing ADR 0022's table again under whole cases.** The choice moved with
  the seed, and every alternative held more stock.
