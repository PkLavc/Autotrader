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
import random
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile, BadZipFile

from .adaptive_learning import (MAX_ROLLING_SWAPS, PRESETS, _simulate,
                                generate_candidates, validate_candles)

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


def holdout_consistency(rows: list[tuple[int, float, float]], profile: str,
                        first: int, last: int, *, symbol: str,
                        parameters: dict | None = None) -> dict:
    """Paired five-day candidate/baseline evidence with regime breakdown."""
    observations = []
    by_regime: dict[str, list[float]] = {}
    for begin in range(first, last, 5 * 24 * 4):
        end = min(last, begin + 5 * 24 * 4)
        if end - begin < 3 * 24 * 4 or begin < 96:
            continue
        reference = rows[begin][1] / rows[begin - 96][1] - 1
        regime = "bull" if reference >= .01 else "bear" if reference <= -.01 else "sideways"
        candidate = _simulate(rows, parameters or PRESETS[profile], begin, end, symbol=symbol)
        baseline = _simulate(rows, PRESETS["baseline"], begin, end, symbol=symbol)
        edge = candidate["net_return"] - baseline["net_return"]
        observations.append(edge)
        by_regime.setdefault(regime, []).append(edge)
    n = len(observations)
    wins = sum(edge > 0 for edge in observations)
    # 95% Wilson lower confidence bound for the candidate's fraction of
    # five-day windows beating baseline, avoiding a normal approximation.
    z = 1.96
    denominator = 1 + z*z/n if n else 1
    center = wins/n + z*z/(2*n) if n else 0
    margin = z * math.sqrt((wins/n*(1-wins/n) + z*z/(4*n))/n) if n else 0
    lower = max(0.0, (center - margin) / denominator) if n else 0.0
    regimes = {name: {"folds": len(edges), "meanEdge": round(sum(edges)/len(edges), 8)}
               for name, edges in sorted(by_regime.items())}
    bootstrap: list[float] = []
    if n >= 10:
        rng = random.Random(0xA0DACE)
        block_size = 2  # paired, non-overlapping five-day blocks
        for _ in range(1000):
            sample = []
            while len(sample) < n:
                start = rng.randrange(max(1, n - block_size + 1))
                sample.extend(observations[start:start + block_size])
            bootstrap.append(sum(sample[:n]) / n)
    bootstrap.sort()
    bootstrap_lower = bootstrap[max(0, int(.025 * len(bootstrap)) - 1)] if bootstrap else 0.0
    return {"folds": n, "foldsBeatingBaseline": wins,
            "wilsonLower95": round(lower, 6),
            "blockBootstrapMeanEdgeLower95": round(bootstrap_lower, 8),
            "bootstrapBlockFolds": 2, "regimes": regimes,
            "regimeCoverage": len(regimes)}


def walk_forward(rows: list[list], symbol: str = "SOL") -> dict:
    """One chronological train/holdout replay with uninterrupted equity curves.

    The last quarter remains untouched during candidate selection. One
    training-selected candidate is evaluated there without parameter changes;
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
    # Bounded, interpretable strategy families. The winner is selected only
    # on the chronological training segment; the untouched holdout is opened
    # once for that one nominated candidate.
    profiles = generate_candidates(parsed[:split])
    training = {name: _simulate(parsed, params, 96, split, symbol=symbol)
                for name, params in profiles.items()}
    holdout = {name: _simulate(parsed, params, split, len(parsed), symbol=symbol)
               for name, params in profiles.items()}
    baseline_train, baseline_test = training["baseline"], holdout["baseline"]
    candidates = [name for name in profiles if name != "baseline" and
                  training[name]["trades"] >= MIN_TRAIN_TRADES and
                  training[name]["net_return"] > baseline_train["net_return"] and
                  training[name]["max_intraday_drawdown"] <= .018 and
                  training[name]["max_competition_drawdown"] <= .018]
    selected = max(candidates, key=lambda name: (
        training[name]["net_return"] - baseline_train["net_return"],
        -training[name]["max_drawdown"], name)) if candidates else None
    test = holdout[selected] if selected else holdout["baseline"]
    candidate_train = training[selected] if selected else baseline_train
    train_edge = selected is not None
    consistency = (holdout_consistency(parsed, selected, split, len(parsed), symbol=symbol,
                                       parameters=profiles[selected])
                   if selected else {"folds": 0, "foldsBeatingBaseline": 0,
                                    "wilsonLower95": 0.0, "regimes": {}, "regimeCoverage": 0})
    consistency_pass = (consistency["folds"] >= 10 and
                        consistency["wilsonLower95"] > .50 and
                        consistency["blockBootstrapMeanEdgeLower95"] > 0 and
                        consistency["regimeCoverage"] >= 2 and
                        all(value["folds"] >= 3 for value in consistency["regimes"].values()) and
                        all(value["meanEdge"] >= 0 for value in consistency["regimes"].values()))
    valid = (train_edge and test["trades"] >= MIN_TEST_TRADES and
             test["max_rolling_swaps_24h"] <= MAX_ROLLING_SWAPS and
             candidate_train["max_rolling_swaps_24h"] <= MAX_ROLLING_SWAPS and
             test["usdc_only_at_end"] is True and
             test["net_return"] > 0 and
             test["net_return"] > baseline_test["net_return"] + .003 and
             test["max_intraday_drawdown"] <= .018 and
             candidate_train["max_intraday_drawdown"] <= .018 and
             test["max_competition_drawdown"] <= .018 and
             candidate_train["max_competition_drawdown"] <= .018 and
             consistency_pass)
    status = ("VALIDATED_HISTORICAL_CANDIDATE" if valid else
              "REJECTED_OOS" if train_edge else "NO_TRAINING_EDGE")
    if not train_edge:
        reason = "NO_GENERATED_CANDIDATE_BEATS_BASELINE_IN_TRAINING"
    elif (test["max_rolling_swaps_24h"] > MAX_ROLLING_SWAPS or
          candidate_train["max_rolling_swaps_24h"] > MAX_ROLLING_SWAPS):
        reason = "ROLLING_SWAP_LIMIT_EXCEEDED"
    elif not test["usdc_only_at_end"]:
        reason = "HOLDOUT_NOT_LIQUIDATED_TO_USDC"
    elif test["trades"] < MIN_TEST_TRADES:
        reason = "INSUFFICIENT_HOLDOUT_TRADES"
    elif test["net_return"] <= 0:
        reason = "CANDIDATE_HOLDOUT_NOT_PROFITABLE_NET_OF_COSTS"
    elif test["net_return"] <= baseline_test["net_return"] + .003:
        reason = "CANDIDATE_HOLDOUT_EDGE_BELOW_30_BPS"
    elif (test["max_intraday_drawdown"] > .018 or
          candidate_train["max_intraday_drawdown"] > .018 or
          test["max_competition_drawdown"] > .018 or
          candidate_train["max_competition_drawdown"] > .018):
        reason = "DRAWDOWN_EXCEEDS_LIVE_1_8_PERCENT_GUARD"
    elif not consistency_pass:
        reason = "HOLDOUT_FOLD_OR_REGIME_CONFIDENCE_INSUFFICIENT"
    else:
        reason = "VALIDATED_12M_OOS_ADVANTAGE"
    comparison = {name: {"train": training[name], "holdout": holdout[name],
                         "parameters": profiles[name]}
                  for name in profiles}
    return {"status": status, "decisionReason": reason,
            "profile": selected or "baseline",
            "historyBars": len(parsed), "trainingBars": split,
            "holdoutBars": len(parsed) - split,
            "trainStartUtc": datetime.fromtimestamp(parsed[96][0], timezone.utc).isoformat(),
            "trainEndUtc": datetime.fromtimestamp(parsed[split - 1][0], timezone.utc).isoformat(),
            "holdoutStartUtc": datetime.fromtimestamp(parsed[split][0], timezone.utc).isoformat(),
            "holdoutEndUtc": datetime.fromtimestamp(parsed[-1][0], timezone.utc).isoformat(),
            "train": candidate_train, "holdout": test,
            "holdoutConsistency": consistency,
            "baselineTrain": baseline_train, "baselineHoldout": baseline_test,
            "comparison": comparison,
            "candidateGeneration": {"method": "training_segment_median_abs_24h_return",
                "baselineFixed": True, "thresholdScale": round(max(.75, min(1.5,
                    (sorted(abs(parsed[i][1] / parsed[i-96][1] - 1)
                            for i in range(96, split))[max(0, (split-96)//2)] / .02))), 6)},
            "costModel": {"estimatedRoundTripCostBps": ROUND_TRIP_FEE_ESTIMATE_BPS,
                "estimatedFeeBpsPerSwap": 10, "estimatedSlippageBpsPerSwap": 5,
                "maximumRollingSwaps24h": 10, "reservedExitSwapForEntry": True,
                "positionCap": .35, "exposureCap": .35,
                "drawdownGuard": .018, "mandatoryUsdcClose": True}}
