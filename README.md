# Autotrader

Autonomous **paper-trading research system** powered by the [Kronos](https://github.com/shiyu-coder/Kronos) financial foundation model.

Autotrader runs unattended on GitHub Actions, downloads a pinned Kronos release, forecasts a liquid US-market universe, builds a constrained portfolio, simulates rebalancing, and records its own performance over time.

> **Status:** paper trading only. No broker credentials and no real-money order execution are included.

## How it works

```text
Market data
    ↓
Kronos-base forecasts
    ↓
Asset ranking
    ↓
Portfolio construction
    ↓
Risk constraints
    ↓
Paper rebalancing
    ↓
State + performance history
```

The project is designed to run without day-to-day intervention. Its scheduled workflow updates the simulated portfolio after US market sessions and commits the resulting state back to this repository.

## Defaults

| Setting | Value |
|---|---:|
| Forecast model | `NeoQuasar/Kronos-base` |
| Starting paper balance | 10,000 |
| Forecast horizon | 5 trading days |
| Maximum positions | 5 |
| Maximum weight per position | 18% |
| Minimum cash buffer | 10% |
| Minimum forecast to enter | +1% |
| Simulated slippage | 10 bps |
| Market universe | 20 liquid US-listed assets |

The strategy settings live in `autotrader/config.py`.

## Automation

### Paper portfolio

`.github/workflows/paper-trade.yml`

Runs Monday through Friday after the regular US market session. It:

1. checks out this repository;
2. fetches the exact Kronos commit stored in `UPSTREAM_REF`;
3. restores cached model weights when available;
4. runs unit tests;
5. downloads current OHLCV market data;
6. generates Kronos forecasts;
7. constructs and rebalances the simulated portfolio;
8. updates `data/state.json` and `data/latest.json`;
9. commits the new simulation state.

Because scheduled runs create repository activity, the project does not depend on a permanently running server.

### Safe Kronos updates

`.github/workflows/upstream-sync.yml`

Once per week Autotrader checks the upstream Kronos repository. A newer commit is **not adopted blindly**. The candidate must first pass:

- the upstream Kronos regression tests;
- Autotrader's own portfolio/risk tests;
- an import compatibility check.

Only after those checks pass is `UPSTREAM_REF` advanced. Upstream changes never overwrite the Autotrader portfolio logic.

## Project structure

```text
autotrader/
  config.py       strategy and risk configuration
  data.py         market-data ingestion
  forecast.py     Kronos forecasting adapter
  portfolio.py    asset selection and target weights
  paper.py        simulated execution and accounting
  run.py          orchestration

data/
  state.json      persistent simulated account state
  latest.json     latest run report, created by the workflow

.github/workflows/
  paper-trade.yml
  upstream-sync.yml
```

## Local validation

```bash
python -m pip install -r requirements.txt
pytest -q
```

A full Kronos run additionally downloads the pinned upstream source and Hugging Face model weights.

## Attribution

Autotrader is an independent project. Its forecasting engine is based on **Kronos**, created by ShiYu and contributors and distributed under the MIT License.

- Upstream project: https://github.com/shiyu-coder/Kronos
- Kronos model weights: https://huggingface.co/NeoQuasar
- Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

The Autotrader orchestration, portfolio simulation, risk rules and GitHub automation are maintained separately from Kronos.

## Disclaimer

This repository is a research and software-engineering project, not investment advice. Forecasts can be wrong, market regimes can change, and simulated results do not guarantee real-world performance.
