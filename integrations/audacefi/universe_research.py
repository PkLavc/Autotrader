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
from .adaptive_learning import PRESETS, policy_for, validate_candles
from .intraday_signal import signal_from_rows
from .league_strategy import LeaguePolicy, decide

BASE = "/api/v1/me"
LIMIT = 30
BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def valid_mint(value: object) -> bool:
    if not isinstance(value, str) or not 32 <= len(value) <= 44 or any(c not in BASE58 for c in value):
        return False
    number = 0
    for char in value:
        number = number * 58 + "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz".index(char)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    decoded = b"\x00" * (len(value) - len(value.lstrip("1"))) + decoded
    return len(decoded) == 32


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


def binance_closed_bars(symbol: str, now: datetime) -> list:
    if not re.fullmatch(r"[A-Z0-9]{2,12}", symbol) or symbol == "USDC":
        return []
    url = ("https://api.binance.com/api/v3/klines?symbol=" + symbol +
           "USDT&interval=15m&limit=300")
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "Autotrader-AudaceFi-Research/1.0"})
    try:
        with urlopen(req, timeout=10) as response:
            content = response.read(300_001)
        if len(content) > 300_000:
            return []
        rows = json.loads(content)
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, list) or len(row) < 6:
                continue
            ts, close, volume = int(row[0]) // 1000, float(row[4]), float(row[5])
            if (ts % 900 == 0 and ts + 900 <= now.timestamp() - 30 and
                    math.isfinite(close) and close > 0 and
                    math.isfinite(volume) and volume > 0):
                result.append([ts, close, volume])
        return result[-300:]
    except (HTTPError, URLError, OSError, ValueError, TypeError, OverflowError):
        return []


def audacefi_price(key: str, mint: str) -> float | None:
    status, market = read_route(key, "/api/v1/market/prices?mints=" + mint)
    value = market.get("prices", {}).get(mint) if status == 200 and isinstance(market, dict) else None
    try:
        price = float(value.get("priceUsd")) if isinstance(value, dict) and value.get("status") == "priced" else math.nan
        age = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(
            market["asOf"].replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return price if math.isfinite(price) and price > 0 and 0 <= age <= 900 else None


def research(token: dict, now: datetime, key: str, bench: dict, season: dict) -> dict:
    symbol = token.get("symbol")
    mint = token.get("mint")
    if not valid_mint(mint):
        return {"symbol": symbol, "state": "ALLOWLIST_MINT_INVALID"}
    decimals = token.get("decimals")
    if not isinstance(decimals, int) or isinstance(decimals, bool):
        return {"symbol": symbol, "state": "ALLOWLIST_DECIMALS_INVALID"}
    mappings = [row for row in bench.get("mints", []) if isinstance(row, dict) and
                row.get("mintMainnet") == mint and row.get("decimals") == decimals and
                valid_mint(row.get("mintDevnet"))]
    if len(mappings) != 1:
        return {"symbol": symbol, "state": "DEVNET_MINT_MAPPING_INVALID"}
    coinbase = validate_candles(coinbase_closed_bars(symbol, now))
    binance = validate_candles(binance_closed_bars(symbol, now))
    price_audacefi = audacefi_price(key, mint)
    if not coinbase or not binance or price_audacefi is None:
        missing = []
        if not coinbase: missing.append("COINBASE")
        if not binance: missing.append("BINANCE")
        if price_audacefi is None: missing.append("AUDACEFI_PRICE")
        return {"symbol": symbol, "state": "MULTI_SOURCE_COVERAGE_INCOMPLETE",
                "missingSources": missing, "routeState": "NOT_TESTED_WITHOUT_CREATING_INTENT"}
    close_cb, close_bn = coinbase[-1][1], binance[-1][1]
    if (now.timestamp() - (coinbase[-1][0] + 900) > 1800 or
            now.timestamp() - (binance[-1][0] + 900) > 1800 or
            abs(coinbase[-1][0] - binance[-1][0]) > 900):
        return {"symbol": symbol, "state": "MULTI_SOURCE_CANDLES_NOT_SYNCHRONIZED",
                "routeState": "NOT_TESTED_WITHOUT_CREATING_INTENT"}
    if (abs(close_cb / close_bn - 1) > .01 or
            abs(close_cb / price_audacefi - 1) > .01):
        return {"symbol": symbol, "state": "CROSS_SOURCE_PRICE_MISMATCH",
                "routeState": "NOT_TESTED_WITHOUT_CREATING_INTENT"}
    bars = coinbase
    latest = bars[-1][1]
    m3 = latest / bars[-13][1] - 1
    m6 = latest / bars[-25][1] - 1
    m24 = latest / bars[-97][1] - 1
    signal = signal_from_rows(symbol, bars, len(bars) - 1)
    if signal is None:
        return {"symbol": symbol, "state": "CURRENT_SIGNAL_INVALID"}
    context = {"verified": True, "category": "crypto-bro",
        "restrictions_verified": True, "wallet_ready": True,
        "network": "solana-devnet", "season_status": "active",
        "start_utc": season["startsAt"], "end_utc": season["endsAt"],
        "eligible_symbols": [symbol], "max_position_weight": .35,
        "max_total_exposure": .35, "start_snapshot_confirmed": True}
    current_candidates = []
    base_policy = LeaguePolicy(max_position_weight=.35, max_total_exposure=.35,
        maximum_season_drawdown=.018, risk_budget_per_trade=.012,
        stop_distance_min=.025, stop_distance_max=.035,
        closing_buffer_hours=16, new_entry_cutoff_hours=24,
        maximum_signal_age_hours=.5, round_trip_cost_bps=30, minimum_net_edge=.004)
    for name in PRESETS:
        result = decide(context, {"equity_usdc": 10_000, "cash_usdc": 10_000,
            "peak_equity_usdc": 10_000, "positions": {}},
            {"asset_class": "crypto", "model": "coinbase_completed_15m",
             "generated_at": now.isoformat(), "signals": [signal]}, now=now,
            policy=policy_for(name, base_policy))
        if result.get("status") == "PROPOSED":
            current_candidates.append(name)
    return {"symbol": symbol, "state": "OBSERVED",
            "sourcesValidated": ["COINBASE", "BINANCE", "AUDACEFI_MARK_PRICE"],
            "mintState": "ALLOWLIST_AND_DEVNET_BENCH_MATCH",
            "crossSourceSpreadPct": round(100 * max(abs(close_cb / close_bn - 1),
                                                   abs(close_cb / price_audacefi - 1)), 4),
            "routeState": "NOT_TESTED_WITHOUT_CREATING_INTENT",
            "m3Pct": round(100 * m3, 3),
            "m6Pct": round(100 * m6, 3),
            "m24Pct": round(100 * m24, 3),
            "medianDollarVolumeUsd": round(signal["median_15m_dollar_volume_usd"], 2),
            "currentEntryCandidates": current_candidates,
            "historyPerformance": "NOT_EVALUATED_BY_SHORT_LIVE_SCAN"}


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
        not isinstance(season.get("startsAt"), str) or not isinstance(season.get("endsAt"), str) or
        allowlist.get("seasonId") != season.get("id") or
        allowlist.get("allowlistFrozen") is not True or
        not isinstance(allowlist.get("tokens"), list)):
        print("ADAPTIVE_UNIVERSE_SEASON_MISMATCH")
        return 1
    bench_status, bench = read_route(key, "/api/v1/devnet/bench")
    if (bench_status != 200 or not isinstance(bench, dict) or
            bench.get("cluster") != "devnet" or not isinstance(bench.get("mints"), list) or
            len(bench["mints"]) > 2048):
        print("ADAPTIVE_UNIVERSE_DEVNET_BENCH_INVALID")
        return 1
    tokens = [token for token in allowlist["tokens"]
              if isinstance(token, dict) and token.get("eligible") is True
              and isinstance(token.get("symbol"), str)
              and re.fullmatch(r"[A-Z0-9]{2,12}", token["symbol"])]
    symbols = sorted({token["symbol"] for token in tokens})
    if len(tokens) != len(symbols):
        print("ADAPTIVE_UNIVERSE_DUPLICATE_SYMBOLS")
        return 1
    if not 1 <= len(symbols) <= LIMIT:
        print("ADAPTIVE_UNIVERSE_COUNT_INVALID")
        return 1
    now = datetime.now(timezone.utc)
    research_tokens = [token for token in tokens if token["symbol"] != "USDC"]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(research, token, now, key, bench, season): token["symbol"]
                   for token in research_tokens}
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
            f.write(f"{covered} of {len(research_tokens)} non-USDC tokens passed Coinbase, Binance and AudaceFi price cross-checks. Routes were not probed by creating intents; no orders placed.\n\n")
            f.write("| Token | Availability | 3h | 6h | 24h | Median 15m liquidity USD | Current shadow candidates |\n|---|---|---:|---:|---:|---:|---|\n")
            for row in results:
                if row["state"] == "OBSERVED":
                    f.write(f'| {row["symbol"]} | observed | {row["m3Pct"]}% | {row["m6Pct"]}% | {row["m24Pct"]}% | {row["medianDollarVolumeUsd"]:.2f} | {", ".join(row["currentEntryCandidates"]) or "none"} |\n')
                else:
                    f.write(f'| {row["symbol"]} | {row["state"]} | - | - | - | - | - |\n')
            f.write("\nShort live candles inventory current signals only; they do not establish historical edge. Routes remain untested until a buy and sell are confirmed on Devnet.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
