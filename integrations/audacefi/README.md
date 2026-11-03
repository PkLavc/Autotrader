# AudaceFi integration — Autotrader

Optional and isolated integration for the AudaceFi Solana devnet league. It does not alter the equity strategy, Alpaca broker, stock paper ledger, research schedule, or account fleet. No automatic orders or recurring Actions workflow are enabled.

## Status

- Equity reports from data/latest.json intentionally result in HOLD, not a crypto trade. Stock tickers must never be reinterpreted as tokens.
- The crypto-only proposal builder is ready for a separate trusted crypto-signal feed, explicit season/wallet/token allowlist and a fixed USDC input budget. It makes no network calls and does not sign or trade.
- The connected JavaScript runner uses the upstream audited integration-kit functions, including the durable journal, quote checks, Solana reference adapter and wallet callback. Real devnet operation requires the owner's onboarding, an individual API key, reviewed deployment profile, qualified RPC, and an external signing adapter. None are included here.
- All execution features default to disabled and require a deliberate command. No mainnet, brokerage, fleet or existing-account credentials are passed to AudaceFi.

Official source: https://github.com/AudaceFi/audacefi-integration-kit (review and pin a tested revision of the stable branch before any connected execution). Read its CONNECTING.md, REFERENCE.md and RECOVERY.md; local demos do not establish deployment or wallet approval.

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
- Configure a separately qualified private RPC endpoint and a wallet adapter module that exports wallet.publicKey() and wallet.signTransaction(unsignedBase64). Keep the private key in the wallet, never in GitHub, an env var or this repository.
- Put the append-only journalDirectory outside the repository on durable storage with restricted owner-only access. The settings.policy inputBudgets contains raw-unit limits per mainnet input mint, and maxOperations; it persists across restarts. Do not reset budgets by changing directories. USDC has six decimals.
- Set AUDACEFI_SETTINGS_PATH, AUDACEFI_PROFILE_PATH, AUDACEFI_OPERATION_PATH, AUDACEFI_SIGNER_MODULE, AUDACEFI_RPC_ENDPOINT in a private shell environment. Prepare an operation using step 1. Check the wallet, season, category, allowed mint and fixed budget.
- Require all of: AUDACEFI_ENABLE_EXECUTION=true, AUDACEFI_DEVNET_ACK=true, and a final explicit --confirm argument. No schedules are registered.

    node integrations/audacefi/runner.mjs execute --confirm

An API submission is NOT on-chain finality. Preserve the original journal and reconcile ambiguous submissions rather than creating or resending a replacement. When definitive finalized evidence is available, use the official reference finality path:

    node integrations/audacefi/runner.mjs finalize OPERATION_KEY --confirm

The reference kit's security limits and network qualification requirements still apply. The runner is a wiring layer, not a hosted agent or independent security audit. Nothing in this repository enrolls an account or provides a trading wallet.

## Tests

    python -m pytest -q tests/test_audacefi.py

There is no added scheduled workflow. The existing CI tests the Python proposal builder; the connected official SDK's own check must also pass before it is used.
