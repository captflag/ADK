# ADR 0029: A block reaches billing as a transfer to a godown nothing is sold from

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

Batchward blocks a recalled batch the moment a notice names it exactly
(ADR 0004), and its own allocation will never sell a blocked batch. Marg does
the billing (ADR 0006), and knows nothing about any of it. Until now the README
said so plainly: "Marg still does the billing and does not yet see these
blocks." A recalled batch sitting in the main godown is one keystroke away from
going out on a bill, and the block that was placed in seconds does nothing to
stop it.

Batchward writes nothing into Marg's tables. What it may do is write a file for
Marg's import, as it already does for a purchase voucher on an approved receipt
(ADR 0016) and a return voucher on an approved claim (ADR 0018). The question is
what that file should say.

Marg has no field this project can rely on for "this batch is blocked": its
column layout is an assumption already (ADR 0006), and inventing a flag in
someone else's schema is not something to build a recall on. What every ERP and
every stockist does understand is a godown, and stock moved into one nothing is
sold from.

## Decision

- **A block is carried into billing as a stock transfer.** Every unit of a held
  batch still sitting in a godown stock is billed from moves to a quarantine
  godown, in Marg's own terms: a `TO` row out of the godown it leaves and a `TI`
  row into the quarantine godown, which is how Marg's `DIS` table records a
  transfer. The columns are an assumption, like every other import file's.
- **The quarantine godown must already be known to sell nothing.** A transfer is
  refused unless the godown is named in `BATCHWARD_UNSELLABLE_LOCATIONS`, so
  Batchward's own view of it agrees with what the office has set up in Marg.
  Moving recalled stock into a godown that still sells is worse than not moving
  it, because it looks like something was done.
- **It is made only on approval** (ADR 0005), through the same workflow as a
  receipt or a claim: the transfer is drafted again when the answer comes back
  and made only if it is still exactly what the person saw, since stock moves
  between the asking and the answering. It is numbered by the day, so drafting
  twice in a day drafts the same transfer and approving it twice writes nothing
  twice.
- **Stock whose hold is lifted comes back**, to the very godown it came from,
  read from the transfers already recorded. Units in quarantine that no record
  accounts for are reported and left where they are: guessing a godown would put
  recalled stock back on sale.
- **The voucher does nothing until somebody imports it**, and Batchward says so
  in the sheet, in the command's output, and every time `recall quarantine list`
  is run: it goes on reporting the gap, naming the transfer that is waiting, for
  as long as the stock is still where Marg can bill it.
- What was moved is kept in the records (ADR 0010) in a thirteenth migration:
  the transfer, its approval, and every move with the godowns it went between.

## Consequences

- A recall now reaches the till: after the transfer is imported, the batch is in
  a godown the biller does not sell from, and a bill for it needs somebody to
  move stock back deliberately.
- **Batchward still cannot stop a bill.** Between blocking and importing there
  is a window in which the stock is sellable in Marg, and the size of that
  window is a matter of office habit, not software. The list command is written
  to make that window loud rather than to hide it.
- Stock that comes back from a chemist after a transfer is not in it. The next
  transfer picks it up, which is why the list is worth reading again the next
  day, and why the command is drafted per day rather than once per recall.
- The office must set up one godown that nothing is sold from and name it in
  `BATCHWARD_UNSELLABLE_LOCATIONS`. Getting that wrong is the one way to make
  this feature actively misleading, so the transfer refuses to be drafted until
  it is right.
- Reversing a transfer that was imported by mistake is Marg's business, not
  Batchward's: the records show what was asked for, and a correcting transfer is
  a person's decision.
- The quarantine godown is not the breakage and expiry shelf. Mixing them would
  put recalled stock in front of the breakage claim (ADR 0025), which is the
  last place it belongs.

## Alternatives rejected

- **Writing a block into Marg's tables.** Batchward reads Marg and writes only
  through its import path (ADR 0006). Setting a flag this project has only
  guessed the existence of, in the system that raises the bills, is the riskiest
  thing it could do.
- **Setting the batch's stock to zero.** It hides the units instead of moving
  them, breaks every reconciliation (ADR 0006), and loses the stock a recall
  report has to account for.
- **A printed list for the biller to check by hand.** That is what exists
  already, and it is what a recall drill catches people failing to do.
- **Moving the stock to the breakage and expiry shelf.** It is the godown
  already marked unsellable, which is exactly why it is wrong: the expiry and
  breakage claims read that shelf, and would try to claim recalled stock from
  the company that recalled it.
- **Doing it automatically when a batch is blocked.** Blocking is the one
  automatic action in Batchward (ADR 0004) because it is reversible and stops
  nothing outside Batchward. Moving stock between godowns is a real movement in
  the billing system: it waits for a person.
