# ADR 0009: Forecast routing is kept after re-checking it on the richer simulator

- **Status:** Accepted
- **Date:** 2026-09-16
- **Supplements:** [ADR 0007](0007-forecast-routing-by-backtest.md)

## Context

ADR 0007 routed forecasts by demand pattern using backtests on the simulated
stockist. Since then the simulator gained chemists' near-expiry returns and
brands that stop selling altogether, so the sales history the backtests read
has changed. ADR 0007 said a route changes only when a backtest supports the
change, so both of its runs were repeated (`batchward backtest` for two years,
`batchward backtest --days 280` for forty weeks).

Median MASE, lower is better; the routed method is marked with an asterisk.

| Pattern | Two years (items) | Forty weeks (items) |
|---|---|---|
| Smooth | exp. smoothing* 0.767, TSB 0.762, naive 0.998 (424) | exp. smoothing* 0.750, naive 0.946 (425) |
| Erratic | moving average* 0.725, exp. smoothing 0.730, naive 0.791 (28) | moving average* 0.405, naive 0.526 (24) |
| Intermittent | moving average* 0.756, naive 0.768, Croston 1.848 (52) | naive 0.000, moving average* 0.407, Croston 4.054 (60) |
| Lumpy | exp. smoothing 0.706, naive 0.722, moving average* 0.725 (7) | moving average* 0.506, naive 0.674 (2) |

Two results break ADR 0007's observation that every routed method beat the
naive benchmark:

- **Lumpy, two years:** the moving average scores 0.725 against naive's 0.722,
  across seven items. Forty weeks earlier it won, across two items. Neither
  sample is large enough to choose between methods.
- **Intermittent, forty weeks:** naive scores 0.000. That median means most of
  these items sold nothing at all in the weeks tested, so a forecast of "same as
  last week" — zero — was exactly right. In the two-year run, 29 of the 52
  intermittent items were brands the simulator had discontinued.

Croston's poor scores have the same cause. It updates only when a sale happens,
so after a brand stops selling it keeps forecasting the old level. On the 29
discontinued brands in the two-year run its median MASE was 6.40, against 0.93
for TSB; on the 15 intermittent items that kept selling it was 0.72.

## Decision

Keep ADR 0007's routing: exponential smoothing for smooth demand, an 8-week
moving average for erratic, intermittent and lumpy demand. Neither exception
is evidence for a different route: one is a near-tie on seven items, the other
measures brands that stopped selling rather than demand that is intermittent.

## Consequences

- The intermittent scores currently measure obsolescence more than
  intermittency. An item that has stopped selling is dead stock, not an item to
  forecast; it should be recognised before forecasting and left out of
  backtests by pattern. Until that is built, intermittent results should not be
  used to change a route.
- Croston's method stays unrouted. Its failure on brands that stop selling is a
  known property, and the kind of error a stockist pays for in expired stock.
- Claims that routed methods beat the naive benchmark on every pattern are no
  longer made; smooth and erratic demand still clearly do.
