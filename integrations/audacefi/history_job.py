"""Offline historical qualification of AudaceFi-approved symbols.

Uses Binance public spot archives, 15-minute candles, verified checksums and
a strict untouched out-of-sample holdout. Reuses the stock AutoTrader's
conservative Bayesian feedback calibration without invoking its stock
broker, NYSE scheduler, equity allocations or paper ledger.
Never sends a trade or changes the live wallet strategy.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import sys
import tempfile
from hashlib import sha256
from urllib.error import HTTPError

from autotrader.config import StrategyConfig
from autotrader.learning import sector_factors
from .adaptive_learning import PRESETS, _simulate, validate_candles
from .history_research import complete_months, load_history, walk_forward
from .live_probe import read_route

HISTORY_CACHE_SCHEMA = "audacefi-walk-forward-v1"


def stock_learning_calibration(bars: list[list], symbol: str) -> float:
    """Reuses the existing AutoTrader's shrinkage calibration as research only.

    This contributes at most one completed chronological training observation;
    slices from the same price path are never counted as independent evidence.
    """
    parsed = validate_candles(bars)
    split = len(parsed) * 3 // 4
    observations = []
    if split > 0:
        baseline = _simulate(parsed, PRESETS["baseline"], 96, split, symbol=symbol)
        if baseline["trades"] > 0:
            observations.append({"sector": symbol,
                                 "excess_return": baseline["net_return"]})
    cfg = StrategyConfig(adaptive_min_total_evaluations=20,
                         adaptive_min_sector_evaluations=12)
    factors = sector_factors({"completed": observations}, cfg)
    factor = factors.get(symbol, 1.0)
    return factor if 0.95 <= factor <= 1.05 else 1.0


def allowlist_snapshot(path: str | Path, *, now: datetime | None = None) -> dict:
    """Read a time-bounded public allowlist snapshot for isolated research hosts."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("HISTORY_SNAPSHOT_CLOCK_INVALID")
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 8192:
            raise ValueError("HISTORY_SNAPSHOT_TOO_LARGE")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("HISTORY_SNAPSHOT_UNREADABLE") from exc
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1 or
            isinstance(value.get("schemaVersion"), bool) or
            value.get("category") != "crypto-bro" or
            value.get("seasonLabel") != "Season 1" or
            value.get("allowlistFrozen") is not True or
            not isinstance(value.get("sourceWorkflowRunId"), int) or
            isinstance(value.get("sourceWorkflowRunId"), bool) or
            value["sourceWorkflowRunId"] <= 0):
        raise RuntimeError("HISTORY_SNAPSHOT_INVALID")
    try:
        verified = datetime.fromisoformat(value["verifiedAtUtc"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(value["expiresAtUtc"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("HISTORY_SNAPSHOT_DATES_INVALID") from exc
    current = now.astimezone(timezone.utc)
    if (verified.tzinfo is None or expires.tzinfo is None or
            verified > current.replace(microsecond=0) + timedelta(minutes=5) or
            expires <= verified or expires - verified > timedelta(days=10)):
        raise RuntimeError("HISTORY_SNAPSHOT_DATES_INVALID")
    if current >= expires:
        raise RuntimeError("HISTORY_ALLOWLIST_SNAPSHOT_EXPIRED")
    symbols = value.get("symbols")
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 24 or
            any(not isinstance(symbol, str) or
                not re.fullmatch(r"[A-Z0-9]{2,12}", symbol) or symbol == "USDC"
                for symbol in symbols) or len(set(symbols)) != len(symbols)):
        raise RuntimeError("HISTORY_SNAPSHOT_SYMBOLS_INVALID")
    return {**value, "symbols": sorted(symbols)}


def eligible_symbols(api_key: str, *, snapshot_path: str | None = None,
                     now: datetime | None = None) -> list[str]:
    if not api_key:
        if not snapshot_path:
            raise RuntimeError("HISTORY_KEY_OR_SNAPSHOT_REQUIRED")
        return allowlist_snapshot(snapshot_path, now=now)["symbols"]
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
        cache_dir = os.environ.get("AUDACEFI_HISTORY_CACHE_DIR")
        quote = "USDT"
        try:
            bars = load_history(symbol, months, quote=quote, cache_dir=cache_dir)
        except Exception as first:
            # A missing or unsupported USDT market is not a trading signal.
            # Only a 404 may trigger a fully reverified USDC spot history;
            # corrupt data, missing checksums or outages fail closed.
            if type(first).__name__ != "HTTPError" or getattr(first, "code", None) != 404:
                raise
            quote = "USDC"
            bars = load_history(symbol, months, quote=quote, cache_dir=cache_dir)
        result_cache = os.environ.get("AUDACEFI_HISTORY_RESULT_CACHE")
        fingerprint_input = json.dumps([HISTORY_CACHE_SCHEMA, symbol, quote, months, bars],
                                       separators=(",", ":"), allow_nan=False).encode()
        fingerprint = sha256(fingerprint_input).hexdigest()
        cache_path = (Path(result_cache) / (symbol + ".json")
                      if result_cache else None)
        if cache_path is not None:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if (isinstance(cached, dict) and cached.get("schema") == HISTORY_CACHE_SCHEMA and
                        cached.get("fingerprint") == fingerprint and
                        isinstance(cached.get("result"), dict)):
                    return {"symbol": symbol, "proxyQuote": quote,
                            "historyBars": len(bars), "cachedEvaluation": True,
                            **cached["result"]}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        outcome = walk_forward(bars, symbol=symbol)
        calibration = stock_learning_calibration(bars, symbol)
        result = {
            "symbol": symbol, "proxyQuote": quote,
            "historyBars": len(bars),
            "bayesianPriorMultiplier": calibration,
            **outcome,
        }
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"schema": HISTORY_CACHE_SCHEMA,
                "fingerprint": fingerprint, "result": result},
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            fd, temporary = tempfile.mkstemp(prefix=".result-", dir=cache_path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, cache_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return result
    except Exception as exc:
        # Public archive presence varies by market. Do not fabricate candles.
        if type(exc).__name__ not in {
            "HTTPError", "URLError", "TimeoutError", "ConnectionError",
            "ValueError", "BadZipFile", "OSError", "UnicodeDecodeError",
            "IncompleteRead", "RemoteDisconnected", "EOFError",
        }:
            raise
        if isinstance(exc, HTTPError) and 100 <= exc.code <= 599:
            reason = f"HTTP_{exc.code}"
        elif isinstance(exc, ValueError) and re.fullmatch(
                r"[A-Z0-9_]{1,64}", str(exc)):
            reason = str(exc)
        else:
            reason = type(exc).__name__
        return {"symbol": symbol, "status": "HISTORY_UNAVAILABLE",
                "reason": reason}


def main() -> int:
    key = os.environ.get("AUDACEFI_API_KEY", "")
    snapshot_path = os.environ.get("AUDACEFI_ALLOWLIST_SNAPSHOT", "")
    if key and (len(key) > 512 or "\r" in key or "\n" in key):
        print("AUDACEFI_HISTORY=KEY_INVALID")
        return 1
    if not key and not snapshot_path:
        print("AUDACEFI_HISTORY=KEY_OR_SNAPSHOT_REQUIRED")
        return 1
    try:
        snapshot = allowlist_snapshot(snapshot_path) if not key else None
    except RuntimeError as exc:
        if str(exc) == "HISTORY_ALLOWLIST_SNAPSHOT_EXPIRED":
            print("AUDACEFI_HISTORY=ALLOWLIST_SNAPSHOT_EXPIRED")
            return 0
        raise
    symbols = (snapshot["symbols"] if snapshot else eligible_symbols(key))
    allowlist_source = "VERIFIED_FROZEN_SNAPSHOT" if snapshot else "LIVE_API"
    months = complete_months(datetime.now(timezone.utc), count=12)
    print("AUDACEFI_HISTORY_MONTHS=" + ",".join(months))
    print("AUDACEFI_HISTORY_ELIGIBLE_SYMBOL_COUNT=" + str(len(symbols)))
    print("AUDACEFI_HISTORY_ALLOWLIST_SOURCE=" + allowlist_source)
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(research_one, symbol, months): symbol
                   for symbol in symbols}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["symbol"])
    checked = sum(row.get("historyBars", 0) >= 30_000 for row in results)
    qualified = sum(row.get("status") == "VALIDATED_HISTORICAL_CANDIDATE"
                    for row in results)
    outcome = {
        "schemaVersion": 1, "basis": "binance_verified_spot_monthly_15m_usdt_or_usdc_proxy",
        "readOnly": True, "liveExecutionAuthorized": False,
        "months": months, "eligibleSymbols": len(symbols),
        "allowlistSource": allowlist_source,
        "allowlistVerifiedAtUtc": snapshot.get("verifiedAtUtc") if snapshot else None,
        "allowlistExpiresAtUtc": snapshot.get("expiresAtUtc") if snapshot else None,
        "historicalCoverage": checked, "validatedCandidates": qualified,
        "stockCalibrationMethod": "autotrader.learning.sector_factors_single_training_curve",
        "results": results,
    }
    report_path = Path(os.environ.get(
        "AUDACEFI_HISTORY_REPORT", "audacefi-historical-qualification.json"))
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(outcome, f, indent=2, sort_keys=True, allow_nan=False)
        f.write("\n")
    print("AUDACEFI_HISTORY_COVERED=" + str(checked))
    print("AUDACEFI_HISTORY_VALIDATED_CANDIDATES=" + str(qualified))
    print("AUDACEFI_HISTORY_REPORT=" + str(report_path))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## AudaceFi — historical 15m strategy qualification\n\n")
            f.write(f"Official allowlist: {len(symbols)} assets. Twelve completed months: {', '.join(months)}.\n\n")
            f.write(f"{checked} supported histories, {qualified} independently validated candidate(s).\n\n")
            f.write("| Token | Status | Candidate | Bars | Baseline OOS net | Candidate OOS net | Baseline swaps | Candidate swaps | Baseline costs USDC | Candidate costs USDC | Candidate max DD | Intraday DD | 5d DD | Decision |\n")
            f.write("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
            for item in results:
                comparison = item.get("comparison", {})
                base = comparison.get("baseline", {}).get("holdout", {})
                candidate = comparison.get(item.get("profile"), {}).get("holdout", {})
                f.write(f"| {item['symbol']} | {item['status']} | {item.get('profile', 'baseline')} | {item.get('historyBars', 0)} | "
                        f"{100 * base.get('net_return', 0):.2f}% | "
                        f"{100 * candidate.get('net_return', 0):.2f}% | "
                        f"{base.get('swaps', 0)} | "
                        f"{candidate.get('swaps', 0)} | "
                        f"{base.get('estimated_costs_usdc', 0):.2f} | "
                        f"{candidate.get('estimated_costs_usdc', 0):.2f} | "
                        f"{100 * candidate.get('max_drawdown', 0):.2f}% | "
                        f"{100 * candidate.get('max_intraday_drawdown', 0):.2f}% | "
                        f"{100 * candidate.get('max_competition_drawdown', 0):.2f}% | "
                        f"{item.get('decisionReason', item.get('reason', 'n/a'))} |\n")
            f.write("\nEquity/max drawdown use a continuous curve per chronological partition; modeled costs are estimates, not observed AudaceFi fills. Historical results do not promise live gains.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
