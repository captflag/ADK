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

Pre-alpha, in **Phase 0: foundations**. No agents yet, and nothing here is ready
for real business data. What exists so far:

- **A batch ledger** where stock is derived from an append-only history of
  movements, never stored directly. It refuses anything that would take a batch
  below zero at any point in time, corrects mistakes with reversing entries,
  and is checked against a reference model with property-based tests.
- **First-expiry-first-out allocation** that never sells expired, blocked or
  recalled stock.
- **Batch tracing**: every chemist who received a batch, what is still on hand,
  and every unit accounted for.
- **A simulated stockist** with seasonal demand, weekly purchase orders, expiry
  write-offs, and a seeded Class I recall of batch AZ4021 across 38 chemists.
- **A Marg ERP bridge** that writes and reads Marg-shaped tables, rebuilds the
  ledger from bill lines, stops a sync when the tables drift from the expected
  layout, and reconciles Marg's stock figures against its own bills. Marg
  publishes no column reference, so the layout is a documented assumption.

## Try it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync
uv run batchward demo-marg sim-out/marg.sqlite
```

This simulates six months of trading — about 160,000 stock movements — and
writes it as a Marg-style SQLite database, with the recall seeded in.

## Project layout

```
src/batchward/
  core/     batch ledger, FEFO allocation, batch tracing
  sim/      simulated catalogue, day-by-day trading, scripted scenarios
  bridge/   Marg ERP layout, export, import, layout checks, reconciliation
  cli.py    command-line entry point
docs/adr/   architecture decision records
tests/      mirrors src/, plus property-based ledger tests
```

## Development

```bash
uv sync               # create the environment
uv run pytest         # run the tests
uv run ruff check     # lint
uv run ruff format    # format
```

## Licence

[Apache 2.0](LICENSE)
