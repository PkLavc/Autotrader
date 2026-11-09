"""Read-only research coverage for every eligible AudaceFi Crypto Bro token.

No orders, wallet signing, candidate promotion or secret printing.
The exchange may not have a USD market for every eligible on-chain token.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .live_probe import read_route
from .adaptive_learning import PRESETS, _simulate, validate_candles

BASE = "/api/v1/me"
LIMIT = 30


def coinbase_closed_bars(symbol: str, now: datetime) -> list:
    if not re.fullmatch(r"[A-Z0-9]{2,12}", symbol) or symbol == "USDC":
        return []
    url = f"https://api.exchange.coinbase.com/products/{symbol}-USD/candles?granularity=900"
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "Autotrader-AudaceFi-Research/1.0"})
    try:
        with urlopen(req, timeout=10) as response:
            content = response.read(150_001)
    except (HTTPError, URLError, OSError):
        return []
    if len(content) > 150_000:
        return []
    try:
        candles = json.loads(content)
        if not isinstance(candles, list) or len(candles) > 400:
            return []
        result = {}
        for row in candles:
            if not isinstance(row, list) or len(row) < 6:
                continue
            ts, close, volume = row[0], float(row[4]), float(row[5])
            if (isinstance(ts, int) and not isinstance(ts, bool) and ts % 900 == 0 and
                ts + 900 <= now.timestamp() - 30 and math.isfinite(close) and close > 0 and
                math.isfinite(volume) and volume > 0):
                result[ts] = [ts, close, volume]
        return [result[ts] for ts in sorted(result)][-300:]
    except (TypeError, ValueError, OverflowError):
        return []


def research(symbol: str, now: datetime) -> dict:
    bars = validate_candles(coinbase_closed_bars(symbol, now))
    if not bars:
        return {"symbol": symbol, "state": "NO_COMPLETE_SUPPORTED_USD_HISTORY"}
    latest = bars[-1][1]
    m3 = latest / bars[-13][1] - 1
    m6 = latest / bars[-25][1] - 1
    m24 = latest / bars[-97][1] - 1
    split = len(bars) - 88
    # Fixed strategy comparison for observing other eligible tokens. Does
    # not grant eligibility for live execution in the SOL/ETH-only signer.
    test = {name: _simulate(bars, p, split, len(bars))
            for name, p in PRESETS.items()}
    best = max(test, key=lambda name: (test[name]["net_return"], name))
    return {"symbol": symbol, "state": "OBSERVED",
            "m3Pct": round(100 * m3, 3),
            "m6Pct": round(100 * m6, 3),
            "m24Pct": round(100 * m24, 3),
            "bestShadowProfile": best,
            "holdoutTrades": test[best]["trades"],
            "holdoutNetPct": round(100 * test[best]["net_return"], 4)}


def main() -> int:
    key = os.environ.get("AUDACEFI_API_KEY", "")
    if not key or "\r" in key or "\n" in key:
        print("ADAPTIVE_UNIVERSE_API_KEY_MISSING")
        return 1
    a_status, state = read_route(key, BASE + "/state?category=crypto-bro&mode=real")
    b_status, allowlist = read_route(key, BASE + "/allowlist?category=crypto_bro&mode=real")
    if a_status != 200 or b_status != 200 or not isinstance(state, dict) or not isinstance(allowlist, dict):
        print("ADAPTIVE_UNIVERSE_API_UNAVAILABLE")
        return 1
    season = (state.get("season") or {}).get("active")
    if (not isinstance(season, dict) or season.get("status") != "active" or
        allowlist.get("seasonId") != season.get("id") or
        allowlist.get("allowlistFrozen") is not True or
        not isinstance(allowlist.get("tokens"), list)):
        print("ADAPTIVE_UNIVERSE_SEASON_MISMATCH")
        return 1
    symbols = sorted({token.get("symbol") for token in allowlist["tokens"]
                      if isinstance(token, dict) and token.get("eligible") is True
                      and isinstance(token.get("symbol"), str)
                      and re.fullmatch(r"[A-Z0-9]{2,12}", token["symbol"])})
    if not 1 <= len(symbols) <= LIMIT:
        print("ADAPTIVE_UNIVERSE_COUNT_INVALID")
        return 1
    now = datetime.now(timezone.utc)
    research_symbols = [s for s in symbols if s != "USDC"]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(research, symbol, now): symbol for symbol in research_symbols}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except (ValueError, OSError, RuntimeError):
                results.append({"symbol": futures[future], "state": "DATA_UNAVAILABLE"})
    results.sort(key=lambda row: row["symbol"])
    covered = sum(row["state"] == "OBSERVED" for row in results)
    print("AUDACEFI_ELIGIBLE_TOKEN_COUNT=" + str(len(symbols)))
    print("AUDACEFI_PUBLIC_MARKET_COVERAGE=" + str(covered))
    print("AUDACEFI_RESEARCH_ONLY=true")
    print("AUDACEFI_RESEARCH=" + json.dumps(results, separators=(",", ":")))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## AudaceFi eligible-token shadow research\n\n")
            f.write(f"{covered} of {len(research_symbols)} non-USDC tokens have sufficient completed Coinbase USD candles. No orders placed.\n\n")
            f.write("| Token | Availability | 3h | 6h | 24h | Holdout net (estimated) |\n|---|---|---:|---:|---:|---:|\n")
            for row in results:
                if row["state"] == "OBSERVED":
                    f.write(f'| {row["symbol"]} | observed | {row["m3Pct"]}% | {row["m6Pct"]}% | {row["m24Pct"]}% | {row["holdoutNetPct"]}% |\n')
                else:
                    f.write(f'| {row["symbol"]} | unavailable | — | — | — | — |\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
