"""Authenticated AudaceFi live endpoint probe. NO trading, no raw payloads in logs.

Only calls documented GET routes against the sandbox API. All sensitive fields,
account/wallet identifiers and credentials are deliberately omitted from output.
"""
from __future__ import annotations
import http.client
import json
import os
import sys

HOST = "sandbox.audacefi.app"
ROUTES = (
    ("state", "/api/v1/me/state?category=crypto-bro"),
    ("allowlist", "/api/v1/me/allowlist?category=crypto-bro"),
    ("bench", "/api/v1/devnet/bench"),
)
MAX_BYTES = 256 * 1024


def read_route(key: str, route: str) -> tuple[int, dict | list | None]:
    c = http.client.HTTPSConnection(HOST, timeout=12)
    try:
        c.request(
            "GET", route,
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"}
        )
        resp = c.getresponse()
        raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized response")
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if isinstance(body, (dict, list)):
            return resp.status, body
        return resp.status, None
    finally:
        c.close()


def summarize(name: str, body: dict | list | None) -> None:
    if name == "state" and isinstance(body, dict):
        season = body.get("season") if isinstance(body.get("season"), dict) else {}
        current = season.get("active")
        wallet = body.get("wallet") if isinstance(body.get("wallet"), dict) else {}
        print("SEASON_ACTIVE=" + str(isinstance(current, dict)).lower())
        if isinstance(current, dict):
            status = current.get("status")
            print("SEASON_STATUS=" + (status if status in
                {"active", "live", "in_progress", "running", "trading", "closed", "ended"} else "other"))
            print("EXECUTION_MODE=" + (
                current.get("executionMode") if current.get("executionMode") in
                {"devnet-onchain", "devnet_onchain", "paper", "offchain"} else "other"))
        print("WALLET_READY=" + str(wallet.get("state") == "ok").lower())
        print("WALLET_LINKED=" + str(bool(wallet.get("walletAddress"))).lower())
        return
    if isinstance(body, dict):
        print(name.upper() + "_ROOT_FIELDS=" + ",".join(sorted(
            k for k in body if k in {
                "season", "category", "mode", "tokens", "assets", "allowlist", "items",
                "eligible", "mints", "programId", "programData", "programDataSha256",
                "feeBps", "poster", "admin", "protocol", "pairs", "executionMode",
                "wallet", "bench", "config", "status", "success", "data"
            }
        )))
        print(name.upper() + "_IS_OBJECT=true")
    elif isinstance(body, list):
        print(name.upper() + "_IS_LIST=true")
        print(name.upper() + "_ITEM_COUNT=" + str(len(body)))
    else:
        print(name.upper() + "_VALID_JSON=false")


def main() -> int:
    key = os.environ.get("AUDACEFI_API_KEY", "")
    if not key:
        print("AUDACEFI_KEY_MISSING")
        return 1
    if "\n" in key or "\r" in key:
        print("AUDACEFI_KEY_MALFORMED")
        return 1
    ok = True
    for name, route in ROUTES:
        try:
            status, body = read_route(key, route)
            print(name.upper() + "_HTTP=" + str(status))
            if status == 200:
                summarize(name, body)
            elif status in (401, 403):
                ok = False
        except (OSError, ValueError, http.client.HTTPException):
            print(name.upper() + "_CONNECT_FAILED")
            ok = False
    print("ORDERS_SUBMITTED=0")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
