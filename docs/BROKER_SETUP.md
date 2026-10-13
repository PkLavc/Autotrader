# Broker setup

The broker lane currently supports Alpaca because its paper and live APIs share the same
order model. Account availability depends on country and approval. Confirm eligibility and
the current fee schedule directly with the broker.

## 1. Start with an Alpaca paper account

Create paper API credentials in Alpaca. Never place credentials in this repository, an issue,
a workflow file, or a chat message.

Add these GitHub Actions secrets:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`

Add these repository variables:

| Variable | Initial value | Purpose |
|---|---:|---|
| `AUTOTRADER_BROKER_ENABLED` | `true` | Allows the broker workflow job to start |
| `AUTOTRADER_BROKER_MODE` | `paper` | Selects the Alpaca paper endpoint |
| `AUTOTRADER_ORDER_SUBMISSION_ENABLED` | `false` | Keeps every run in dry-run mode |
| `AUTOTRADER_MAX_ORDER_NOTIONAL` | `100` | Maximum estimated dollars per order |
| `AUTOTRADER_MAX_TOTAL_NOTIONAL` | `500` | Maximum estimated dollars per workflow run |
| `AUTOTRADER_MAX_TARGET_WEIGHT` | `0.18` | Rejects larger position targets |
| `AUTOTRADER_MAX_GROSS_TARGET` | `0.90` | Rejects excessive total exposure |

Run **Broker paper execution** manually with `submit_orders=false`. Inspect the artifact and
`data/broker-latest.json`. Only then set `AUTOTRADER_ORDER_SUBMISSION_ENABLED=true`.

## 2. Broker behavior

The broker runner:

1. rejects stale signal reports;
2. verifies the account is active and not blocked;
3. validates all target weights and total exposure;
4. plans reductions before additions;
5. limits buys to currently reported cash and never assumes sell proceeds;
6. enforces per-order and per-run notional caps;
7. checks the broker clock before submitting market orders;
8. assigns a deterministic `client_order_id` to every order;
9. refuses a second order when that identifier already exists;
10. persists a sanitized audit without keys or account identifiers.

Orders are fractional day market orders. This is appropriate for initial paper verification,
not a claim that market orders are optimal. A later execution study should compare market,
marketable-limit, and participation algorithms using actual fill data.

## 3. Local dry run

Set credentials only in the local environment, never in `.env` committed to Git:

```powershell
$env:ALPACA_API_KEY = "your-paper-key"
$env:ALPACA_API_SECRET = "your-paper-secret"
$env:AUTOTRADER_BROKER_MODE = "paper"
python -m autotrader.broker_run --signal data/latest.json --output data/broker-latest.json
```

Omit `--execute` until the dry-run plan has been reviewed. The command intentionally refuses
to create a plan from a legacy report without `market_session`.

## 4. Live lock

Credentials alone cannot enable live mode. In addition to changing
`AUTOTRADER_BROKER_MODE=live`, live requires:

- repository variable `AUTOTRADER_LIVE_EXECUTION_ENABLED=true`;
- repository variable `AUTOTRADER_ALLOW_LIVE=true`;
- secret `AUTOTRADER_LIVE_CONFIRMATION` with the exact phrase documented in
  `autotrader/broker_run.py`;
- `AUTOTRADER_ORDER_SUBMISSION_ENABLED=true`.

Do not set these gates until the promotion checklist is complete. Use a cash, long-only
account without options, shorting, or leverage for the first production phase.
