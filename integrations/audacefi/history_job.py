"""Offline historical qualification of AudaceFi-approved symbols.

Uses Binance public spot archives, 15-minute candles, verified checksums and
a strict untouched out-of-sample holdout. Reuses the stock AutoTrader's
conservative Bayesian feedback calibration without invoking its stock
broker, NYSE scheduler, equity allocations or paper ledger.
Never sends a trade or changes the live wallet strategy.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
import re
import sys

from autotrader.config import StrategyConfig
from autotrader.learning import sector_factors
from .adaptive_learning import PRESETS, _simulate, validate_candles
from .history_research import WINDOW, complete_months, load_history, walk_forward
from .live_probe import read_route


def stock_learning_calibration(bars: list[list], symbol: str) -> float:
    """Reuses the existing AutoTrader's shrinkage calibration as research only.

    Each historical TRAINING fold is a completed, mature observation.
    The unseen final quarter is never considered for these factors.
    """
    split = (len(bars) // WINDOW) * 3 // 4
    observations = []
    for index in range(split):
        block = bars[index * WINDOW:(index + 1) * WINDOW]
        if not validate_candles(block):
            continue
        baseline = _simulate(block, PRESETS["baseline"], 96, len(block))
        # Only matured, genuinely traded rounds are evidence. Windows with
        # no entries are not losses and must not bias the posterior.
        if baseline["trades"] > 0:
            observations.append({"sector": symbol,
                                 "excess_return": baseline["net_return"]})
    cfg = StrategyConfig(adaptive_min_total_evaluations=20,
                         adaptive_min_sector_evaluations=12)
    factors = sector_factors({"completed": observations}, cfg)
    factor = factors.get(symbol, 1.0)
    return factor if 0.95 <= factor <= 1.05 else 1.0


def eligible_symbols(api_key: str) -> list[str]:
    a_status, state = read_route(api_key, "/api/v1/me/state?category=crypto-bro&mode=real")
    b_status, allowed = read_route(api_key, "/api/v1/me/allowlist?category=crypto_bro&mode=real")
    active = state.get("season", {}).get("active") if isinstance(state, dict) else None
    if (a_status != 200 or b_status != 200 or not isinstance(active, dict) or
            active.get("status") != "active" or
            not isinstance(allowed, dict) or
            allowed.get("seasonId") != active.get("id") or
            allowed.get("allowlistFrozen") is not True or
            not isinstance(allowed.get("tokens"), list)):
        raise RuntimeError("HISTORY_SEASON_UNVERIFIED")
    tokens = {
        item["symbol"] for item in allowed["tokens"]
        if isinstance(item, dict) and item.get("eligible") is True and
        isinstance(item.get("symbol"), str) and
        re.fullmatch("[A-Z0-9]{2,12}", item["symbol"]) and
        item["symbol"] != "USDC"
    }
    if len(tokens) < 1 or len(tokens) > 24:
        raise RuntimeError("HISTORY_ALLOWLIST_INVALID")
    return sorted(tokens)


def research_one(symbol: str, months: list[str]) -> dict:
    try:
        quote = "USDT"
        try:
            bars = load_history(symbol, months, quote=quote)
        except Exception as first:
            # A missing or unsupported USDT market is not a trading signal.
            # Only a 404 may trigger a fully reverified USDC spot history;
            # corrupt data, missing checksums or outages fail closed.
            if type(first).__name__ != "HTTPError" or getattr(first, "code", None) != 404:
                raise
            quote = "USDC"
            bars = load_history(symbol, months, quote=quote)
        outcome = walk_forward(bars)
        calibration = stock_learning_calibration(bars, symbol)
        return {
            "symbol": symbol, "proxyQuote": quote,
            "historyBars": len(bars),
            "bayesianPriorMultiplier": calibration,
            **outcome,
        }
    except Exception as exc:
        # Public archive presence varies by market. Do not fabricate candles.
        if type(exc).__name__ not in {
            "HTTPError", "URLError", "TimeoutError", "ConnectionError",
            "ValueError", "BadZipFile", "OSError", "UnicodeDecodeError",
            "IncompleteRead", "RemoteDisconnected", "EOFError",
        }:
            raise
        return {"symbol": symbol, "status": "HISTORY_UNAVAILABLE",
                "reason": type(exc).__name__}


def main() -> int:
    key = os.environ.get("AUDACEFI_API_KEY", "")
    if not key or len(key) > 512 or "\r" in key or "\n" in key:
        print("AUDACEFI_HISTORY=KEY_REQUIRED")
        return 1
    symbols = eligible_symbols(key)
    months = complete_months(datetime.now(timezone.utc), count=4)
    print("AUDACEFI_HISTORY_MONTHS=" + ",".join(months))
    print("AUDACEFI_HISTORY_ELIGIBLE_SYMBOL_COUNT=" + str(len(symbols)))
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(research_one, symbol, months): symbol
                   for symbol in symbols}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["symbol"])
    checked = sum(row.get("historyBars", 0) >= 2560 for row in results)
    qualified = sum(row.get("status") == "VALIDATED_HISTORICAL_CANDIDATE"
                    for row in results)
    outcome = {
        "schemaVersion": 1, "basis": "binance_verified_spot_monthly_15m_usdt_proxy",
        "readOnly": True, "liveExecutionAuthorized": False,
        "months": months, "eligibleSymbols": len(symbols),
        "historicalCoverage": checked, "validatedCandidates": qualified,
        "stockCalibrationMethod": "autotrader.learning.sector_factors",
        "results": results,
    }
    with open("audacefi-historical-qualification.json", "w", encoding="utf-8") as f:
        json.dump(outcome, f, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")
    print("AUDACEFI_HISTORY_COVERED=" + str(checked))
    print("AUDACEFI_HISTORY_VALIDATED_CANDIDATES=" + str(qualified))
    print("AUDACEFI_HISTORY_REPORT=audacefi-historical-qualification.json")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## AudaceFi — historical 15m strategy qualification\n\n")
            f.write(f"Official allowlist: {len(symbols)} assets. Four completed months: {', '.join(months)}.\n\n")
            f.write(f"{checked} supported histories, {qualified} independently validated candidate(s).\n\n")
            f.write("| Token | Status | Bars | Candidate | Holdout net | AutoTrader calibrated factor |\n")
            f.write("|---|---|---:|---|---:|---:|\n")
            for item in results:
                ho = item.get("holdout", {})
                f.write(f"| {item['symbol']} | {item['status']} | {item.get('historyBars', 0)} | "
                        f"{item.get('profile', '—')} | "
                        f"{100 * ho.get('net_return', 0):.2f}% | "
                        f"{item.get('bayesianPriorMultiplier', 1.0):.4f} |\n")
            f.write("\nThis is research only. Historical performance is not a promise of live gains.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
