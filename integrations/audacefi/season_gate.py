"""AudaceFi Season 1 read-only watch. Never signs, submits or trades."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://sandbox.audacefi.app/api/v1/me/state?category=crypto-bro"
MAX_RESPONSE = 262144
ACTIVE_STATUSES = {"active", "live", "in_progress", "running", "trading"}
DEVNET_MODES = {"devnet-onchain", "devnet_onchain"}


def _utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid season time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("missing season timezone")
    return parsed.astimezone(timezone.utc)


def classify(payload: object, *, now: datetime | None = None) -> str:
    """Fail closed on unrecognized season data; a season is never an order permission."""
    if not isinstance(payload, dict):
        return "HOLD_INVALID_STATE"
    season = payload.get("season")
    if not isinstance(season, dict) or "active" not in season:
        return "HOLD_UNRECOGNIZED_SEASON"
    current = season["active"]
    if current is None:
        return "WAITING_FOR_SEASON"
    if not isinstance(current, dict):
        return "HOLD_INVALID_SEASON"
    status = current.get("status")
    if not isinstance(status, str):
        return "HOLD_INVALID_SEASON_STATUS"
    status = status.lower()
    if status in {"scheduled", "pending", "upcoming", "closed", "finished", "ended"}:
        return "WAITING_FOR_SEASON"
    if status not in ACTIVE_STATUSES:
        return "HOLD_UNKNOWN_SEASON_STATUS"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return "HOLD_INVALID_LOCAL_CLOCK"
    try:
        start = _utc(current.get("startsAt"))
        end = _utc(current.get("endsAt"))
    except (ValueError, OverflowError):
        return "HOLD_INVALID_SEASON_TIME"
    if (start is not None and now < start) or (end is not None and now >= end):
        return "WAITING_FOR_SEASON"
    if current.get("executionMode") not in DEVNET_MODES:
        return "HOLD_UNVERIFIED_EXECUTION_MODE"
    wallet = payload.get("wallet")
    if not isinstance(wallet, dict) or wallet.get("state") != "ok":
        return "SEASON_ACTIVE_WALLET_PENDING"
    if wallet.get("category") not in {"crypto-bro", "crypto_bro"}:
        return "HOLD_WALLET_CATEGORY_MISMATCH"
    if not wallet.get("walletAddress"):
        return "SEASON_ACTIVE_WALLET_PENDING"
    return "SEASON_ACTIVE_DEPLOYMENT_PENDING"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_remote_state(api_key: str) -> object:
    if not api_key or len(api_key) > 512:
        raise ValueError("API key missing or oversized")
    request = urllib.request.Request(
        API_URL,
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise ValueError("unexpected response")
        contents = response.read(MAX_RESPONSE + 1)
        if len(contents) > MAX_RESPONSE:
            raise ValueError("oversized response")
        return json.loads(contents)


def main() -> int:
    secret = os.environ.get("AUDACEFI_API_KEY", "")
    try:
        outcome = classify(read_remote_state(secret))
    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError):
        outcome = "HOLD_API_READ_FAILED"
    print("AUDACEFI_SEASON=" + outcome)
    print("AUDACEFI_TRADING=DISABLED")
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as file:
            file.write("status=" + outcome + "\n")
            file.write("season_active=" + str(outcome.startswith("SEASON_ACTIVE")).lower() + "\n")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as file:
            file.write("## AudaceFi Season 1\n\nStatus: **" + outcome + "**\n\n")
            file.write("Trades: **not authorized by this watcher**.\n\n")
            file.write("No private data or secrets were printed.\n")
    return 1 if outcome.startswith("HOLD") else 0


if __name__ == "__main__":
    sys.exit(main())
