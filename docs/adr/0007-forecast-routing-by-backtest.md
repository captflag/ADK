# ADR 0007: Forecasting methods are routed by demand pattern, as backtests show

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Pharma stock sells unevenly: some items steadily, many in ones and twos with
gaps. The forecasting literature recommends Croston's method, or TSB, for
intermittent demand. Batchward needs one forecast per item that reorder and
expiry decisions can rely on, and the choice should rest on evidence from this
kind of demand rather than on reputation.

Weekly sales from the simulated stockist were classified by demand pattern and
every method was backtested one week ahead (`batchward backtest`). Two runs
were compared: two years of history, and forty weeks.

| Pattern | Two years: best | Forty weeks: best | Croston / TSB |
|---|---|---|---|
| Smooth | TSB 0.768, exp. smoothing 0.771, moving average 0.775 | exp. smoothing 0.751 | tied, but Croston under-forecasts by 0.9–1.8 units a week |
| Intermittent | moving average 0.788, Croston 0.788 | moving average 0.788 | tied in one run, worse than naive in the other |
| Erratic | moving average 0.720 | moving average 0.530 | worse, over-forecasting by 2–6 units a week |
| Lumpy | moving average 0.619 | moving average 0.506 | worse, over-forecasting |

(Median MASE; lower is better. Every routed method beat the naive
same-as-last-week benchmark in both runs.)

## Decision

- Smooth demand: exponential smoothing.
- Erratic, intermittent and lumpy demand: an 8-week moving average.
- No demand in the history: forecast zero.

Croston's method and TSB remain available but are not routed. A route changes
only when a backtest supports the change.

## Consequences

- The default forecasts are simple enough for a stockist to check by hand.
- Over-forecasting uneven demand — the error that turns into expired stock —
  is avoided.
- The evidence comes from simulated sales, which are less intermittent than a
  real 12,000-SKU catalogue: most simulated items are smooth at weekly level.
  TSB's ability to lower its forecast when a brand stops selling may matter
  more on real data, so this decision must be re-run on real history before it
  is trusted there.
- Rankings among close methods moved between runs, so differences of a few
  hundredths of MASE are treated as ties.
