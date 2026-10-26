"""Read-only Eulerpool vs Yahoo Finance comparison for the Autotrader universe.

This script never calls a broker and never changes the research ledger. It compares
raw daily OHLCV and dividend/split-adjusted close separately to avoid treating
unadjusted prices as equivalent to the Kronos input.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import statistics
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from autotrader.config import StrategyConfig


ROUTES = (
    "https://api.eulerpool.com/api/1/equities/{symbol}/history",
    "https://api.eulerpool.com/v1/equities/{symbol}/history",
)
FIELDS = ("open", "high", "low", "close", "volume")
ADJUSTED_KEYS = ("adjustedClose", "adjClose", "adj_close", "adjusted_close", "adj close")


def _rows(payload: object) -> list[dict]:
    """Extract arrays without assuming a single undocumented response wrapper."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "prices", "history", "results", "items"):
            if key in payload:
                return _rows(payload[key])
    raise ValueError("Response has no recognizable history array")


def _date(value: object) -> str:
    if isinstance(value, (float, int)):
        stamp = float(value)
        return datetime.fromtimestamp(stamp / 1000 if stamp > 10**11 else stamp,
                                      tz=ZoneInfo("UTC")).date().isoformat()
    return str(value)[:10]


def normalize_eulerpool(payload: object, end_date: str) -> pd.DataFrame:
    records = []
    for row in _rows(payload):
        try:
            date_value = next((row[key] for key in ("date", "datetime", "timestamp", "time")
                               if row.get(key) is not None), None)
            if date_value is None:
                continue
            day = _date(date_value)
            if day > end_date:
                continue
            normalized = {"date": day}
            for field in FIELDS:
                value = row.get(field, row.get(field.title()))
                normalized[field] = float(value) if value is not None else float("nan")
            adjusted = next((row.get(k) for k in ADJUSTED_KEYS if row.get(k) is not None), None)
            normalized["adj_close"] = float(adjusted) if adjusted is not None else float("nan")
            records.append(normalized)
        except (TypeError, ValueError, OverflowError):
            continue
    if not records:
        raise ValueError("Eulerpool returned no usable daily OHLCV rows")
    df = pd.DataFrame(records).drop_duplicates("date", keep="last").set_index("date").sort_index()
    df = df.dropna(subset=list(FIELDS))
    if df.empty:
        raise ValueError("Eulerpool returned no complete daily OHLCV rows")
    return df


def fetch_eulerpool(symbol: str, key: str, start: str, end: str) -> tuple[pd.DataFrame, str]:
    query = urlencode({"from": start, "to": end, "interval": "1d"})
    failures = []
    for route in ROUTES:
        endpoint = route.format(symbol=symbol)
        request = Request(
            endpoint + "?" + query,
            headers={"Authorization": "Bearer " + key,
                     "Accept": "application/json", "User-Agent": "PkLavc-Autotrader-validation/1.0"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            return normalize_eulerpool(payload, end), endpoint
        except HTTPError as exc:
            # The marketing pages document two distinct route prefixes.
            if exc.code in (404, 405):
                failures.append(f"{route.split('.com/')[-1]} HTTP {exc.code}")
                continue
            raise RuntimeError(f"Eulerpool HTTP {exc.code} for {symbol}") from None
        except (ValueError, json.JSONDecodeError) as exc:
            failures.append(f"{route.split('.com/')[-1]} unexpected response ({type(exc).__name__})")
        except URLError as exc:
            raise RuntimeError(f"Eulerpool connection failed: {type(exc.reason).__name__}") from None
    raise RuntimeError("; ".join(failures) or "No working Eulerpool history route")


def fetch_yahoo(symbol: str, end: str) -> pd.DataFrame:
    df = yf.download(
        symbol, period="3y", interval="1d", auto_adjust=False,
        actions=True, progress=False, threads=False, timeout=30,
    )
    if df.empty:
        raise ValueError("Yahoo returned an empty history")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    if any(c not in df.columns for c in FIELDS):
        raise ValueError("Yahoo is missing required OHLCV fields")
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df = df[df.index <= end]
    return df.dropna(subset=list(FIELDS)).sort_index()


def _median_p95(values: pd.Series) -> tuple[float | None, float | None]:
    clean = pd.to_numeric(values, errors="coerce").replace(
        [float("inf"), float("-inf")], float("nan")
    ).dropna()
    if clean.empty:
        return None, None
    return round(float(clean.median()) * 100, 4), round(float(clean.quantile(0.95)) * 100, 4)


def compare(symbol: str, yahoo: pd.DataFrame, euler: pd.DataFrame,
            lookback: int, endpoint: str) -> dict:
    reference = yahoo.tail(lookback)
    common = reference.index.intersection(euler.index).sort_values()
    missing = sorted(set(reference.index) - set(euler.index))
    result = {
        "symbol": symbol, "source_route": endpoint.split(".com/")[-1],
        "yahoo_rows": len(reference), "eulerpool_rows": len(euler),
        "overlap": len(common), "missing_sessions": len(missing),
        "last_yahoo_session": str(reference.index[-1]),
        "last_eulerpool_session": str(euler.index[-1]),
        "median_raw_close_error_pct": None, "p95_raw_close_error_pct": None,
        "median_adj_close_error_pct": None, "p95_adj_close_error_pct": None,
        "adj_close_available": False,
        "status": "INCOMPLETE",
    }
    if common.empty:
        return result
    raw = (euler.loc[common, "close"] / reference.loc[common, "close"] - 1).abs()
    result["median_raw_close_error_pct"], result["p95_raw_close_error_pct"] = _median_p95(raw)
    if "adj_close" in reference.columns:
        adj = euler.loc[common, "adj_close"]
        valid = adj.notna() & reference.loc[common, "adj_close"].notna()
        if valid.any():
            result["adj_close_available"] = True
            diffs = (adj[valid] / reference.loc[common, "adj_close"][valid] - 1).abs()
            result["median_adj_close_error_pct"], result["p95_adj_close_error_pct"] = _median_p95(diffs)
    enough = (len(common) >= min(lookback, 400) - 5
              and len(missing) <= 5
              and result["last_yahoo_session"] == result["last_eulerpool_session"])
    price_ok = (result["median_raw_close_error_pct"] is not None
                and result["median_raw_close_error_pct"] <= 0.5
                and result["p95_raw_close_error_pct"] <= 2)
    adj_ok = (result["adj_close_available"]
              and result["median_adj_close_error_pct"] is not None
              and result["median_adj_close_error_pct"] <= 0.5
              and result["p95_adj_close_error_pct"] <= 2)
    result["status"] = ("CANDIDATE" if enough and price_ok and adj_ok
                        else "DIVERGENT" if enough and not (price_ok and adj_ok)
                        else "INCOMPLETE")
    return result


def report_markdown(rows: list[dict], date_limit: str) -> str:
    lines = [
        "## Eulerpool vs Yahoo — read-only validation",
        "",
        f"Through NY market date: {date_limit}. No orders were submitted.",
        "",
        "| Symbol | Status | Common sessions | Missing | Raw close median error | Adjusted close median error |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        def fmt(value: object) -> str:
            return "—" if value is None else f"{value}%"
        if row["status"] == "ERROR":
            lines.append(f"| {row['symbol']} | ERROR | — | — | — | — |")
        else:
            lines.append(f"| {row['symbol']} | {row['status']} | {row['overlap']} | "
                         f"{row['missing_sessions']} | {fmt(row['median_raw_close_error_pct'])} | "
                         f"{fmt(row['median_adj_close_error_pct'])} |")
    lines += [
        "",
        "CANDIDATE means coverage and preliminary price checks passed, **not** approval for trading.",
        "Corporate actions (dividends/splits), adjusted OHLCV and walk-forward forecasts still require separate validation.",
        "ERROR details are in the downloadable JSON report; no API keys are written.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(StrategyConfig().symbols))
    parser.add_argument("--output", default="reports/eulerpool-comparison.json")
    args = parser.parse_args()
    key = os.environ.get("EULERPOOL_API_KEY", "").strip()
    if not key:
        raise SystemExit("Missing EULERPOOL_API_KEY GitHub Actions secret")
    # Avoid partial same-day candles, especially during US trading hours.
    last_completed = (datetime.now(ZoneInfo("America/New_York")).date() -
                      timedelta(days=1)).isoformat()
    start = (datetime.fromisoformat(last_completed).date() -
             timedelta(days=3 * 366)).isoformat()
    config = StrategyConfig()
    rows = []
    for symbol in dict.fromkeys(args.symbols):
        print(f"Comparing {symbol}...", flush=True)
        try:
            euler, endpoint = fetch_eulerpool(symbol, key, start, last_completed)
            yahoo = fetch_yahoo(symbol, last_completed)
            rows.append(compare(symbol, yahoo, euler, config.lookback, endpoint))
        except Exception as exc:
            # Never write or echo request headers / keys.
            rows.append({"symbol": symbol, "status": "ERROR",
                         "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "date_limit": last_completed, "lookback": config.lookback,
        "results": rows, "production_modified": False,
    }, indent=2) + "\n", encoding="utf-8")
    summary = report_markdown(rows, last_completed)
    Path(str(output) + ".md").write_text(summary, encoding="utf-8")
    print(summary)
    return 1 if any(row["status"] == "ERROR" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
