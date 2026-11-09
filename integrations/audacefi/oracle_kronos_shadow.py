"""Daily Oracle-only Kronos shadow forecasts and matured outcome tracking."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Callable

import pandas as pd

from .crypto_research import load_crypto_histories, run_forecasts

HORIZON_DAYS = 5
MAX_ROWS = 1100


def _prices(histories: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for symbol, history in histories.items():
        if symbol not in {"WBTC", "ETH", "SOL"} or "close" not in history:
            continue
        rows = {}
        for timestamp, raw in history["close"].items():
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                continue
            rows[pd.Timestamp(timestamp).date().isoformat()] = value
        result[symbol] = rows
    return result


def _evaluate(rows: list[dict], prices: dict[str, dict[str, float]], *, today: date) -> int:
    resolved = 0
    for row in rows:
        if row.get("actual_return_5d") is not None:
            continue
        try:
            start = date.fromisoformat(row["forecast_date"])
            end = start + timedelta(days=HORIZON_DAYS)
            if today < end:
                continue
            market = prices.get(row["symbol"], {})
            expected_dates = [(start + timedelta(days=offset)).isoformat()
                              for offset in range(HORIZON_DAYS + 1)]
            if not all(key in market for key in expected_dates):
                continue
            actual = market[expected_dates[-1]] / market[expected_dates[0]] - 1
            row["actual_return_5d"] = round(actual, 8)
            row["kronos_absolute_error"] = round(abs(actual - row["kronos_return"]), 8)
            row["momentum_absolute_error"] = round(abs(actual - row["momentum_5d_return"]), 8)
            row["kronos_beat_momentum"] = (
                row["kronos_absolute_error"] < row["momentum_absolute_error"])
            resolved += 1
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return resolved


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".kronos-shadow-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run_shadow(*, now: datetime | None = None,
               state_path: str | Path | None = None,
               history_loader: Callable = load_crypto_histories,
               forecast_runner: Callable = run_forecasts) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("KRONOS_SHADOW_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    state = Path(state_path or os.environ.get(
        "AUDACEFI_KRONOS_SHADOW_FILE",
        "/var/lib/autotrader/audacefi/kronos-shadow.json"))
    histories = history_loader(now)
    forecast = forecast_runner(now, histories=histories)
    if forecast.get("model") != "NeoQuasar/Kronos-base" or not isinstance(
            forecast.get("signals"), list):
        raise RuntimeError("KRONOS_SHADOW_FORECAST_INVALID")
    try:
        raw_state = state.read_bytes()
        if len(raw_state) > 2_000_000:
            raise RuntimeError("KRONOS_SHADOW_STATE_TOO_LARGE")
        old = json.loads(raw_state)
    except FileNotFoundError:
        old = {"schemaVersion": 1, "predictions": []}
    if (not isinstance(old, dict) or old.get("schemaVersion") != 1 or
            not isinstance(old.get("predictions"), list) or
            len(old["predictions"]) > MAX_ROWS):
        raise RuntimeError("KRONOS_SHADOW_STATE_INVALID")
    rows = old["predictions"]
    today = now.date()
    prices = _prices(histories)
    matured = _evaluate(rows, prices, today=today)
    forecast_dates = {(row.get("symbol"), row.get("forecast_date")) for row in rows}
    added = 0
    for signal in forecast["signals"]:
        symbol = signal.get("symbol")
        forecast_date = signal.get("last_completed_candle_utc")
        current = signal.get("current_close_usd")
        kronos_return = signal.get("expected_return")
        momentum = signal.get("baseline_momentum_5d")
        latest = max(prices.get(symbol, {}), default=None)
        if (symbol not in {"WBTC", "ETH", "SOL"} or
                not isinstance(forecast_date, str) or forecast_date != latest or
                (symbol, forecast_date) in forecast_dates or
                not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value)
                        for value in (current, kronos_return, momentum)) or current <= 0):
            continue
        rows.append({
            "symbol": symbol,
            "forecast_date": forecast_date,
            "horizon_days": HORIZON_DAYS,
            "current_close_usd": current,
            "kronos_return": kronos_return,
            "momentum_5d_return": momentum,
            "actual_return_5d": None,
            "model_revision": forecast.get("model_revision"),
            "tokenizer_revision": forecast.get("tokenizer_revision"),
            "model_source_revision": forecast.get("model_source_revision"),
        })
        forecast_dates.add((symbol, forecast_date))
        added += 1
    rows[:] = rows[-MAX_ROWS:]
    completed = [row for row in rows if row.get("actual_return_5d") is not None]
    payload = {
        "schemaVersion": 1,
        "lastRunUtc": now.isoformat(),
        "model": forecast["model"],
        "modelRevision": forecast.get("model_revision"),
        "tokenizerRevision": forecast.get("tokenizer_revision"),
        "modelSourceRevision": forecast.get("model_source_revision"),
        "dataSource": "Yahoo Finance completed daily candles; WBTC uses BTC-USD proxy",
        "executionAuthorized": False,
        "predictions": rows,
        "evaluation": {
            "maturedAssetPredictions": len(completed),
            "kronosMeanAbsoluteError": round(sum(
                row["kronos_absolute_error"] for row in completed) / len(completed), 8)
                if completed else None,
            "momentumMeanAbsoluteError": round(sum(
                row["momentum_absolute_error"] for row in completed) / len(completed), 8)
                if completed else None,
            "kronosBeatMomentumCount": sum(
                row["kronos_beat_momentum"] for row in completed),
        },
    }
    _save(state, payload)
    result = {"status": "RECORDED", "newPredictions": added,
              "newlyMatured": matured,
              "maturedAssetPredictions": len(completed),
              "executionAuthorized": False}
    print("AUDACEFI_KRONOS_SHADOW=" + json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    run_shadow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
