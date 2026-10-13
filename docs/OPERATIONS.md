# Operations and promotion policy

This document defines engineering gates. Passing them does not guarantee profitability.

## Daily checks

- The research workflow completed successfully.
- `market_session` advanced by exactly one real exchange session.
- The state audit reports no duplicate session or invalid balance.
- Forecast errors did not remove most of the universe.
- The pending signal is dated and the next-open execution metadata is present.
- Paper broker positions, cash, open orders, and recorded audit agree.
- No order exceeded configured notional or exposure limits.

## Incident response

Disable `AUTOTRADER_ORDER_SUBMISSION_ENABLED` immediately when any of these occurs:

- broker/research position mismatch;
- duplicate or unexplained order;
- stale or missing market data;
- split or dividend discrepancy;
- unexpected authentication or permission change;
- unbounded loss, exposure, or order size;
- repeated workflow delay beyond the intended execution window.

Cancel open orders in the broker interface and reconcile statements before re-enabling. Never
repair an account by deleting state history or manually editing quantities to hide a mismatch.

## Promotion gates: research to broker paper

- Unit and integration tests pass.
- Walk-forward evaluation uses only information available at each decision time.
- Transaction costs and slippage are included and stress-tested.
- Results are compared with SPY and simple non-ML baselines.
- Performance is evaluated across rising, falling, volatile, and sideways regimes.
- Forecast calibration, turnover, drawdown, and exposure are understood.

## Promotion gates: broker paper to limited live

- A substantial sequence of distinct market sessions completed without manual state repair.
- Every submitted order can be reconciled to a signal and deterministic client order ID.
- Partial fills, rejections, holidays, delayed jobs, and API outages were tested.
- Net paper performance remains acceptable after realistic all-in costs.
- Maximum tolerable drawdown and loss limits are documented by the account owner.
- Funding, foreign-exchange costs, statements, and tax reporting were reviewed.
- A manual kill procedure was exercised successfully.

Begin live operation with the smallest capital the owner can afford to lose and conservative
notional limits. Increase limits only through a reviewed configuration change with preserved
history. Never use leverage to compensate for weak or statistically uncertain returns.

## Known research limitations

- The initial universe is a fixed list of current liquid US securities and can create
  survivorship bias in historical evaluation.
- Kronos is a forecast model, not a complete trading strategy.
- Risk-adjusted ranking requires out-of-sample calibration before its score can be interpreted
  as economic confidence.
- GitHub scheduled workflows may start late; live daily execution eventually belongs on a
  monitored scheduler with explicit service-level objectives.
- A robust backtest must use point-in-time universes, corporate actions, delistings, and
  broker-realistic fills.
