"""AudaceFi read-only historical strategy qualification from official Binance spot archives.

Downloads up to twelve completed calendar months of 15m SPOT candles, validates
archive hashes and chronological integrity, and simulates non-overlapping
walk-forward windows. It never signs, trades, changes the active strategy,
or reads wallet material. Public USDT spot prices are only proxy observations,
NOT the AudaceFi chain oracle.
"""
from __future__ import annotations

import calendar
import csv
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO, TextIOWrapper
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile, BadZipFile

from .adaptive_learning import (MAX_ROLLING_SWAPS, PRESETS, _simulate,
                                validate_candles)

ORIGIN = "https://data.binance.vision"
MAX_ZIP = 5_000_000
MAX_CSV = 15_000_000
WINDOW = 5 * 24 * 4
MIN_TRAIN_TRADES = 10
MIN_TEST_TRADES = 6
ROUND_TRIP_FEE_ESTIMATE_BPS = 30


def complete_months(now: datetime, count: int = 12) -> list[str]:
    if now.tzinfo is None or count < 1 or count > 12:
        raise ValueError("INVALID_MONTH_REQUEST")
    value = now.astimezone(timezone.utc)
    year, month = value.year, value.month
    months = []
    for _ in range(count):
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
        months.append(f"{year:04d}-{month:02d}")
    return list(reversed(months))


def archive_url(symbol: str, month: str, quote: str = "USDT") -> str:
    if not re.fullmatch(r"[A-Z0-9]{2,12}", symbol):
        raise ValueError("INVALID_SYMBOL")
    if not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", month):
        raise ValueError("INVALID_MONTH")
    if quote not in {"USDT", "USDC"}:
        raise ValueError("UNSUPPORTED_QUOTE_ASSET")
    market = symbol + quote
    return (f"{ORIGIN}/data/spot/monthly/klines/{market}/15m/"
            f"{market}-15m-{month}.zip")


def download(url: str, max_bytes: int) -> bytes:
    if not url.startswith(ORIGIN + "/data/spot/monthly/klines/"):
        raise ValueError("UNTRUSTED_ARCHIVE_ORIGIN")
    req = Request(url, headers={"User-Agent": "AudaceFi-Historical-Research/1.0"})
    with urlopen(req, timeout=18) as reply:
        value = reply.read(max_bytes + 1)
    if len(value) > max_bytes:
        raise ValueError("ARCHIVE_TOO_LARGE")
    return value


def parse_archive(data: bytes, expected_hash: str) -> list[list]:
    if not re.fullmatch("[0-9a-fA-F]{64}", expected_hash):
        raise ValueError("ARCHIVE_HASH_MISSING")
    if sha256(data).hexdigest() != expected_hash.lower():
        raise ValueError("ARCHIVE_HASH_MISMATCH")
    with ZipFile(BytesIO(data)) as z:
        entries = [entry for entry in z.infolist() if not entry.is_dir()]
        if len(entries) != 1 or not entries[0].filename.endswith(".csv") or (
            entries[0].file_size > MAX_CSV or entries[0].file_size <= 0
        ):
            raise ValueError("ARCHIVE_CONTENTS_INVALID")
        with z.open(entries[0]) as file:
            text = TextIOWrapper(file, encoding="utf-8", newline="")
            rows = []
            for row in csv.reader(text):
                if len(row) < 6:
                    raise ValueError("BAD_CANDLE_SCHEMA")
                try:
                    raw_time = int(row[0])
                    # Since January 2025 Binance writes microsecond timestamps.
                    ts = raw_time // (1_000_000 if raw_time >= 10**15 else 1000)
                    close, volume = float(row[4]), float(row[5])
                except ValueError:
                    if not rows and row[0].lower().startswith(("open", "timestamp")):
                        continue
                    raise
                if not (ts % 900 == 0 and math.isfinite(close) and close > 0 and
                        math.isfinite(volume) and volume > 0):
                    raise ValueError("BAD_CANDLE_VALUES")
                rows.append([ts, close, volume])
                if len(rows) > 3100:
                    raise ValueError("CANDLE_COUNT_TOO_LARGE")
    if any(b[0] - a[0] != 900 for a, b in zip(rows, rows[1:])):
        raise ValueError("NON_CONTIGUOUS_ARCHIVE")
    return rows


def load_history(symbol: str, months: list[str], loader=download,
                 quote: str = "USDT") -> list[list]:
    all_rows = []
    for month in months:
        url = archive_url(symbol, month, quote)
        checksum = loader(url + ".CHECKSUM", 512).decode("ascii").strip().split()
        if not checksum:
            raise ValueError("ARCHIVE_CHECKSUM_UNAVAILABLE")
        data = loader(url, MAX_ZIP)
        rows = parse_archive(data, checksum[0])
        if not rows:
            raise ValueError("ARCHIVE_EMPTY")
        # Missing bars at monthly boundaries signal incomplete history.
        if all_rows and rows[0][0] != all_rows[-1][0] + 900:
            raise ValueError("MONTH_BOUNDARY_GAP")
        all_rows.extend(rows)
    return all_rows


def walk_forward(rows: list[list], symbol: str = "SOL") -> dict:
    """One chronological train/holdout replay with uninterrupted equity curves.

    The last quarter is untouched while choosing the candidate. Each portion
    is replayed continuously across its real five-day competition windows;
    equity and max drawdown are never aggregated from isolated fold returns.
    """
    parsed = validate_candles(rows)
    if not parsed or len(parsed) < 30_000:
        return {"status": "INSUFFICIENT_HISTORY", "historyBars": len(parsed)}
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,12}", symbol):
        return {"status": "UNSUPPORTED_SIMULATION_SYMBOL"}
    split = len(parsed) * 3 // 4
    if split < 20_000 or len(parsed) - split < 7_000:
        return {"status": "INSUFFICIENT_CHRONOLOGICAL_SPLIT"}
    profiles = ("baseline", "recovery")
    training = {name: _simulate(parsed, PRESETS[name], 96, split, symbol=symbol)
                for name in profiles}
    holdout = {name: _simulate(parsed, PRESETS[name], split, len(parsed), symbol=symbol)
               for name in profiles}
    baseline_train, baseline_test = training["baseline"], holdout["baseline"]
    candidate = training["recovery"]
    train_edge = (candidate["net_return"] > baseline_train["net_return"] and
                  candidate["trades"] >= MIN_TRAIN_TRADES)
    test = holdout["recovery"]
    valid = (train_edge and test["trades"] >= MIN_TEST_TRADES and
             test["max_rolling_swaps_24h"] <= MAX_ROLLING_SWAPS and
             training["recovery"]["max_rolling_swaps_24h"] <= MAX_ROLLING_SWAPS and
             test["usdc_only_at_end"] is True and
             test["net_return"] > 0 and
             test["net_return"] > baseline_test["net_return"] + .003 and
             test["max_intraday_drawdown"] <= .018 and
             training["recovery"]["max_intraday_drawdown"] <= .018 and
             test["max_competition_drawdown"] <= .018 and
             training["recovery"]["max_competition_drawdown"] <= .018)
    status = ("VALIDATED_HISTORICAL_CANDIDATE" if valid else
              "REJECTED_OOS" if train_edge else "NO_TRAINING_EDGE")
    if not train_edge:
        reason = "RECOVERY_NOT_BETTER_DURING_TRAINING"
    elif (test["max_rolling_swaps_24h"] > MAX_ROLLING_SWAPS or
          training["recovery"]["max_rolling_swaps_24h"] > MAX_ROLLING_SWAPS):
        reason = "ROLLING_SWAP_LIMIT_EXCEEDED"
    elif not test["usdc_only_at_end"]:
        reason = "HOLDOUT_NOT_LIQUIDATED_TO_USDC"
    elif test["trades"] < MIN_TEST_TRADES:
        reason = "INSUFFICIENT_HOLDOUT_TRADES"
    elif test["net_return"] <= 0:
        reason = "RECOVERY_HOLDOUT_NOT_PROFITABLE_NET_OF_COSTS"
    elif test["net_return"] <= baseline_test["net_return"] + .003:
        reason = "RECOVERY_HOLDOUT_EDGE_BELOW_30_BPS"
    elif (test["max_intraday_drawdown"] > .018 or
          training["recovery"]["max_intraday_drawdown"] > .018 or
          test["max_competition_drawdown"] > .018 or
          training["recovery"]["max_competition_drawdown"] > .018):
        reason = "DRAWDOWN_EXCEEDS_LIVE_1_8_PERCENT_GUARD"
    else:
        reason = "VALIDATED_12M_OOS_ADVANTAGE"
    comparison = {name: {"train": training[name], "holdout": holdout[name]}
                  for name in profiles}
    return {"status": status, "decisionReason": reason, "profile": "recovery",
            "historyBars": len(parsed), "trainingBars": split,
            "holdoutBars": len(parsed) - split,
            "trainStartUtc": datetime.fromtimestamp(parsed[96][0], timezone.utc).isoformat(),
            "trainEndUtc": datetime.fromtimestamp(parsed[split - 1][0], timezone.utc).isoformat(),
            "holdoutStartUtc": datetime.fromtimestamp(parsed[split][0], timezone.utc).isoformat(),
            "holdoutEndUtc": datetime.fromtimestamp(parsed[-1][0], timezone.utc).isoformat(),
            "train": training["recovery"], "holdout": test,
            "baselineTrain": baseline_train, "baselineHoldout": baseline_test,
            "comparison": comparison,
            "costModel": {"estimatedRoundTripCostBps": ROUND_TRIP_FEE_ESTIMATE_BPS,
                "estimatedFeeBpsPerSwap": 10, "estimatedSlippageBpsPerSwap": 5,
                "maximumRollingSwaps24h": 10, "reservedExitSwapForEntry": True,
                "positionCap": .35, "exposureCap": .35,
                "drawdownGuard": .018, "mandatoryUsdcClose": True}}
