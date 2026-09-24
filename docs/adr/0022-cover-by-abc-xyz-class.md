# ADR 0022: Each item's cover follows its ABC-XYZ class, as a replay of demand chose

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

ADR 0020 ordered every item to the same cover: order when the stock that will
sell falls below the lead time and 10.5 days of demand, up to the lead time and
21 days. That is really two numbers, the same for every item: 10.5 days of
safety, held against demand running ahead of the forecast while an order is on
its way, and 10.5 days of demand brought in by each order. ADR 0020 noted that
the ABC-XYZ classification already existed and the order rule did not use it.

The textbook answer is that steady items need less safety than erratic ones,
and that items carrying the money should be ordered often and cheap ones
seldom. How many days each class should get is not in the textbook; it depends
on the demand. Choosing numbers by feel would be the kind of unsupported
judgment ADR 0007 refused for forecasting methods.

The simulator knows demand in full: what was sold, and, since this decision,
what went short each day. A replay can run any rule over that demand and count
what it would have served, held and ordered.

## Decision

- An item's cover is **safety days by its XYZ class** and **days per order by
  its ABC class**:

  | | Safety days | | Days per order |
  |---|---|---|---|
  | X (steady) | 7 | A (most of the money) | 7 |
  | Y (variable) | 10.5 | B | 10.5 |
  | Z (erratic or rare) | 10.5 | C (the long tail) | 28 |

  An item is ordered below lead time plus safety, up to lead time plus safety
  plus days per order, in days of its forecast demand. Its ABC class is judged
  on the year of consumption at cost before the day, and its XYZ class on the
  same 26 weeks of sales its forecast uses.
- The table was chosen by replay (`batchward cover-backtest`): two simulated
  years of demand as chemists asked for it, the forecast Batchward uses made
  each week, and orders taking the 2 to 5 days simulated companies took. Every
  safety of 2 to 21 days and every order of 7 to 28 days was replayed on every
  item. The choice is the least stock at cost that:
  - fills as many of the units asked for as the one-cover rule did, to within
    a twentieth of a percent, in each ABC class and in each XYZ class, all its
    items together; and
  - places no more order lines than the one-cover rule, in all.

  Fill is judged by ABC class and by XYZ class rather than by each of the nine
  cells, because a cell can hold one item, whose luck would decide the choice.
- The table was chosen on the first nine months after the forecast's 26 weeks
  of history, and checked on the next nine:

  | Simulation | One cover of 21 days | By class | Fill, one cover → by class | Order lines |
  |---|---|---|---|---|
  | seed 42, where it was chosen | ₹15,80,458 | ₹11,36,446 (−28.1%) | 99.99% → 99.98% | 10,200 → 9,997 |
  | seed 7 | ₹15,93,198 | ₹11,58,140 (−27.3%) | 99.99% → 99.98% | 9,280 → 9,031 |
  | seed 2024 | ₹16,71,750 | ₹12,10,629 (−27.6%) | 99.98% → 99.98% | 9,820 → 9,437 |

  Stock is the average held at cost. Almost all of the saving is on A items,
  ordered weekly, and on steady items, which held more safety than they
  needed: AX items held a third less and still filled 100.00%. C items hold
  more, ordered monthly, and that saves more order lines than weekly A orders
  add. Erratic items fill better than before (seed 42: Z from 95.95% to 97.90%),
  because most are C items and bigger orders carry them through.
- Seeds 42 and 2024 chose this table on their own. Seed 7 chose 7 safety days
  for Z items, which cost Z items a point of fill when checked. Z keeps
  10.5 days: few items, with little money in them, and the replay cannot see
  them expire.
- Orders, the brief and the forecaster's tool use the table. `--cover-days N`
  still orders every item to one cover of N days, as ADR 0020 did. An order
  put up for approval under one cover is planned again under the same one.

## Consequences

- About a quarter less money in stock for the same service, in the simulation.
  Real demand will differ; `cover-backtest` can be run again as the simulator
  grows, and on real sales once shortfalls can be recorded.
- `orders suggest` shows each item's class and cover, so a person can see why
  an order is the size it is.
- A items are ordered about weekly, which suits companies that take weekly
  orders. The replay does not know which days a company takes orders.
- The replay does not see expiry. Holding more of a C item risks more of it
  expiring, and C items now hold more; expiry claims (ADR 0018) and the
  expiry-risk figures will show whether that matters.
- Quantities are still not rounded to case sizes, which would change what each
  order brings in.
- In real use, sales hide the demand a stockout turned away, so a replay on
  real data would understate what the one-cover rule missed.

## Alternatives rejected

- **Days picked by rule of thumb per cell.** Nothing would say they are right,
  and nothing would say when they stop being right.
- **Judging fill in each of the nine cells.** Tried first: cells with one or a
  few items made the choice follow chance, and it changed with the random
  delivery times.
- **Least stock at a fixed fill target, such as 99.5%.** The one-cover rule
  already filled close to every unit on A and B items; a target below it would
  have bought the saving by serving chemists worse.
- **Safety from each item's own demand variance.** The formula assumes
  normally distributed demand and a known lead time, neither of which pharma
  distribution gives; days by class can be read, checked and replayed.
