# Autotrader

[![CI](https://github.com/PkLavc/Autotrader/actions/workflows/ci.yml/badge.svg)](https://github.com/PkLavc/Autotrader/actions/workflows/ci.yml)
[![Paper Portfolio](https://github.com/PkLavc/Autotrader/actions/workflows/paper-trade.yml/badge.svg)](https://github.com/PkLavc/Autotrader/actions/workflows/paper-trade.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Autonomous daily-market research and guarded broker-execution platform powered by the
[Kronos](https://github.com/shiyu-coder/Kronos) financial foundation model.

**Dashboard:** https://pklavc.com/Autotrader/

> The research portfolio is paper trading. Broker execution is disabled until an owner
> explicitly configures credentials and safety gates. Profit is not guaranteed and the
> project is not investment advice.

## Architecture

```text
Adjusted daily market data + corporate actions
                    |
                    v
       deterministic Kronos forecast
                    |
                    v
       risk-adjusted rank + constraints
                    |
             close-of-day signal
                    |
                    v
       next-session-open paper execution
                    |
       accounting + costs + state audit

Optional broker lane (separate book):
signal report -> guarded plan -> Alpaca paper -> reconciliation audit
                                      |
                         live remains hard-locked
```

The research ledger and broker account are intentionally separate. Broker failures,
partial fills, or delayed jobs cannot rewrite the research history.

## Correctness and safety properties

- A signal generated from a session close is not filled retroactively at that close.
- Each market session is processed at most once, including manual retries and holidays.
- Forecast sampling is seeded by symbol and market session for repeatability.
- Regular NYSE holidays are excluded from forecast timestamps; downloaded sessions remain authoritative.
- Splits adjust held quantities and dividends credit paper cash exactly once.
- Adjusted OHLC data prevents historical split discontinuities from reaching Kronos.
- Paper fills include slippage and a conservative per-order commission model.
- Implausible forecasts above the configured absolute-return ceiling are rejected.
- Volatility-adjusted scores rank eligible signals while position and cash caps remain enforced.
- Broker plans are long-only, cash-limited, notional-limited, and idempotent.
- Live mode requires multiple independent opt-ins and never activates from credentials alone.

## Strategy defaults

| Setting | Default |
|---|---:|
| Model | `NeoQuasar/Kronos-base` |
| Starting research cash | US$10,000 |
| Lookback | 400 market sessions |
| Forecast horizon | 5 market sessions |
| Maximum positions | 5 |
| Maximum position | 18% |
| Minimum cash | 10% |
| Entry forecast floor | +1% |
| Forecast plausibility ceiling | +30% |
| Rebalance threshold | 3% of NAV |
| Slippage | 10 bps |
| Paper commission | US$0.0035/share, minimum US$0.35/order |

Configuration lives in `autotrader/config.py`. These are research assumptions, not claims
that the values are optimal.

## Automated workflows

### Research portfolio

`.github/workflows/paper-trade.yml` runs after the US regular session, downloads the pinned
Kronos release, tests the project, produces a new signal, audits state invariants, and commits
the new paper ledger. The schedule uses `America/New_York` so daylight-saving changes do not
shift its market-relative time.

The first v2 run migrates the existing state without resetting cash, positions, or history.
Legacy executions remain identifiable because they do not have v2 execution metadata.

### Broker paper execution

`.github/workflows/broker-paper.yml` is inert until the repository variable
`AUTOTRADER_BROKER_ENABLED=true` exists. It runs after the regular market opens and defaults
to Alpaca paper mode. It can produce dry-run plans or submit guarded paper orders.

Read [Broker setup](docs/BROKER_SETUP.md) before enabling it. Do not enable live execution
until every gate in [Operations and promotion](docs/OPERATIONS.md) is satisfied.

### Safe upstream updates

`.github/workflows/upstream-sync.yml` validates upstream source/model candidates before
advancing `UPSTREAM_REF`, `MODEL_REF`, or `TOKENIZER_REF`. Upstream code executes in a
read-only job; the write-enabled job only persists validated revision identifiers.

## Local validation

Use Python 3.11, matching CI:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/audit_state.py data/state.json data/latest.json
```

A full forecast additionally downloads the pinned Kronos source and model weights.

Point-in-time forecast archives can be replayed without same-bar fills:

```bash
python scripts/walk_forward.py path/to/sessions.jsonl --output data/backtest-latest.json
```

Each JSONL row must contain a unique increasing `market_date`, per-symbol adjusted `open` and
`close` prices, and the forecasts that were available at that close. The final session's signal
is reported as pending rather than falsely counted as an executed trade.

## Repository map

```text
autotrader/
  broker.py        pure broker planning + Alpaca client
  broker_run.py    guarded broker command
  calendar.py      regular NYSE session calendar
  config.py        research and cost assumptions
  data.py          adjusted OHLCV and corporate actions
  forecast.py      deterministic Kronos adapter
  paper.py         v2 ledger, execution and metrics
  portfolio.py     signal filtering, ranking and targets
  run.py           daily research orchestration

data/
  state.json          persistent research ledger
  latest.json         latest research signal/report
  broker-latest.json  sanitized broker audit, once configured
```

## Scope and limitations

Kronos produces raw forecasts, not guaranteed alpha. The present fixed universe can carry
sector concentration and survivorship bias. GitHub Actions is suitable for daily research
and guarded paper automation, but it does not provide deterministic low-latency scheduling.
An intraday system would require a different dataset, validation regime, execution service,
and risk model.

## Attribution

Autotrader is maintained by Patrick Araujo (PkLavc). Kronos was created by ShiYu and
contributors and is distributed under the MIT License. See [third-party notices](THIRD_PARTY_NOTICES.md).
