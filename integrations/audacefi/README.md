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
months of verified Binance spot history. Full-curve drawdown stays continuous
inside each training or holdout period; daily and five-day competition drawdowns
are also measured separately for the official risk gate.

The simulator applies the 35% position and total exposure caps, a 1.8% guard
(stricter than the displayed 2% platform maximum), no more than ten swaps in
any rolling 24 hours, a reserved exit slot, 30 bps estimated round-trip costs
(20 bps assumed fees and 10 bps assumed slippage), and end-of-season USDC
liquidation. Cost figures are estimates, not quotes or observed fills.

Short, overlapping live evaluations are diagnostic only and cannot promote a
strategy. Recovery remains disabled unless its twelve-month holdout is
profitable net of costs, beats baseline by at least 30 bps, has at least six
completed trades, and stays within the live daily and five-day drawdown guard
in both training and holdout. Full-curve drawdown remains visible as a separate
research metric and is not mislabeled as the per-competition 2% rule. The live runner remains the only order executor; the history job
and Oracle research are read-only. Backtests do not guarantee live gains.

## Historical data, AutoTrader reuse and Oracle VM

The [historical training workflow](../../.github/workflows/audacefi-history-training.yml)
runs weekly and on demand. It downloads twelve **completed** months of public
15-minute Binance spot archives for the active AudaceFi-eligible symbols,
verifies official SHA-256 checksums, rejects missing candles and regenerates a
bounded candidate grid across momentum, recovery, breakout, mean-reversion and
multi-period trend families. Alternative signal thresholds are regenerated
from the training segment's median absolute 24-hour return; the live baseline
and portfolio risk limits stay fixed. It selects a candidate on the first 75%
chronologically, then opens the untouched final 25% once for promotion checks.
Each partition is simulated as one continuous equity curve through the same
decision engine as live execution. A missing
USDT spot archive may fall back to a separately verified USDC pair. These
stablecoin market prices are proxies; they are **not** AudaceFi Devnet quotes.

The job calls the stock AutoTrader's
[`sector_factors`](../../autotrader/learning.py) only with one independent
chronological training curve per asset; overlapping slices of the same prices
are not treated as separate evidence. The stock Kronos forecaster is already
adapted to daily crypto candles in
[`CryptoKronosEngine`](crypto_research.py) but remains optional research:
it is **not** a validated 15-minute live trading model. Nothing in this
workflow places orders. A 12-month candidate may be written to the strategy
gate independently for SOL or ETH only when that asset's candidate beats
momentum in training and untouched holdout, has enough completed trades, closes
in USDC, respects the rolling swap limit and
stays within the intraday and competition drawdown guards. The executor still
rolls back to momentum for 24 hours after three consecutive realized losing
round trips exceeding 20 USDC combined.

The live executor also keeps a bounded, encrypted-journal ledger of SOL/ETH
signals, including proposals and rejected entries. It records indicators and
filter decisions, then marks net counterfactual 1h/6h/24h returns after estimated
fees and slippage as later completed candles arrive. Only matured 24-hour labels
with non-overlapping daily observations enter the online learner. It splits
those labels chronologically, selects among historically OOS-approved policies
per asset, and tests only stricter parameter variants. Promotion requires a
positive net validation edge over momentum, at least eight validation samples,
and drawdown below 1.8%; realized losing round trips can roll that asset back.
The online ledger cannot bypass the historical qualification gate or increase
exposure.

The six-times-daily universe scan now checks all currently eligible symbols
against the frozen allowlist and AudaceFi Devnet mint mapping, then cross-checks
recent completed Coinbase, Binance, Kraken and OKX candles against the
authenticated AudaceFi mark price and a liquidity floor. A short scan reports current signal
candidates only; it does not treat a few candles as historical performance.
Quote-route availability remains unverified until an authorized Devnet buy and
sell are both confirmed. The live signer and executor remain restricted to
SOL/ETH, so no newly discovered token can be traded by this change.

Previous twelve-month run (November 9, before per-asset qualification and
training-derived threshold generation): 15 explicitly eligible symbols,
5 with complete verified history, and **zero** recovery candidates approved.
On the untouched holdout, SOL momentum returned -1.09% (150 swaps; estimated
costs 793.44 USDC) versus recovery -4.11% (42 swaps; 212.24 USDC). ETH momentum
returned -1.62% (144 swaps; 755.98 USDC) versus recovery -1.87% (16 swaps;
81.14 USDC). Recovery reduced activity and costs, but lost more net USDC on
both assets. Recovery also breached the 1.8% daily/five-day guard during
training on both SOL (2.02%/2.62%) and ETH (1.84%/2.36%). Full-curve holdout max drawdown was SOL 8.37%/6.10% and ETH 7.27%/1.88%
(momentum/recovery). All reported costs use estimated 10 bps fees and 5 bps slippage per swap; they are
not observed AudaceFi fills. The result is evidence to keep momentum and reject
recovery, not proof of live profitability.

The [Oracle worker deployment](../../deploy/oracle/README.md) runs the same
candidate evaluation daily and a Kronos five-day shadow forecast on WBTC, ETH
and SOL. It records realized forecast errors against five-day momentum without
signing or placing orders. Its frozen allowlist snapshot expires with Season 1
and carries no API or wallet secrets. GitHub Actions remains the only trading
executor. This checkout has no Oracle deployment credential or verified
research-result bridge, so the live gate is currently sourced from the
authenticated GitHub historical-training workflow; the Oracle's private report
does not yet publish decisions into that gate.

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
