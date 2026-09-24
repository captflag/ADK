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
| [0008](0008-batch-holds-log.md) | Holds on batches are an append-only log, separate from stock | Accepted |
| [0009](0009-forecast-routing-rechecked.md) | Forecast routing is kept after re-checking it on the richer simulator | Accepted |
| [0010](0010-own-records-database.md) | Batchward keeps its own records in its own database | Accepted |
| [0011](0011-price-guard-dated-ceilings.md) | Ceiling prices are dated data, and billing above them is blocked | Accepted |
| [0012](0012-registrar-rule-65-wholesale.md) | The Registrar checks Rule 65 wholesale records, not retail registers | Accepted |
| [0013](0013-nppa-notifications-per-unit-sold.md) | NPPA notifications are imported from their tables and priced per pack stocked | Accepted |
| [0014](0014-cdsco-alert-lists-as-notices.md) | CDSCO alert lists are imported row by row as recall notices | Accepted |
| [0015](0015-invoice-intake-checked-in-plain-python.md) | Invoices are extracted as printed text and checked in plain Python before posting | Accepted |
| [0016](0016-receipts-matched-three-ways-and-posted-on-approval.md) | A delivery is matched against its order and count, and posted only on approval | Accepted |
| [0017](0017-orders-matched-across-bills.md) | An order delivered on several bills is matched against what is still due | Accepted |
| [0018](0018-expiry-claims-from-dated-return-terms.md) | Expiry claims are drafted from dated return terms and made only on approval | Accepted |
| [0019](0019-approvals-on-whatsapp.md) | Approvals are answered on WhatsApp by approvers known by phone number | Accepted |
| [0020](0020-purchase-orders-drafted-from-the-forecast.md) | Purchase orders are drafted from the forecast and placed only on approval | Accepted |
| [0021](0021-morning-brief-worked-out-in-python.md) | The morning brief is worked out in Python and sent on WhatsApp | Accepted |
| [0022](0022-cover-by-abc-xyz-class.md) | Each item's cover follows its ABC-XYZ class, as a replay of demand chose | Accepted |
| [0023](0023-orders-rounded-up-to-whole-cases.md) | Orders are rounded up to whole cases, from case sizes kept in the records | Accepted |
| [0024](0024-damage-on-arrival-debited-back.md) | Units that arrive damaged are counted apart, never posted, and debited back | Accepted |
| [0025](0025-breakage-claimed-from-a-recorded-reason.md) | Breakage is claimed from a reason recorded against the credit note | Accepted |
