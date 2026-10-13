# Security

Autotrader is intentionally configured as a public, paper-trading repository.

## Credentials

Do not commit broker API keys, passwords, private tokens, seed phrases, account identifiers, or other secrets to this repository.

The research workflow does not require broker credentials. The separate broker workflow reads
credentials only from GitHub Actions secrets and is disabled by default.

Broker credentials must be trading-only whenever the broker supports scoped keys. Do not grant
withdrawal, transfer, account-management, or user-administration permissions. Rotate a key
immediately if it appears in source, logs, artifacts, screenshots, chat, or shell history.

Live mode is defense in depth: endpoint selection, repository policy, an explicit allow flag,
a confirmation secret, order-submission enablement, market clock, signal freshness, and notional
limits must all pass. Removing any one of these checks is a security-sensitive change.

## Upstream isolation

Kronos source and model revisions are pinned. Candidate upstream code is validated in a GitHub Actions job with read-only repository permissions. The job that writes validated revision pins does not execute upstream code.

The paper-trading workflow follows the same boundary: model execution occurs in a read-only job and only generated paper-state JSON is passed to a separate write-enabled persistence job.

## Reporting

If you find a vulnerability, avoid publishing credentials or sensitive account information in a public issue.
