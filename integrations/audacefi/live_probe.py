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
    ("me", "/api/v1/me"),
    ("state", "/api/v1/me/state?category=crypto-bro&mode=real"),
    ("allowlist", "/api/v1/me/allowlist?category=crypto_bro&mode=real"),
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
    if name == "me" and isinstance(body, dict):
        account = body.get("account")
        account = account if isinstance(account, dict) else {}
        scopes = body.get("credentialScopes")
        print("ACCOUNT_AI_AGENT=" + str(account.get("participantType") == "ai_agent").lower())
        print("ACCOUNT_ACTIVE=" + str(account.get("status") == "active").lower())
        print("ONBOARDING_COMPLETE=" + str(body.get("onboardingComplete") is True).lower())
        print("API_SCOPES_FIELD_PRESENT=" + str(isinstance(scopes, list)).lower())
        if isinstance(scopes, list):
            print("API_READ_SCOPE=" + str("read" in scopes).lower())
            print("API_EXECUTE_SCOPE=" + str("execute" in scopes).lower())
        return
    if name == "state" and isinstance(body, dict):
        print("STATE_ROOT_FIELDS=" + ",".join(sorted(
            key for key in body if key in {
                "account", "balances", "dailyLimits", "drawdown", "equity",
                "history", "limits", "performance", "portfolio", "positions",
                "risk", "season", "stats", "trades", "wallet"
            }
        )))
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
            print("SEASON_HAS_DATES=" + str(bool(current.get("startsAt") and current.get("endsAt"))).lower())
        print("WALLET_READY=" + str(wallet.get("state") == "ok").lower())
        print("WALLET_LINKED=" + str(bool(wallet.get("walletAddress"))).lower())
        return
    if name == "allowlist" and isinstance(body, dict):
        tokens = body.get("tokens")
        print("ALLOWLIST_FROZEN=" + str(body.get("allowlistFrozen") is True).lower())
        print("ALLOWLIST_SEASON_PRESENT=" + str(isinstance(body.get("seasonId"), str)).lower())
        if isinstance(tokens, list):
            print("ALLOWLIST_ELIGIBLE_COUNT=" + str(sum(
                isinstance(token, dict) and token.get("eligible") is True
                for token in tokens
            )))
            for symbol in ("SOL", "BTC", "ETH"):
                print(symbol + "_ELIGIBLE=" + str(any(
                    isinstance(token, dict) and token.get("eligible") is True and
                    token.get("symbol") == symbol for token in tokens
                )).lower())
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
    active_season_id = None
    expected_wallet = os.environ.get("AUDACEFI_WALLET_ADDRESS", "")
    for name, route in ROUTES:
        try:
            status, body = read_route(key, route)
            print(name.upper() + "_HTTP=" + str(status))
            if status == 200:
                summarize(name, body)
                if name == "state" and isinstance(body, dict):
                    season = body.get("season")
                    active = season.get("active") if isinstance(season, dict) else None
                    active_season_id = active.get("id") if isinstance(active, dict) else None
                    wallet = body.get("wallet")
                    linked = wallet.get("walletAddress") if isinstance(wallet, dict) else None
                    matched = bool(expected_wallet and linked == expected_wallet)
                    print("LINKED_WALLET_MATCH=" + str(matched).lower())
                    ok = ok and matched
                if name == "allowlist" and isinstance(body, dict):
                    matched = bool(active_season_id and body.get("seasonId") == active_season_id)
                    print("ALLOWLIST_SEASON_MATCH=" + str(matched).lower())
                    ok = ok and matched
            if status != 200 and isinstance(body, dict):
                # Only a bounded machine code is safe to expose from an error DTO.
                code = body.get("code")
                if isinstance(code, str) and 1 <= len(code) <= 64 and all(
                    char.isupper() or char.isdigit() or char == "_" for char in code
                ):
                    print(name.upper() + "_ERROR_CODE=" + code)
            if status != 200:
                ok = False
        except (OSError, ValueError, http.client.HTTPException):
            print(name.upper() + "_CONNECT_FAILED")
            ok = False
    print("ORDERS_SUBMITTED=0")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
