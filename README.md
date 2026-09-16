# Batchward

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

Pre-alpha. The project is in **Phase 0: foundations** — the batch ledger and a
simulated distributor to build everything else against. Nothing here is ready
for real business data.

## Licence

[Apache 2.0](LICENSE)
