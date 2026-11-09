# AudaceFi Season 1 — Solana Devnet

This integration is isolated from the stock AutoTrader. It runs on GitHub
Actions with the registered AudaceFi AI Agent wallet and fictitious Devnet
capital.

## Verified live state

On 2026-11-09, the authenticated API reported an active `devnet_onchain`
season, a linked wallet and a frozen Crypto Bro allowlist. The direct API
completed and finalized a 25 USDC SOL purchase and a full sale to USDC:

- BUY: `5hZoXCPpf5Y8gMJiJtTAJzp6opLrNvBTCRkyBpbVmeSrgS8k1NuR4azj7fXS3yn4bzc3q7XhixuBw1qMdYfdBGt8`
- SELL: `2CB8TpAkRXdvMNnz1TqrwVyGsp6WiRWbsXKWTkV3WeLNWG9vZjMWwZpeS86SbsBhVo6LX67XjUTVpXdf5PMnyKgZ`

Both transactions finalized on Solana Devnet and were reported through the
AudaceFi `/result` route. The authenticated wallet then showed two swaps and
USDC-only holdings. At 2026-11-09 05:28 UTC, it showed 9,999.8987 USDC LPV
and rank 98/105. These are timestamped observations.

A later read-only `history` run matched each finalized hash to its exact
server-side `/submission` record. The wallet still reported two swaps and the
player record a last-swap timestamp. The account feed did not include those
hashes, and `GET /result` returned 404; an exact row in the panel's visual
trade history has not been independently observed.

The official kit's signed deployment approval is required for its ranked
gateway. The supported direct API accepted both trades without that package.
The direct client checks the live season, wallet, allowlist, bench program and
ProgramData identity. The pinned kit builds and verifies signed transactions.
Its balance verifier is instrumented for the 10 bps fee observed on the live
program; signed bytes, genesis, instructions, accounts, signatures and token
deltas remain strictly checked.

## Automatic operator

[The trading workflow](../../.github/workflows/audacefi-direct-pilot.yml)
runs at minutes 17 and 47 during the active Season 1 calendar. A scheduled
session checks every three minutes for 330 minutes, then is configured to
dispatch its successor if the authenticated season remains active. It also
queues a backup after 21 minutes so a failed session can hand off. One active
and one pending session are allowed. GitHub can delay or drop scheduled triggers;
continuous availability cannot be guaranteed.

An [independent watchdog](../../.github/workflows/audacefi-watchdog.yml) runs
at minutes 7, 23, 39 and 55 each hour. It checks the authenticated season,
active workflow and encrypted-journal commit time. After a 12-minute stale heartbeat it requests
cancelation and queues one replacement. It never reads the wallet recovery
phrase, signs, or submits a trade.

`direct-auto.mjs` reconciles AudaceFi wallet holdings, Solana balances and an
encrypted journal before each order. The separate
`audacefi-competitive-journal` branch stores encrypted plans, risk state and
operation events. Each operation uses a stable idempotency key. An ambiguous
submission blocks new orders until the existing intent is reconciled.

Competitive entries are enabled with `AUDACEFI_ALLOW_ENTRY=true`. Each cycle
still requires a valid signal, a reconciled wallet and journal, and the season's
risk and trading limits before submitting an order. The operator may hold when
those conditions do not permit a trade.

The present operator supports one SOL or ETH spot position at a time. It caps
an entry at 35% of equity or 3,500 fictitious USDC, reserves a sell under a
rolling 24-hour ten-trade limit, and counts the two pilot swaps on their first
day. It stops new entries 24 hours before the API season end and begins
liquidation 16 hours before it. A 1.8% portfolio guard and position stops
reduce risk, but price gaps or outages can still breach the platform's 2%
intraday drawdown limit. Platform rules and live API state are authoritative.

## Shared AudaceFi evaluation (November 9, 2026)

Historical simulation and the live signal path use the same completed-candle
signal calculations and `league_strategy.decide` rules for entries, stops,
reversals and required USDC exits. Simulated orders fill on the next 15-minute
close. The comparison uses an untouched chronological final quarter of twelve
months of verified Binance spot history; equity and drawdown remain continuous
inside each training or holdout curve rather than being compounded from
independent short folds.

The simulator applies the 35% position and total exposure caps, a 1.8% guard
(stricter than the displayed 2% platform maximum), no more than ten swaps in
any rolling 24 hours, a reserved exit slot, 30 bps estimated round-trip costs
(20 bps assumed fees and 10 bps assumed slippage), and end-of-season USDC
liquidation. Cost figures are estimates, not quotes or observed fills.

Short, overlapping live evaluations are diagnostic only and cannot promote a
strategy. Recovery remains disabled unless its twelve-month holdout is
profitable net of costs, beats baseline by at least 30 bps, has at least six
completed trades, and stays within the live drawdown guard in both training
and holdout. The live runner remains the only order executor; the history job
and Oracle research are read-only. Backtests do not guarantee live gains.

## Historical data, AutoTrader reuse and Oracle VM

The [historical training workflow](../../.github/workflows/audacefi-history-training.yml)
runs daily and on demand. It downloads twelve **completed** months of public
15-minute Binance spot archives for the active AudaceFi-eligible symbols,
verifies official SHA-256 checksums, rejects missing candles and tests fixed
strategies in chronological, non-overlapping train/holdout folds. A missing
USDT spot archive may fall back to a separately verified USDC pair. These
stablecoin market prices are proxies; they are **not** AudaceFi Devnet quotes.

The job reuses the stock AutoTrader's
[`sector_factors`](../../autotrader/learning.py) conservative Bayesian
feedback calibration on **matured training folds only**, treating each crypto
symbol as a separate feedback group. The stock Kronos forecaster is already
adapted to daily crypto candles in
[`CryptoKronosEngine`](crypto_research.py) but remains optional research:
it is **not** a validated 15-minute live trading model. Nothing in this
workflow places orders or automatically promotes a historical result to
the live strategy.

First four-month baseline run (November 9): 15 explicitly eligible symbols,
6 with uninterrupted available historical coverage, and **zero** new
candidates that passed the chosen out-of-sample thresholds. The result is a
reproducible evidence baseline, not proof of a profitable trading edge.

The [Oracle worker deployment](../../deploy/oracle/README.md) runs this
historical research and a daily Kronos five-day shadow forecast on WBTC, ETH
and SOL. It records realized forecast errors against five-day momentum without
promoting a model or sending orders. It uses a public frozen allowlist snapshot
that expires with Season 1 and carries no API or wallet secrets. The live
GitHub worker remains the only trading executor until a separately verified
cross-host handoff prevents concurrent orders.

## Safe operation

The workflow reads GitHub Actions secrets named `AUDACEFI_API_KEY`,
`AUDACEFI_WALLET_ADDRESS`, `AUDACEFI_WALLET_RECOVERY_PHRASE`, and
`AUDACEFI_JOURNAL_KEY`. Never put their values in code, logs, issues or chat.
Restrict repository writers because a workflow change can access the Devnet
signer. Never reset the journal to bypass a pending operation.

Manual modes include `preflight`, `history`, `reconcile`, and `auto`. `auto`
with `session_minutes=0` performs one check; `330` starts a long session. The
[read-only live probe](../../.github/workflows/audacefi-live-probe.yml)
reports safe account and wallet summaries without submitting orders.

The public [Rulebook](https://audacefi.com/rulebook) requires supported
sandbox execution and USDC-only start and end snapshots. The official
[integration kit](https://github.com/AudaceFi/audacefi-integration-kit) is
pinned to commit `9abd82702795929722554906b3ac14a7c861181a` in the
workflow. `runner.mjs` is a separate legacy route.

## Local checks

    python -m pytest -q tests/test_audacefi.py
    node --test integrations/audacefi/wallet-signer.test.mjs
    node --test integrations/audacefi/queue-next-session.test.mjs
    node --test integrations/audacefi/watchdog.test.mjs
    node --check integrations/audacefi/direct-auto.mjs
    node --check integrations/audacefi/queue-next-session.mjs

Tests and a green workflow do not prove a trade. Require a finalized Devnet
transaction and a matching authenticated AudaceFi wallet/history update.
