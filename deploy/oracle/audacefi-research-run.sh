#!/usr/bin/env bash
set -euo pipefail

cd /opt/autotrader
git pull --ff-only origin main
.venv/bin/python -m integrations.audacefi.history_job
.venv/bin/python -m integrations.audacefi.oracle_publish
