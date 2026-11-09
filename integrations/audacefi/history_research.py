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

from .adaptive_learning import PRESETS, _simulate, validate_candles

ORIGIN = "https://data.binance.vision"
MAX_ZIP = 5_000_000
MAX_CSV = 15_000_000
WINDOW = 320
MIN_TRAIN_TRADES = 10
MIN_TEST_TRADES = 6
ROUND_TRIP_FEE_ESTIMATE_BPS = 60


def complete_months(now: datetime, count: int = 4) -> list[str]:
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


def aggregate(folds: list[dict]) -> dict:
    compounded = 1.0
    max_single_drawdown = 0.0
    trades = 0
    winning = 0
    for fold in folds:
        compounded *= 1 + fold["net_return"]
        trades += fold["trades"]
        winning += fold["win_rate"] * fold["trades"]
        max_single_drawdown = max(max_single_drawdown, fold["max_drawdown"])
    return {"trades": trades, "net_return": round(compounded - 1, 8),
            "max_drawdown": round(max_single_drawdown, 8),
            "win_rate": round(winning / trades, 6) if trades else 0.0}


def walk_forward(rows: list[list]) -> dict:
    if not isinstance(rows, list) or len(rows) < WINDOW * 8:
        return {"status": "INSUFFICIENT_HISTORY"}
    # Each simulated block is non-overlapping. All price features warm up
    # using data from the same block; no lookahead across block boundaries.
    folds = []
    for begin in range(0, len(rows) - WINDOW + 1, WINDOW):
        batch = rows[begin:begin + WINDOW]
        if not validate_candles(batch):
            continue
        folds.append({name: _simulate(batch, p, 96, len(batch))
                      for name, p in PRESETS.items()})
    if len(folds) < 8:
        return {"status": "INSUFFICIENT_FOLDS"}
    # Keep a chronological final quarter completely outside parameter selection.
    split = len(folds) * 3 // 4
    training = {name: aggregate([fold[name] for fold in folds[:split]])
                for name in PRESETS}
    holdout_comparison = {name: aggregate([fold[name] for fold in folds[split:]])
                          for name in PRESETS}
    baseline = training["baseline"]
    names = [name for name in PRESETS if name != "baseline" and
             training[name]["trades"] >= MIN_TRAIN_TRADES and
             training[name]["net_return"] > 0 and
             training[name]["net_return"] > baseline["net_return"]]
    if not names:
        return {"status": "NO_TRAINING_EDGE", "folds": len(folds),
                "baselineTrain": baseline,
                "baselineHoldout": holdout_comparison["baseline"],
                "comparison": {name: {"train": training[name],
                                      "holdout": holdout_comparison[name]}
                               for name in PRESETS}}
    # One choice from TRAIN only. Do not search the final holdout for a winner.
    name = max(names, key=lambda n: (training[n]["net_return"], n))
    testing = holdout_comparison[name]
    reference = holdout_comparison["baseline"]
    valid = (testing["trades"] >= MIN_TEST_TRADES and
             testing["net_return"] > .003 and
             testing["net_return"] > reference["net_return"] + .003 and
             testing["max_drawdown"] <= .012 and
             testing["win_rate"] >= .5)
    return {"status": "VALIDATED_HISTORICAL_CANDIDATE" if valid else "REJECTED_OOS",
            "profile": name, "folds": len(folds), "train": training[name],
            "holdout": testing, "baselineHoldout": reference,
            "comparison": {profile: {"train": training[profile],
                                     "holdout": holdout_comparison[profile]}
                           for profile in PRESETS},
            "estimatedRoundTripCostBps": ROUND_TRIP_FEE_ESTIMATE_BPS}
