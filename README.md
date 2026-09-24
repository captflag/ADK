# Batchward

[![CI](https://github.com/captflag/ADK/actions/workflows/ci.yml/badge.svg)](https://github.com/captflag/ADK/actions/workflows/ci.yml)

An AI back-office team for Indian pharma distributors, built on Google's
[Agent Development Kit](https://adk.dev).

A pharma stockist carries thousands of SKUs, and every one of them has a batch
number, an expiry date and a controlled price. Batchward watches all three:

- **Recall tracing** — match a recalled or substandard batch against everything
  ever held, block it, and list every chemist who received it.
- **Price guard** — hold notified ceiling prices and stop a line being billed
  above the allowed rate.
- **Expiry and claims** — see stock that will expire before it sells, and claim
  it from the company before the return window closes.
- **A morning brief** — the day's decisions on WhatsApp, each with a rupee figure
  and a button to approve.

## Status

Pre-alpha. **Phase 0 (foundations) is complete**, **Phase 1 (analysis and
agents) is under way**, and all three Phase 3 compliance engines — recall, the
price guard and the registrar — have begun. Nothing here is ready for real
business data. What exists so far:

- **A batch ledger** where stock is derived from an append-only history of
  movements, never stored directly. It refuses anything that would take a batch
  below zero at any point in time, corrects mistakes with reversing entries,
  and is checked against a reference model with property-based tests.
- **First-expiry-first-out allocation** that never sells expired, blocked or
  recalled stock.
- **Batch tracing**: every chemist who received a batch, what is still on hand,
  and every unit accounted for.
- **A simulated stockist** with seasonal demand, weekly purchase orders placed
  from past sales, whole-case ordering, short-dated deliveries, brands that
  collapse or are discontinued, near-expiry returns from chemists, and a seeded
  Class I recall of batch AZ4021 across 38 chemists. Three simulated years
  expire about 0.7% of purchase value.
- **Demand analysis and forecasting**: demand patterns (smooth, erratic,
  intermittent, lumpy), four forecasting methods, and rolling-origin backtests.
  Methods are chosen per pattern by backtest results, not reputation
  ([ADR 0007](docs/adr/0007-forecast-routing-by-backtest.md), re-checked in
  [ADR 0009](docs/adr/0009-forecast-routing-rechecked.md)).
- **Stock health in rupees at cost**: stock ageing, dead stock, ABC and XYZ
  classification, and the value of stock likely to expire before it sells.
- **A Marg ERP bridge** that writes and reads Marg-shaped tables, rebuilds the
  ledger from bill lines, stops a sync when the tables drift from the expected
  layout, and reconciles Marg's stock figures against its own bills. Marg
  publishes no column reference, so the layout is a documented assumption.
- **A recall engine and drill**: a recall notice is matched against every batch
  ever held. Exact matches on manufacturer, product, batch number and expiry are
  blocked at once ([ADR 0004](docs/adr/0004-automatic-recall-block.md)); look-alike
  batch numbers such as AZ4O21 are raised for review, never blocked. Blocks and
  releases are an append-only log ([ADR 0008](docs/adr/0008-batch-holds-log.md)).
  A report reconciles what each chemist was supplied against what they returned,
  with the CDSCO deadlines, and a draft notice to each chemist still holding the
  batch is ready for the pharmacist to sign. A monthly CDSCO drug alert list can be checked
  whole, row by row. The drill checks all of it against a seeded Class I
  recall and three look-alike batches.
- **A price guard**: ceiling prices are dated records, each in force from its
  own date. A batch whose printed MRP is above the ceiling plus GST in force is
  blocked before billing, with the notification named; a scheduled item with
  no ceiling on record is warned, not allowed. Past sales above the ceiling, or
  of a batch whose MRP rose more than 10% in a year, are totalled as exposure
  with 15% simple interest ([ADR 0011](docs/adr/0011-price-guard-dated-ceilings.md)).
  This is Batchward's reading of the rules, not legal advice.
- **A registrar** that checks the records a Drugs Inspector examines under
  Rule 65: every sale memo carries the buyer's name, address and sale licence
  number with the drug, quantity, batch and manufacturer; every purchase names
  a licensed supplier with an address; bills are numbered in date order; and
  memos from the last three years are kept. It cannot see the competent
  person's signature, and says so ([ADR 0012](docs/adr/0012-registrar-rule-65-wholesale.md)).
- **Invoice intake checks**: a supplier invoice extracted as the text printed on
  it is read and checked in plain Python before anything is posted: arithmetic,
  totals, the GSTIN check character, expiries against batches on record, batch
  numbers easily misread as ones already held, recall blocks and ceiling prices.
  Each finding says whether to extract again, ask a person, or just note it
  ([ADR 0015](docs/adr/0015-invoice-intake-checked-in-plain-python.md)). A
  clerk agent reads an invoice photo, PDF or text into that form and is asked
  to read flagged fields again, never told the value that would add up. A
  ready invoice is then matched against its purchase order and the godown's
  count: short supply goes on a draft debit note, and only when a named person
  approves is a purchase voucher written for Marg to import, with the approval
  recorded so a bill is never posted twice
  ([ADR 0016](docs/adr/0016-receipts-matched-three-ways-and-posted-on-approval.md)).
  An order delivered on several bills is matched against what is still due on
  it, so it cannot be billed twice over
  ([ADR 0017](docs/adr/0017-orders-matched-across-bills.md)).
  The simulator prints its deliveries as invoices, with their orders and counts.
- **Approvals**: a delivery ready to post waits as a numbered request in a
  graph workflow paused on ADK's `RequestInput`, kept in SQLite so a person can
  answer hours later from another process. On approval the delivery is matched
  again and posted only if it is still exactly what was approved; a rejection
  is recorded with its reason, and nothing is written
  ([ADR 0005](docs/adr/0005-approvals-via-request-input.md)). Approvers can
  answer on WhatsApp with Approve and Reject buttons; only listed phone numbers
  count, and every webhook delivery must carry Meta's signature
  ([ADR 0019](docs/adr/0019-approvals-on-whatsapp.md)).
- **Purchase orders**: what to order from each company, from the forecast, the
  stock that will actually sell before it expires, holds, and what is still due
  on orders already placed. An order is drafted per company, placed only on
  approval, and recorded, so deliveries are matched against it by the number
  they quote ([ADR 0020](docs/adr/0020-purchase-orders-drafted-from-the-forecast.md)).
- **Expiry claims**: each company's return terms are dated records. Stock the
  forecast says will not sell before expiry is valued at what its company would
  credit, window by window: open now, closing within 15 days, or already lost to
  a write-off inside an open window. A claim on one company is drafted with its
  sheet, a letter asking for a section 34 credit note and a Marg return voucher,
  made only on approval, and tracked until the company's credit notes settle it
  ([ADR 0018](docs/adr/0018-expiry-claims-from-dated-return-terms.md)).
- **Batchward's own records**: recall notices, blocks and their release are kept
  in a SQLite database of their own, which refuses any edit or deletion
  ([ADR 0010](docs/adr/0010-own-records-database.md)). Receiving a notice and
  blocking its batch happen in one transaction. Marg still does the billing and
  does not yet see these blocks.
- **A first agent team on Google ADK**: a desk that routes questions to an
  analyst, a forecaster and a reporter. Their tools are read-only, and a
  Numbers Guard plugin holds back any answer quoting a figure or batch number
  that no tool returned ([ADR 0003](docs/adr/0003-model-never-computes.md)).
  The analyst can report where a recorded recall stands and which batches the
  price guard blocks, but no agent can place or lift a block. The agents have
  been tested offline; runs against Gemini need an API key.

## Try it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync
uv run batchward demo-marg sim-out/marg.sqlite --records sim-out/records.sqlite
```

This simulates a year of trading, from September 2025 — about 250,000 stock
movements — and writes it as a Marg-style SQLite database with a recall and
price problems seeded in. The records database holds the manufacturer's recall
notice, received on 12 February 2026, the block it placed on batch AZ4021, and
the simulated ceiling prices, including one lowered on 1 April 2026 below three
brands' printed MRPs, and each simulated company's return terms for expiring stock.

```bash
uv run batchward health        # stock value, ageing, dead stock and expiry risk
uv run batchward backtest      # how each forecasting method scores, by demand pattern
uv run batchward recall-drill  # block recalled batch AZ4021, reconcile returns, print the report
uv run batchward price-guard   # a lowered ceiling, a 10% price rise, and the exposure they create
```

```bash
uv run batchward recall status --marg sim-out/marg.sqlite --records sim-out/records.sqlite --reference RN/2026/014
uv run batchward recall list --records sim-out/records.sqlite
```

`batchward recall receive` records a new notice and blocks what it names
exactly; `batchward recall release` lifts a block, which stays on record.
`batchward recall notices` drafts a notice for each chemist still holding the
batch, naming the bills it went out on, for the pharmacist to sign; nothing is
sent. `batchward recall import-alerts` checks a whole CDSCO drug alert list,
saved as CSV, against every batch ever held, blocking only exact matches
([ADR 0014](docs/adr/0014-cdsco-alert-lists-as-notices.md)).
`batchward ceilings add` records a notified ceiling price from its date, and
`batchward ceilings list` shows them. `batchward ceilings import` records a
whole NPPA notification from its table saved as CSV, converting each price per
tablet or per ml into a price per pack stocked
([ADR 0013](docs/adr/0013-nppa-notifications-per-unit-sold.md)).

```bash
uv run batchward registrar --marg sim-out/marg.sqlite   # Rule 65 records, as an inspector would check them
```

`batchward demo-marg ... --invoices sim-out/invoices` also writes the last 30
days' supplier invoices, each as printed text, as its correct reading in JSON and
as the godown counted it, with the purchase orders they fill.
`batchward intake check <invoice.json> --marg sim-out/marg.sqlite --records
sim-out/records.sqlite` checks a reading before posting, and `batchward intake
extract <invoice.txt|.pdf|.jpg> ...` has the clerk read the document first, which
needs a Gemini API key. `batchward intake receive <invoice.json> --count <count.csv> --order
<order.json> ...` matches the delivery and shows what is still due on the order.
With `--out <folder>` a delivery ready to post is put up for approval as a
numbered request, and its run pauses. `batchward approvals list --records
sim-out/records.sqlite` shows what waits, `approvals show A-0001 ...` what
approving it would write, and `approvals approve A-0001 ... --by <name>` or
`approvals reject A-0001 ... --by <name> --reason <why>` resumes the run: only an
approval writes the Marg purchase voucher and any debit note. `--approve-by
<name>` approves at once.

`batchward orders suggest --marg sim-out/marg.sqlite --records sim-out/records.sqlite`
shows what to order from each company; `orders draft <company> ... --out <folder>`
puts an order up for approval, and `orders list` shows what each order placed
still waits for.

`batchward claims windows --marg sim-out/marg.sqlite --records
sim-out/records.sqlite` shows what can be claimed from companies now, what closes
soon and what was lost; the demo records every simulated company's return terms,
and `claims terms import <terms.csv>` records real ones. `claims draft <company>
... --out <folder>` puts a claim up for approval, `claims list` shows what each
claim still waits for, and `claims settle <claim> --credit-note ... --amount ...`
records a company's credit note.

To answer on WhatsApp, fill in the `WHATSAPP_*` settings and `BATCHWARD_APPROVERS`
in `.env` (see `.env.example`), list approvers in a CSV of `phone,name`, and run
`uv run --env-file .env batchward whatsapp serve`, reachable by Meta over HTTPS
(for example through a tunnel). `batchward whatsapp notify A-0001` sends a waiting
request to every approver, as `--notify` does when putting something up for
approval; an approver taps Approve, or replies `reject A-0001` and the reason.
Without an approval template, only approvers who wrote to the business number
in the last 24 hours can be sent a request, which is what WhatsApp delivers.

### Talk to the agents

Copy `.env.example` to `.env`, add a Gemini API key, and point
`BATCHWARD_MARG_DB` and `BATCHWARD_RECORDS` at the databases created above.
Then, from the project root:

```bash
uv run adk web agents
```

Open the page it prints and ask, for example, *"Where does the AZ4021 recall
stand?"*, *"Which batches can't I bill because of ceiling prices?"*, *"Are my records
ready for an inspection?"* or *"Give me the morning brief."*

## Project layout

```
src/batchward/
  core/       batch ledger, holds, FEFO allocation, batch tracing, time
  sim/        simulated catalogue, day-by-day trading, scenarios, recall drill, ceilings
  bridge/     Marg ERP layout, export, import, layout checks, reconciliation
  analysis/   demand, forecasting, backtests, costs, ageing, expiry, stock health
  compliance/ recall, price guard, and the Rule 65 registrar
  records/    Batchward's own database: recall notices, holds, releases
  reporting/  presentation: rupees in Indian digit grouping, the recall report
  agents/     the ADK agent team, its read-only tools, and the data they read
  cli.py      command-line entry point; *_cli.py modules hold the subcommands
agents/desk/  entry point that `adk web` and `adk run` load
docs/adr/     architecture decision records
tests/        mirrors src/, plus property-based ledger tests
```

## Development

```bash
uv sync               # create the environment
uv run pytest         # run the fast tests
uv run pytest -m slow # three years through Marg, and the recall drill on three years (minutes)
uv run --env-file .env pytest -m live   # against the real Gemini API; costs money
uv run ruff check     # lint
uv run ruff format    # format
```

## Licence

[Apache 2.0](LICENSE)
