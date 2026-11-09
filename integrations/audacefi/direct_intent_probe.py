"""One-off authenticated direct API write probe; never signs or submits a swap."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import http.client
import json
import os
import re

HOST = "sandbox.audacefi.app"
MAX_BYTES = 262144
MINT = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}\Z")
CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def request(key: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPSConnection(HOST, timeout=12)
    try:
        data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        connection.request(method, path, body=data, headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        response = connection.getresponse()
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("RESPONSE_TOO_LARGE")
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("INVALID_RESPONSE")
        return response.status, parsed
    finally:
        connection.close()


def error_code(body: dict) -> str:
    error = body.get("error")
    for value in (body.get("code"), error.get("code") if isinstance(error, dict) else error):
        if isinstance(value, str) and CODE.fullmatch(value):
            return value
    return "UNAVAILABLE"


def token(tokens: object, symbol: str) -> dict:
    if not isinstance(tokens, list):
        raise ValueError("ALLOWLIST_INVALID")
    matches = [item for item in tokens if isinstance(item, dict) and item.get("symbol") == symbol]
    if len(matches) != 1 or not MINT.fullmatch(str(matches[0].get("mint", ""))):
        raise ValueError("TOKEN_IDENTITY_UNAVAILABLE")
    if matches[0].get("eligible") is not True and matches[0].get("exempt") is not True:
        raise ValueError("TOKEN_NOT_ELIGIBLE")
    return matches[0]


def main() -> int:
    key = os.environ.get("AUDACEFI_API_KEY", "")
    expected_wallet = os.environ.get("AUDACEFI_WALLET_ADDRESS", "")
    if not key or "\n" in key or "\r" in key or not expected_wallet:
        print("DIRECT_PROBE=SECRETS_UNAVAILABLE")
        return 1
    try:
        state_status, state = request(key, "GET", "/api/v1/me/state?category=crypto-bro&mode=real")
        if state_status != 200:
            print("DIRECT_PROBE=STATE_HTTP_" + str(state_status))
            return 1
        season = state.get("season")
        active = season.get("active") if isinstance(season, dict) else None
        wallet = state.get("wallet")
        now = datetime.now(timezone.utc)
        if (not isinstance(active, dict) or active.get("status") != "active" or
            active.get("executionMode") not in ("devnet_onchain", "devnet-onchain") or
            not isinstance(wallet, dict) or wallet.get("state") != "ok" or
            wallet.get("walletAddress") != expected_wallet or
            state.get("category") != "crypto-bro" or state.get("mode") != "real"):
            raise ValueError("SEASON_OR_WALLET_NOT_READY")
        starts = datetime.fromisoformat(active["startsAt"].replace("Z", "+00:00"))
        ends = datetime.fromisoformat(active["endsAt"].replace("Z", "+00:00"))
        if starts.tzinfo is None or ends.tzinfo is None or not starts <= now < ends:
            raise ValueError("OUTSIDE_SEASON_WINDOW")
        season_id = active.get("id")
        if not isinstance(season_id, str):
            raise ValueError("SEASON_ID_UNAVAILABLE")
        allow_status, allow = request(key, "GET", "/api/v1/me/allowlist?category=crypto_bro&mode=real")
        if allow_status != 200 or allow.get("seasonId") != season_id or allow.get("allowlistFrozen") is not True:
            raise ValueError("ALLOWLIST_UNAVAILABLE")
        usdc, sol = token(allow.get("tokens"), "USDC"), token(allow.get("tokens"), "SOL")
        if usdc.get("decimals") != 6 or usdc["mint"] == sol["mint"]:
            raise ValueError("TOKEN_DECIMALS_OR_PAIR_INVALID")
        material = "autotrader-s1-direct-intent-25-usdc-sol:" + season_id + ":" + expected_wallet
        idem = "at_s1_" + sha256(material.encode()).hexdigest()[:48]
        intent = {
            "seasonId": season_id,
            "category": "crypto_bro",
            "tokenInMint": usdc["mint"],
            "tokenOutMint": sol["mint"],
            "amountIn": 25,
            "idempotencyKey": idem,
            "surface": "api",
            "inputBinding": "audacefi.swap-intent-input.v1",
        }
        status, body = request(key, "POST", "/api/v1/swap-intents", intent)
        print("DIRECT_INTENT_HTTP=" + str(status))
        if status not in (200, 201):
            print("DIRECT_INTENT_ERROR_CODE=" + error_code(body))
            return 1
        if body.get("status") != "built" or not isinstance(body.get("intentId"), str):
            print("DIRECT_PROBE=INTENT_RESPONSE_UNRECOGNIZED")
            return 1
        print("DIRECT_INTENT_BUILT=true")
        print("DIRECT_INTENT_REPLAYED=" + str(body.get("replayed") is True).lower())
        print("DIRECT_INPUT_BINDING_ECHO=" + str(body.get("inputBinding") == intent["inputBinding"]).lower())
        quote_status, quote = request(key, "POST", "/api/v1/swap-intents/" + body["intentId"] + "/quote", {"amountInRaw": "25000000"})
        print("DIRECT_QUOTE_HTTP=" + str(quote_status))
        if quote_status not in (200, 201):
            print("DIRECT_QUOTE_ERROR_CODE=" + error_code(quote))
            return 1
        print("DIRECT_QUOTE_CONTEXT_MATCH=" + str(
            quote.get("intentId") == body["intentId"] and quote.get("amountInRaw") == "25000000"
        ).lower())
        print("DIRECT_QUOTE_HAS_EXPIRY=" + str(isinstance(quote.get("expiresAt"), str)).lower())
        print("DIRECT_PROBE=NO_SIGNATURE_OR_SUBMISSION")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, http.client.HTTPException) as exc:
        code = str(exc)
        print("DIRECT_PROBE=" + (code if CODE.fullmatch(code) else "READ_OR_TRANSPORT_FAILURE"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
