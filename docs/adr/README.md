# Architecture decision records

Each record captures one decision, the context that forced it, and what it
costs. Records are never edited after acceptance; a changed decision gets a new
record that supersedes the old one.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-append-only-batch-ledger.md) | Stock is an append-only ledger of batch movements | Accepted |
| [0002](0002-batch-identity.md) | A batch is identified by company, item, batch number and expiry | Accepted |
| [0003](0003-model-never-computes.md) | The language model never produces numbers; tools do | Accepted |
| [0004](0004-automatic-recall-block.md) | Blocking a recalled batch is the only automatic action | Accepted |
| [0005](0005-approvals-via-request-input.md) | Approvals use graph `RequestInput` and an approval table | Accepted |
| [0006](0006-read-marg-read-only.md) | Marg ERP is read over ODBC and written only through its import path | Accepted |
| [0007](0007-forecast-routing-by-backtest.md) | Forecasting methods are routed by demand pattern, as backtests show | Accepted |
