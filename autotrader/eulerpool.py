"""Read-only Eulerpool daily close verification for the paper-trading research report.

Eulerpool's currently observed equity/candles response exposes roughly one year of
timestamped OHLC prices, without volume or an adjusted-close field. It must not
replace the 400-session adjusted OHLCV feed used by Kronos until parity is proven.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pandas import DataFrame


API_BASE = "https://api.eulerpool.com/api/1"


def _date(value: object) -> str:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        stamp = float(value)
        if stamp > 10 ** 11:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()
    return str(value)[:10]


def normalize_candles(payload: object) -> dict[str, dict[str, float]]:
    """Return date-keyed OHLC, accepting the observed list and documented wrappers."""
    if isinstance(payload, dict):
        for name in ("data", "candles", "prices", "results", "history"):
            if name in payload:
                return normalize_candles(payload[name])
        raise ValueError("Eulerpool response is not a candle array")
    if not isinstance(payload, list):
        raise ValueError("Eulerpool response is not a candle array")
    dates: dict[str, dict[str, float]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        when = next((row[k] for k in ("timestamp", "date", "datetime", "time") if row.get(k) is not None), None)
        if when is None:
            continue
        try:
            parsed = {field: float(row[field]) for field in ("open", "high", "low", "close")}
            if all(value > 0 for value in parsed.values()):
                dates[_date(when)] = parsed
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    if not dates:
        raise ValueError("Eulerpool returned no usable OHLC dates")
    return dates


def fetch_candles(symbol: str, api_key: str) -> dict[str, dict[str, float]]:
    path = f"/equity/candles/{quote(symbol, safe='')}"
    # API officially accepts bearer auth and token query; never log this URL.
    url = API_BASE + path + "?" + urlencode({"token": api_key})
    req = Request(url, headers={"Authorization": "Bearer " + api_key,
                                "Accept": "application/json",
                                "User-Agent": "PkLavc-Autotrader/1.0"})
    try:
        with urlopen(req, timeout=12) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Eulerpool HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Eulerpool connection unavailable") from None
    return normalize_candles(data)


def audit_target_prices(targets: list, histories: dict[str, DataFrame], market_date: str) -> dict:
    """Verify targets' closing prices without altering signals, trades, or state."""
    key = os.environ.get("EULERPOOL_API_KEY", "").strip()
    if not key:
        return {"status": "disabled_missing_secret", "mode": "verification_only", "results": []}
    rows: list[dict] = []
    for target in targets[:5]:
        symbol = target.symbol
        frame = histories.get(symbol)
        if frame is None or frame.empty:
            rows.append({"symbol": symbol, "status": "missing_yahoo_history"})
            continue
        yahoo_close = float(frame["close"].iloc[-1])
        try:
            prices = fetch_candles(symbol, key)
            if market_date not in prices:
                rows.append({"symbol": symbol, "status": "missing_market_session",
                             "last_eulerpool_session": max(prices)})
                continue
            euler_close = prices[market_date]["close"]
            difference_pct = round(abs(euler_close / yahoo_close - 1.0) * 100.0, 4)
            rows.append({"symbol": symbol,
                         "status": "verified" if difference_pct <= 2.0 else "divergent",
                         "market_session": market_date,
                         "yahoo_close": round(yahoo_close, 4),
                         "eulerpool_close": round(euler_close, 4),
                         "difference_pct": difference_pct})
        except (RuntimeError, ValueError, TypeError) as exc:
            # Controlled, sanitized messages only: no URLs, response bodies or keys.
            message = str(exc)
            if not (message.startswith("Eulerpool HTTP ") or
                    message in ("Eulerpool connection unavailable",
                                "Eulerpool response is not a candle array",
                                "Eulerpool returned no usable OHLC dates")):
                message = "Eulerpool response invalid"
            rows.append({"symbol": symbol, "status": "unavailable", "reason": message})
    verified = sum(row["status"] == "verified" for row in rows)
    return {"status": "active" if verified else "no_verified_prices",
            "mode": "verification_only",
            "checked": len(rows), "verified": verified, "results": rows}
