# Oracle VM — optional AudaceFi research worker

This is **research only**. It does not alter the existing GitHub Actions
trader, sign transactions or place orders. Hosting the backtester on the
user's Oracle instance can reduce GitHub Actions compute usage. It is
not required: the GitHub workflow already runs a validated historical scan.

## Runtime prerequisites

* Always Free or paid Oracle VM with supported Linux, network access and
  a reliable clock; the correct Always Free shape depends on the account.
* Read-only GitHub checkout of `PkLavc/Autotrader` at `/opt/autotrader`.
* Python 3.11 or newer and dependencies from `requirements.txt` in
  `/opt/autotrader/.venv`.
* A dedicated nonprivileged `autotrader` Linux user.
* `/etc/autotrader/audacefi-research.env` owned by root with permissions
  0600, containing `AUDACEFI_API_KEY=<read-scoped API key>`. A read-only key
  is preferable; never copy a wallet seed phrase to the research worker.
* `/var/lib/autotrader/audacefi` owned by `autotrader`, mode 0700.

Install the service and timer using `sudo install`:

```bash
sudo install -m 0644 deploy/oracle/audacefi-research.service /etc/systemd/system/
sudo install -m 0644 deploy/oracle/audacefi-research.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audacefi-research.timer
sudo systemctl start audacefi-research.service
sudo systemctl status audacefi-research.service --no-pager
```

The read-only report is written to
`/var/lib/autotrader/audacefi/audacefi-historical-qualification.json`.
It contains only historical research, not credentials or a live order key.

## Moving live trading to Oracle later

Do **not** run two execution workers (Oracle and GitHub Actions) concurrently.
The current journal uses an encrypted GitHub branch plus a GitHub Actions
concurrency group; these safeguards do not automatically coordinate with
another host. Moving the live trader requires a verified single-writer lock
held across both hosts, secrets provisioned **privately** on Oracle, a proven
shutdown/standby of GitHub execution, a successful portfolio and journal
reconciliation and a supervised handoff. Until that is implemented, keep
live execution on GitHub and use Oracle only for read-only training/Kronos.

The stock `autotrader.learning.sector_factors` feedback calibration is
reused by the AudaceFi history job, while `crypto_research.CryptoKronosEngine`
already adapts the stock Kronos forecast to seven-day crypto calendars.
The stock NYSE calendar, equity broker and sector allocation must not be
copied unmodified into the Solana Devnet order executor.
