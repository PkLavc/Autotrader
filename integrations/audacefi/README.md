# AudaceFi integration — Autotrader

Optional and isolated integration for the AudaceFi Solana devnet league. It does not alter the equity strategy, Alpaca broker, stock paper ledger, research schedule, or account fleet. No automatic orders are enabled. A small scheduled read-only season watcher is enabled.

## Crypto Bro league strategy (v1, isolated from stock Autotrader)

**Goal:** maximize **net percentage USDC return** during the official five-day
season. The sandbox ranks performance first and maximum drawdown only as a
tie-breaker. Being first is **not guaranteed**.

AudaceFi's published [Rulebook](https://www.audacefi.com/rulebook) requires
USDC-only holdings at both official snapshots (except the allowed SOL gas
buffer), category-eligible tokens, and swaps through its supported interface
or API. A standard season runs Monday 00:00 UTC to Saturday 00:00 UTC, but
**the authenticated season state is authoritative**. Never assume Season 1
uses those standard dates or that every BTC/ETH/SOL token is eligible.

The new three-stage, read-only pipeline is:

1. `crypto_research.py` computes completed daily crypto candles, 3/7-day
   momentum, 30-day volatility, indicative dollar volume and an independent
   5-session crypto Kronos forecast. It never uses an unfinished UTC candle.
2. `league_strategy.py` validates **verified** season dates, token allowlist,
   category limits, wallet/starting snapshot, and reconciled USDC equity. It
   proposes spot exposure (up to 35% per token, 70% total, 1.25% planned
   per-trade risk) only when momentum and conservative forecast agree after an
   80-basis-point round-trip cost allowance. It checks protective exits and
   15% season drawdown, rejects new entries inside the final 16 hours and
   requires a USDC exit in the final 8 hours. A sell proposal does **not**
   mean the tokens have been swapped.
3. `league_plan.py` passes an eligible BUY proposal to the existing audited
   crypto operation preparer, **only** if the verified season ID, league wallet,
   token mints and USDC mint match the owner's explicit configuration. It
   floors the proposed USDC input to the six-decimal limit. No automatic
   submission, signing or wallet interaction occurs.

Example read-only commands (after providing **independently verified** private
context and a current reconciled wallet snapshot):

    python -m integrations.audacefi.league_strategy \
      --context /private/league-context.json \
      --portfolio /private/league-portfolio.json \
      --signals integrations/audacefi/signals.local.json

    python -m integrations.audacefi.league_plan \
      --context /private/league-context.json \
      --portfolio /private/league-portfolio.json \
      --signals integrations/audacefi/signals.local.json \
      --settings /private/league-settings.json

Those context files are **normalized adapters**, not raw AudaceFi API
responses. The inputs require `verified=true`,
`restrictions_verified=true`, `network=solana-devnet`,
`category=crypto-bro`, `season_status`, real `start_utc/end_utc`,
`start_snapshot_confirmed`, `wallet_ready`, official
`eligible_symbols`, numeric `max_position_weight/max_total_exposure`,
and (for BUY preparation) `season_id`, `wallet_address`,
`approved_token_mints`, `approved_usdc_mint`. The portfolio supplies
`equity_usdc`, `cash_usdc`, `peak_equity_usdc` and reconciled
`positions` with `value_usdc`, `price_usdc`,
`entry_price_usdc`, `peak_price_usdc`. Never fill these by guessing;
the authenticated API/schema and actual official wallet must be validated.
No test fixture authorizes a trade.

**Deployment blockers:** The season watcher last returned
`WAITING_FOR_SEASON` on 2026-11-04. There is no confirmed active season
or qualified league wallet, and no verified durable trading journal or
complete SELL-to-USDC execution adapter. The current daily forecast cadence
cannot protect against intraday crypto moves; the paper strategy must undergo
point-in-time five-day league backtests, execution/slippage stress tests and
an official devnet end-to-end rehearsal before autonomous trading is enabled.
No password, seed phrase or real crypto is needed for these read-only tests.


### Offline five-day season replay

`league_backtest.py` performs a **point-in-time** replay over timestamped
market quotes and already-existing crypto forecasts. Each entry must contain
`timestamp`, `prices_usdc`, and a crypto `report` whose `generated_at`
does not exceed that timestamp. It applies separate fee and slippage estimates
on both sides of a spot trade, marks the start/end snapshots, and reports
net USDC-equivalent return, maximum drawdown and trade count.

    python -m integrations.audacefi.league_backtest \
      /private/season-replay.json --start-utc 2026-11-09T00:00:00Z \
      --end-utc 2026-11-14T00:00:00Z --eligible SOL BTC

The dates above are **illustrative fixtures**, not the confirmed schedule of
Season 1. A replay cannot validate the strategy unless it uses genuinely
as-of forecasts and the actual eligible universe/season dates. This tool
never queries the wallet, signs, submits, or claims an official league result.
Backtest results must be compared with USDC-only, buy-and-hold and simple
momentum baselines before using them for live decisions.

## Status

- Equity reports from data/latest.json intentionally result in HOLD, not a crypto trade. Stock tickers must never be reinterpreted as tokens.
- The crypto-only proposal builder is ready for a separate trusted crypto-signal feed, explicit season/wallet/token allowlist and a fixed USDC input budget. It makes no network calls and does not sign or trade.
- The connected JavaScript runner uses the upstream audited integration-kit functions, including the durable journal, quote checks, Solana reference adapter and wallet callback. Real devnet operation requires the owner's onboarding, an individual API key, reviewed deployment profile, qualified RPC, and an external signing adapter. None are included here.
- All execution features default to disabled and require a deliberate command. No mainnet, brokerage, fleet or existing-account credentials are passed to AudaceFi.

Official source: https://github.com/AudaceFi/audacefi-integration-kit (review and pin a tested revision of the stable branch before any connected execution). Read its CONNECTING.md, REFERENCE.md and RECOVERY.md; local demos do not establish deployment or wallet approval.

## Enrolled account and safe API check (2026-11-03)

- AudaceFi participant: **AI Agent**, **Crypto Bro**, conservative risk preset, Founding Player #15, Season 1 **registered**.
- The Phantom wallet is linked to the registration. The platform says the season-specific league wallet and snapshot are created at season start; **there is no active season or confirmed launch date yet**.
- The onboarding API key was reportedly saved by the operator in GitHub Actions as `AUDACEFI_API_KEY`. The repository cannot read its value from GitHub.
- Run [AudaceFi API connection (read-only)](../../.github/workflows/audacefi-connection.yml) in **Actions > Run workflow**. The workflow also runs once when its own definition is updated on `main`. It performs **only** a single authenticated GET to `https://sandbox.audacefi.app/api/v1/me/state?category=crypto-bro`, exactly as published in this account's Control Panel. It prints only an HTTP status and category, never the key, wallet address or response payload. It does not place trades, create a wallet, or trigger any recurring schedule.
- The current public integration kit uses `/v1/me/state` in its SDK, while the account's panel shows `/api/v1/me/state`. Those paths have **not** been proven interchangeable. Do not consider a successful direct read a qualification of the SDK's full order pipeline.
- No private signing key should be exported to a repository, Actions secret, model prompt or log. The registered Phantom wallet needs an independently approved external signing approach for unattended execution. Do not claim this step is complete or turn on automatic orders until the actual devnet bench target, API execution protocol, current season wallet and persistent journal are independently verified.

## GitHub Actions only — Phantom signer (Season 1)

**Run location:** GitHub Actions only. No personal PC, local background process or extra wallet is required.

The existing registered Phantom account can be restored from its 12-word recovery phrase and derived in the ephemeral Action runner. The signer **must** match the exact public address already bound to AudaceFi, or it fails closed. The reference integration kit validates the exact devnet transaction before invoking the signing callback. Both automatic order submission and scheduled trading remain OFF until the official season wallet, network profile, persistent operation journal, and eligible token list exist.

One-time operator setup:
1. Open repository **Settings > Secrets and variables > Actions**.
2. Under **Secrets**, add `AUDACEFI_WALLET_RECOVERY_PHRASE` with the 12 words separated by single spaces, in the correct order. **Never commit or paste the phrase into chat, issues or logs.** This gives the Actions runner temporary signing access; repository writers can change workflows to extract it, so restrict write access.
3. Under **Secrets**, add `AUDACEFI_WALLET_ADDRESS` with the **full Solana public address of the same registered Phantom account**. This is public information but storing it in Secrets is supported. Both workflow inputs now read from Secrets.
4. Run [AudaceFi Phantom wallet validation](../../.github/workflows/audacefi-wallet-validation.yml) manually once via **Actions > Run workflow**. It runs synthetic offline signing tests, then verifies that the recovery phrase derives the exact linked address. It will print only `PHANTOM_WALLET_ADDRESS_MATCHED` on success; it **never submits** a trade.

`runner.mjs` now defaults to `integrations/audacefi/wallet-signer.mjs` whenever an approved operation is later executed. The signer uses Node's standard crypto library (no wallet browser, no signing service, no paid dependencies), refuses a changed payer/mismatched address, requires an unsigned two-signer legacy message, and requires execution flags. The encrypted secret is not written to disk. This is **not yet an autonomous live execution workflow**: the AudaceFi season is not active, the league wallet has not been instantiated, and the official SDK reference profile and durable journal remain unqualified. Never use Actions cache or ephemeral runner directories as the only operation journal.

## GitHub Actions season monitoring

- The [Season 1 watcher](../../.github/workflows/audacefi-season-watch.yml) runs a read-only API check every six hours; it uses the API key but **never** the wallet recovery phrase.
- When the season is closed, it reports WAITING_FOR_SEASON. When the league wallet and season are active, it marks DEPLOYMENT_PENDING and opens one GitHub issue identifying the remaining deployment requirements.
- [Official SDK preflight](../../.github/workflows/audacefi-kit-preflight.yml) uses pinned source revision d2dc6c8d5c9359c1e143f8d41172dd279da934b5 and no secrets.
- Trading remains disabled because the official season-specific wallet, qualified chain/API profile, token allowlist, durable journal (must persist across GitHub Actions runners), and budget policy have not been verified. Never treat a green CI result as permission to submit orders.
## 1. Prepare locally

Python 3.11 is sufficient for preparation:

    python -m integrations.audacefi.prepare --signals data/latest.json

This returns HOLD because the existing report covers US equities. You can generate a separate crypto-only signal feed from the existing Kronos model after installing its pinned upstream code and model (see root README). Run manually, never on the expensive daily stock workflow:

    python -m integrations.audacefi.crypto_research --output integrations/audacefi/signals.local.json

It examines BTC-USD, ETH-USD and SOL-USD with completed UTC daily bars and a seven-day-week forecast calendar, including weekends. This runs local CPU inference, is unscheduled, and does not prove profitable crypto trading. Feed those signals into the proposal builder with --signals integrations/audacefi/signals.local.json. Alternatively, use an independently vetted crypto research feed in this format:

    {"asset_class":"crypto","generated_at":"2026-11-03T20:00:00Z","signals":[{"symbol":"SOL","expected_return":0.06}]}

Use an accurate UTC generated_at; do not reuse this example date. Copy settings.example.json to settings.local.json, obtain the valid season ID, your public devnet wallet address, officially verified mainnet token identities and approved category, and specify strictly capped buy amounts. The example contains placeholders and will not execute as-is. Then:

    python -m integrations.audacefi.prepare --signals /path/to/crypto-signals.json --settings integrations/audacefi/settings.local.json --output integrations/audacefi/operation.local.json

Operation files are intentionally created with exclusive-write mode and are ignored by git. Existing files must be reconciled or archived explicitly; the builder will not silently overwrite a potentially pending operation. Keep the timestamp current. The prepared operation is NOT authorization to execute.

## 2. Build the upstream kit and inspect the league state

Review/pin the source before use. Requires Node 24.22.1 and npm 11.19.1:

    git clone --branch stable https://github.com/AudaceFi/audacefi-integration-kit.git vendor/audacefi-integration-kit
    cd vendor/audacefi-integration-kit
    npm ci --ignore-scripts
    npm run check
    cd ../..

The vendor directory is already excluded by the project's root .gitignore. Never run the connected runner if upstream tests/build fail.

Complete AudaceFi onboarding for an individual API key (sandbox: https://sandbox.audacefi.app/join). In a private shell environment supply AUDACEFI_API_KEY, AUDACEFI_API_ORIGIN and AUDACEFI_CATEGORY (degen/crypto-bro/whale), then:

    node integrations/audacefi/runner.mjs state

This is an authenticated read-only query; API origin is an HTTPS origin verified during onboarding, not guessed from the sandbox URL.

## 3. Optional devnet-only execution — disabled by default

Only after reviewing both the upstream kit and your authorized deployment:
- Create private profile.local.json implementing the official BenchProfile (authorized genesis, program and ProgramData hash, USDC and devnet mint mappings, and an independently approved durable API capability). Do not copy source test-fixture values.
- Configure a separately qualified private RPC endpoint. On GitHub Actions, the default `wallet-signer.mjs` derives the *already registered* wallet only from the Actions secret, never from a tracked file. This means a workflow with access to that secret has signing authority; limit repository writers and keep it devoted to test funds.
- Put the append-only journalDirectory outside the repository on durable storage with restricted owner-only access. The settings.policy inputBudgets contains raw-unit limits per mainnet input mint, and maxOperations; it persists across restarts. Do not reset budgets by changing directories. USDC has six decimals.
- Set AUDACEFI_SETTINGS_PATH, AUDACEFI_PROFILE_PATH, AUDACEFI_OPERATION_PATH, AUDACEFI_RPC_ENDPOINT in a private shell environment. Prepare an operation using step 1. Check the wallet, season, category, allowed mint and fixed budget.
- Require all of: AUDACEFI_ENABLE_EXECUTION=true, AUDACEFI_DEVNET_ACK=true, and a final explicit --confirm argument. No schedules are registered.

    node integrations/audacefi/runner.mjs execute --confirm

An API submission is NOT on-chain finality. Preserve the original journal and reconcile ambiguous submissions rather than creating or resending a replacement. When definitive finalized evidence is available, use the official reference finality path:

    node integrations/audacefi/runner.mjs finalize OPERATION_KEY --confirm

The reference kit's security limits and network qualification requirements still apply. The runner is a wiring layer, not a hosted agent or independent security audit. Nothing in this repository enrolls an account or provides a trading wallet.

## Tests

    python -m pytest -q tests/test_audacefi.py

Only a read-only scheduled season watcher was added. The existing CI tests the Python proposal builder; the pinned official SDK is preflighted separately and no automated trading is scheduled.
