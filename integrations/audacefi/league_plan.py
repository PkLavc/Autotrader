"""Connect league decisions to the official crypto-only proposal builder.

This is an offline preflight: it never submits or signs a transaction.
SELL_TO_USDC decisions are explicit exit requirements, not fake BUY orders.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path
from typing import Any

from .league_strategy import decide
from .prepare import prepare, hold


def build_plan(context: dict, portfolio: dict, report: dict,
               settings: dict | None, *, now: datetime | None = None) -> dict[str, Any]:
    decision = decide(context, portfolio, report, now=now)
    if decision["status"] != "PROPOSED":
        return decision
    if not isinstance(settings, dict) or settings.get("category") != "crypto-bro":
        return hold("CRYPTO_BRO_SETTINGS_REQUIRED")
    if settings.get("seasonId") != context.get("season_id"):
        return hold("VERIFIED_SEASON_ID_MISMATCH")
    if settings.get("playerPublicKey") != context.get("wallet_address"):
        return hold("VERIFIED_LEAGUE_WALLET_MISMATCH")
    verified_mints = context.get("approved_token_mints")
    if not isinstance(verified_mints, dict):
        return hold("VERIFIED_TOKEN_MINTS_REQUIRED")
    configured_tokens = settings.get("tokens")
    if not isinstance(configured_tokens, dict):
        return hold("VERIFIED_TOKEN_MINT_MISMATCH")
    if any(not isinstance(verified_mints.get(symbol), str) or
           configured_tokens.get(symbol) != verified_mints[symbol]
           for symbol in context.get("eligible_symbols", [])):
        return hold("VERIFIED_TOKEN_MINT_MISMATCH")
    if settings.get("usdcMainnet") != context.get("approved_usdc_mint"):
        return hold("VERIFIED_USDC_MINT_MISMATCH")

    for candidate in decision["actions"]:
        symbol = candidate["symbol"]
        if candidate["side"] != "BUY_WITH_USDC":
            continue
        configured = Decimal(str(settings.get("buyAmountUsdc", "0")))
        approved = Decimal(str(candidate["maximum_input_usdc"]))
        limit = Decimal(str(settings.get("maxInputUsdc", "0")))
        budget = min(configured, approved, limit).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if budget < Decimal("1"):
            continue
        guarded_settings = {**settings, "buyAmountUsdc": str(budget)}
        guarded_report = {**report, "signals": [
            signal for signal in report.get("signals", [])
            if signal.get("symbol") == symbol
        ]}
        try:
            result = prepare(guarded_report, guarded_settings, now=now)
        except (ValueError, KeyError):
            continue
        if result.get("status") != "PREPARED":
            continue
        return {
            **result,
            "league": {
                "model": decision["model"],
                "estimated_net_edge": candidate["estimated_net_edge"],
                "risk_reference_stop_fraction": candidate["stop_reference_fraction"],
                "phase": decision["phase"],
            },
            "executionAuthorized": False,
        }
    return hold("NO_ORDER_WITHIN_VERIFIED_BUDGET")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline AudaceFi decision-to-operation preflight")
    parser.add_argument("--context", required=True)
    parser.add_argument("--portfolio", required=True)
    parser.add_argument("--signals", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--output", help="Private operation file; exclusive create, never overwrite")
    args = parser.parse_args()
    try:
        def read(path: str) -> dict:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected JSON object")
            return data

        result = build_plan(read(args.context), read(args.portfolio),
                            read(args.signals), read(args.settings))
        if args.output and result.get("status") == "PREPARED":
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as file:
                json.dump(result["operation"], file, indent=2, sort_keys=True)
                file.write("\n")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        print(json.dumps(hold("LEAGUE_PLAN_INPUT_INVALID")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
