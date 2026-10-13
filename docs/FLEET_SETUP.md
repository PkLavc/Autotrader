# Private multi-account fleet

The fleet uses one research signal for every enabled account while keeping each account's
cash, holdings, limits, orders, failures, credentials, and dashboard session separate.

## Security model

- The public GitHub repository contains no account snapshots or broker credentials.
- Credentials are encrypted with AES-256-GCM before they are stored in Cloudflare D1.
- The encryption key and API control tokens are Cloudflare Worker secrets, not variables.
- Dashboard links carry a one-time invitation in the URL fragment (`#...`). Browsers do not
  send fragments in HTTP requests or referrer headers. The page immediately removes it and
  exchanges it for a `Secure`, `HttpOnly`, `SameSite=Strict` cookie.
- Invitations expire and cannot be reused. Sessions can be revoked without rotating broker
  credentials.
- The account page is public HTML but its data API only returns the account attached to the
  authenticated cookie.
- A dedicated, more-specific Cloudflare route at `api.pklavc.com/autotrader/*` isolates this
  Worker from the site's existing catch-all API Worker. Because both hosts share the
  `pklavc.com` site, the strict session cookie remains first-party/same-site.

Never publish a reusable bearer token as a path or query string and never commit a generated
invite URL. Send it directly to its intended user.

## Runtime flow

1. The daily research workflow publishes one close-of-session target allocation.
2. `.github/workflows/fleet-paper.yml` authenticates to the private Worker endpoint.
3. The Worker validates freshness and limits, records a fleet run, and queues one job for
   each active account.
4. Each queue job independently reads that account, plans sells before buys, prevents a sell
   when no position exists, caps purchases by available cash, and checks deterministic order
   identifiers before submission.
5. A failed or blocked account is audited without stopping any other account.
6. The account's private snapshot is refreshed for its dashboard.

## Cost and tax simulation limits

The portfolio is limited to Alpaca US equities in paper mode. Standard self-directed
equity commissions are modeled as $0, with SEC/FINRA TAF/CAT pass-through estimates based
on the current published schedule. Alpaca aggregates and rounds several fee totals per
account/day, so per-order figures are estimates, not broker-confirmed charges. Compare
them with the broker's activity ledger and statements. The model excludes routing-specific
fees, FX conversion, margin interest, ADR pass-through fees, and tax withholding on
dividends. It credits gross cash dividends in local historical simulation and labels them
as gross/unwithheld.

US tax is marked not calculated because liability is taxpayer-specific. For a Brazilian
resident using a US account, the dashboard reports realized USD gains and a 15% reference
rate, but does not claim tax due until complete tax lots and event-date BRL conversion are
reconciled. This is not a tax filing calculation. Brazilian B3 rules are not selectable
for the Alpaca US equities integration.

There are three independent paper-order gates: repository variable
`AUTOTRADER_FLEET_ORDER_SUBMISSION_ENABLED`, the account's `execution_enabled` flag, and
Worker variable `PAPER_ORDER_SUBMISSION_ENABLED`. All default to false.

## Infrastructure bootstrap

From `fleet-worker/`, create D1 and both queues, replace `D1_DATABASE_ID` in `wrangler.toml`,
apply migrations, and deploy:

```powershell
npm install
npx wrangler d1 create autotrader-fleet
npx wrangler queues create autotrader-fleet-paper
npx wrangler queues create autotrader-fleet-dead
npx wrangler d1 migrations apply autotrader-fleet --remote
npx wrangler deploy
```

Set these secrets. Generate fresh random values in memory; do not save them in a file:

- `ACCOUNT_ENCRYPTION_KEY`: exactly 32 random bytes, base64url encoded.
- `FLEET_ADMIN_TOKEN`: local administrative bearer token.
- `FLEET_SERVICE_TOKEN`: GitHub-to-Worker dispatch bearer token.

The same service token must be stored as GitHub Actions secret
`AUTOTRADER_FLEET_SERVICE_TOKEN`. Keep the admin token out of GitHub and browser storage.

To start an administrative session, generate a new token, set it on the Worker, and retain it
only in the current PowerShell process:

```powershell
$buffer = [byte[]]::new(32)
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
$env:AUTOTRADER_FLEET_ADMIN_TOKEN = [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+','-').Replace('/','_')
$env:AUTOTRADER_FLEET_ADMIN_TOKEN | npx.cmd wrangler secret put FLEET_ADMIN_TOKEN -c fleet-worker/wrangler.toml
```

## Register accounts

The command-line tool reads secrets only from the current process. It never writes them to
disk or includes them in command-line arguments:

```powershell
$env:ALPACA_API_KEY = "paper-key"
$env:ALPACA_API_SECRET = "paper-secret"
python scripts/fleet_admin.py create --name "Conta de teste"
Remove-Item Env:ALPACA_API_KEY, Env:ALPACA_API_SECRET
python scripts/fleet_admin.py list
```

New accounts start with order execution disabled. Enable one only after checking its dry-run
audit:

```powershell
python scripts/fleet_admin.py update ACCOUNT_ID --execution true
python scripts/fleet_admin.py invite ACCOUNT_ID --valid-minutes 60
```

The invite command prints the one-time link. Once opened, that browser receives a 30-day
revocable session and the invitation is consumed.

Disable a compromised link/session immediately with:

```powershell
python scripts/fleet_admin.py revoke-access ACCOUNT_ID
```

## Live accounts and other people

Fleet v1 deliberately rejects live accounts. For accounts owned by other people, use Alpaca
Connect OAuth so each owner authorizes access at Alpaca; do not ask for their passwords or
copy API secrets through messages. Alpaca requires the app to be registered and approved
before live trading for other users. Broker API is the alternative when brokerage accounts
are created inside your own product.

Managing other people's real-money accounts can create investment-adviser, authorization,
disclosure, privacy, tax, and jurisdiction-specific obligations. Complete broker approval
and qualified legal/compliance review before adding a live execution path.
