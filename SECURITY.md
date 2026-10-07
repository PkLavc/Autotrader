# Security

Autotrader is intentionally configured as a public, paper-trading repository.

## Credentials

Do not commit broker API keys, passwords, private tokens, seed phrases, account identifiers, or other secrets to this repository.

The current project does not require broker credentials because it does not place real-money orders.

## Upstream isolation

Kronos source and model revisions are pinned. Candidate upstream code is validated in a GitHub Actions job with read-only repository permissions. The job that writes validated revision pins does not execute upstream code.

The paper-trading workflow follows the same boundary: model execution occurs in a read-only job and only generated paper-state JSON is passed to a separate write-enabled persistence job.

## Reporting

If you find a vulnerability, avoid publishing credentials or sensitive account information in a public issue.
