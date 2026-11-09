# Oracle VM — AudaceFi research and Kronos shadow workers

Both services are **research only**. They do not read wallet credentials,
sign transactions, place orders, or change the active strategy. GitHub Actions
remains the only trading executor until a separately verified cross-host
single-writer handoff is implemented.

## Runtime prerequisites

* Oracle Ubuntu 24.04 VM with network access and a reliable clock. The current
  instance was measured at 1 vCPU, about 5.8 GiB RAM and 45 GiB storage.
* Read-only GitHub checkout of `PkLavc/Autotrader` at `/opt/autotrader`.
* Python 3.11 or newer and the pinned Kronos source/model revisions from
  `UPSTREAM_REF`, `MODEL_REF` and `TOKENIZER_REF`.
* A dedicated nonprivileged `autotrader` Linux user.
* No AudaceFi API key, wallet address, signing key or recovery phrase is
  needed. Historical research reads a public, time-bounded frozen allowlist
  snapshot. Kronos uses public market candles.
* `/var/lib/autotrader/audacefi` is created privately by systemd, mode 0700.

Create the user and read-only checkout, then pin the already-qualified Kronos
source revision:

```bash
sudo useradd --system --user-group --home-dir /var/lib/autotrader --create-home --shell /usr/sbin/nologin autotrader
sudo install -d -o autotrader -g autotrader -m 0750 /opt/autotrader
sudo -u autotrader git clone https://github.com/PkLavc/Autotrader.git /opt/autotrader
sudo -u autotrader git -C /opt/autotrader checkout main
sudo -u autotrader install -d /opt/autotrader/vendor/Kronos
sudo -u autotrader git -C /opt/autotrader/vendor/Kronos init
sudo -u autotrader git -C /opt/autotrader/vendor/Kronos remote add origin https://github.com/shiyu-coder/Kronos.git
sudo -u autotrader git -C /opt/autotrader/vendor/Kronos fetch --depth 1 origin "$(cat /opt/autotrader/UPSTREAM_REF)"
sudo -u autotrader git -C /opt/autotrader/vendor/Kronos checkout --detach FETCH_HEAD
```

Install the Python environment as the `autotrader` user. The CPU-only PyTorch
wheel and Kronos packages are separate from the stock equity executor:

```bash
sudo -u autotrader python3 -m venv /opt/autotrader/.venv
sudo -u autotrader /opt/autotrader/.venv/bin/pip install --upgrade pip
sudo -u autotrader /opt/autotrader/.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.0'
sudo -u autotrader /opt/autotrader/.venv/bin/pip install -r /opt/autotrader/vendor/Kronos/requirements.txt
sudo -u autotrader /opt/autotrader/.venv/bin/pip install -r /opt/autotrader/requirements.txt
```

Install the service and timer using `sudo install`:

```bash
sudo install -m 0644 deploy/oracle/audacefi-research.service /etc/systemd/system/
sudo install -m 0644 deploy/oracle/audacefi-research.timer /etc/systemd/system/
sudo install -m 0644 deploy/oracle/audacefi-kronos-shadow.service /etc/systemd/system/
sudo install -m 0644 deploy/oracle/audacefi-kronos-shadow.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audacefi-research.timer
sudo systemctl enable --now audacefi-kronos-shadow.timer
sudo systemctl start audacefi-research.service
sudo systemctl start audacefi-kronos-shadow.service
sudo systemctl status audacefi-research.service --no-pager
sudo systemctl status audacefi-kronos-shadow.service --no-pager
```

The historical report uses twelve completed calendar months, regenerates and
walk-forward evaluates bounded momentum, recovery, breakout, mean-reversion and
multi-period trend candidates, and is written privately to
`/var/lib/autotrader/audacefi/audacefi-historical-qualification.json`. Training
selects one candidate from the first 75% only; promotion checks open the
untouched final 25% once and require repeated five-day improvements across at
least two market regimes. The same profile must pass on both executor assets,
SOL and ETH. Oracle research cannot change the live policy.
The private Kronos shadow journal is
`/var/lib/autotrader/audacefi/kronos-shadow.json`; it records three eligible
assets, five-day predictions and realized comparisons against five-day
momentum. The allowlist snapshot expires at the Season 1 end time and then the
historical service safely stops without using stale symbols.

Each research run fast-forwards the public checkout to `main`, then reuses
checksum-verified monthly archives and per-token evaluation results. The cache
key covers symbol, quote, months and every candle; unchanged tokens skip their
walk-forward simulation, while new monthly data invalidates only that token's
evaluation. Bump `HISTORY_CACHE_SCHEMA` when changing the research algorithm.
Historical research runs every six
hours at 00:00, 06:00, 12:00 and 18:00 UTC.
Kronos shadow runs every six hours at 03:00, 09:00, 15:00 and 21:00 UTC, so the
CPU and memory intensive jobs do not overlap. Failed jobs retry after two
minutes, up to three attempts per hour. Research is capped at 35% CPU and
1.5 GiB RAM; Kronos is capped at 25% CPU and 3 GiB RAM. These limits reserve
capacity for Ark and the VM's other services.

## Moving live trading to Oracle later

Do **not** run two execution workers (Oracle and GitHub Actions) concurrently.
The current journal uses an encrypted GitHub branch plus a GitHub Actions
concurrency group; these safeguards do not automatically coordinate with
another host. Moving the live trader requires a verified cross-host single-writer lock
held across both hosts, credentials provisioned **privately** on Oracle, a proven
shutdown/standby of GitHub execution, a successful portfolio and journal
reconciliation and a supervised handoff. Until that is implemented, keep
live execution on GitHub and use Oracle only for read-only training/Kronos.

The stock `autotrader.learning.sector_factors` feedback calibration is
reused by the AudaceFi history job, while `crypto_research.CryptoKronosEngine`
already adapts the stock Kronos forecast to seven-day crypto calendars.
The stock NYSE calendar, equity broker and sector allocation must not be
copied unmodified into the Solana Devnet order executor.

## Research result handoff

The GitHub executor reads the versioned
`integrations/audacefi/strategy-qualification.json` file and verifies its
`.sha256` sidecar before accepting entry approvals. The Oracle research service
publishes only that file and checksum with a repository-scoped deploy key stored
under `/var/lib/autotrader/.ssh`; it has no AudaceFi or wallet credentials.
Qualification pushes trigger the GitHub safe-rollout workflow, which reconciles
the existing executor before a new revision can run. GitHub Actions remains the
only order executor.
