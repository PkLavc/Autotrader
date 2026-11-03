"""Prepare a bounded, non-executable AudaceFi proposal from crypto-only signals.

Stock research reports intentionally produce HOLD. No API or wallet calls occur.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ALLOWED_CATEGORIES = {"degen", "crypto-bro", "whale"}
MAX_FILE_BYTES = 128_000


def hold(reason: str) -> dict[str, Any]:
    return {"status": "HOLD", "reason": reason, "executionAuthorized": False}


def decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("invalid " + name)
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid " + name) from exc
    if not result.is_finite():
        raise ValueError("nonfinite " + name)
    return result


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("generated_at must be a timestamp")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid generated_at") from exc
    if moment.tzinfo is None:
        raise ValueError("generated_at must include timezone")
    return moment.astimezone(timezone.utc)


def mint(value: Any, name: str) -> str:
    if not isinstance(value, str) or not BASE58.fullmatch(value):
        raise ValueError("invalid " + name)
    return value


def read_json(path: str) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("input file too large")
    obj = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def prepare(report: dict[str, Any], settings: dict[str, Any] | None, *,
            now: datetime | None = None) -> dict[str, Any]:
    if report.get("asset_class") != "crypto":
        return hold("EQUITY_REPORT_NOT_CRYPTO")
    if settings is None:
        return hold("ACCOUNT_NOT_CONFIGURED")
    now = now or datetime.now(timezone.utc)
    age = (now.astimezone(timezone.utc) - timestamp(report.get("generated_at"))).total_seconds()
    max_age = settings.get("maxSignalAgeSeconds", 3600)
    if type(max_age) is not int or not (60 <= max_age <= 86400):
        raise ValueError("invalid maximum signal age")
    if age < -300 or age > max_age:
        return hold("CRYPTO_SIGNAL_STALE")
    if not UUID.fullmatch(str(settings.get("seasonId", ""))):
        raise ValueError("invalid seasonId")
    category = settings.get("category")
    if category not in ALLOWED_CATEGORIES:
        raise ValueError("invalid category")
    wallet = mint(settings.get("playerPublicKey"), "playerPublicKey")
    usdc = mint(settings.get("usdcMainnet"), "usdcMainnet")
    tokens = settings.get("tokens")
    if not isinstance(tokens, dict) or not (1 <= len(tokens) <= 30):
        raise ValueError("invalid allowlisted tokens")
    for symbol, token in tokens.items():
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,12}", symbol):
            raise ValueError("invalid token symbol")
        mint(token, "token mint")
        if token == usdc:
            raise ValueError("same input and output mint")
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("duplicate token mint")
    buy = decimal(settings.get("buyAmountUsdc"), "buyAmountUsdc")
    limit = decimal(settings.get("maxInputUsdc"), "maxInputUsdc")
    minimum = decimal(settings.get("minExpectedReturn", "0.02"), "minExpectedReturn")
    maximum = decimal(settings.get("maxExpectedReturn", "0.30"), "maxExpectedReturn")
    if not (Decimal("0") < buy <= limit <= Decimal("1000000")):
        raise ValueError("invalid buy limit")
    if not (Decimal("0") <= minimum <= maximum <= Decimal("1")):
        raise ValueError("invalid forecast limits")
    units = buy * Decimal(1000000)
    if units != units.to_integral_value() or units > Decimal(2**64 - 1):
        raise ValueError("USDC amount exceeds six decimal precision")
    signals = report.get("signals")
    if not isinstance(signals, list) or len(signals) > 100:
        raise ValueError("invalid signals")
    eligible: list[tuple[Decimal, str]] = []
    for row in signals:
        if not isinstance(row, dict):
            raise ValueError("invalid signal row")
        symbol = row.get("symbol")
        if symbol not in tokens:
            continue
        score = decimal(row.get("expected_return"), "expected_return")
        if minimum <= score <= maximum:
            eligible.append((score, symbol))
    if not eligible:
        return hold("NO_ELIGIBLE_CRYPTO_SIGNAL")
    eligible.sort(key=lambda row: (-row[0], row[1]))
    score, symbol = eligible[0]
    fingerprint = "|".join([
        str(settings["seasonId"]), wallet, symbol, str(units), report["generated_at"]
    ])
    key = "audacefi_" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:36]
    operation = {
        "operationKey": key,
        "seasonId": settings["seasonId"],
        "category": category,
        "playerPublicKey": wallet,
        "tokenInMint": usdc,
        "tokenOutMint": tokens[symbol],
        "amountIn": float(buy),
        "amountInRaw": str(int(units)),
        "inputDecimals": 6,
    }
    return {
        "status": "PREPARED", "symbol": symbol,
        "expected_return": str(score), "operation": operation,
        "executionAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AudaceFi isolated proposal builder (no trades)")
    parser.add_argument("--signals", default="data/latest.json")
    parser.add_argument("--settings", default="integrations/audacefi/settings.local.json")
    parser.add_argument("--output", help="Optional private operation.local.json; refuses overwrite")
    args = parser.parse_args()
    try:
        report = read_json(args.signals)
        settings = read_json(args.settings) if Path(args.settings).is_file() else None
        result = prepare(report, settings)
        if args.output and result["status"] == "PREPARED":
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as handle:
                json.dump(result["operation"], handle, indent=2)
                handle.write("\n")
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "reason": type(exc).__name__, "executionAuthorized": False}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
