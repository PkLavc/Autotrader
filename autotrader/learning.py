"""Point-in-time prediction journal and deliberately conservative online feedback.

This is calibration of target *ranking*, not model self-training. Outcomes are
only resolved after the full five-session prediction horizon. Small samples
cannot alter trade selection. Never train on the day being predicted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import math
from pathlib import Path

import pandas as pd

from .config import StrategyConfig
from .forecast import Forecast
from .portfolio import Target, sector_of


def load_journal(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        return {"schema_version": 1, "pending": [], "completed": []}
    payload = json.loads(file.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unknown learning journal schema; refusing to reset it")
    if not isinstance(payload.get("pending"), list) or not isinstance(payload.get("completed"), list):
        raise ValueError("Invalid learning journal")
    return payload


def _normalized_index(history: pd.DataFrame) -> pd.DataFrame:
    out = history.copy()
    out.index = pd.to_datetime(out.index).normalize()
    return out.sort_index()


def resolve_outcomes(
    journal: dict, histories: dict[str, pd.DataFrame],
    benchmark_history: pd.DataFrame, market_date: str, cfg: StrategyConfig,
) -> dict:
    """Resolve only observations whose future daily closes have become available."""
    benchmark = _normalized_index(benchmark_history)
    pending = []
    matured = []
    known = {row["id"] for row in journal["completed"]}

    for row in journal["pending"]:
        if row["id"] in known:
            continue
        sym = row["symbol"]
        history = histories.get(sym)
        if history is None:
            pending.append(row)
            continue
        history = _normalized_index(history)
        signal_date = pd.Timestamp(row["signal_date"])
        if signal_date not in history.index:
            pending.append(row)
            continue
        idx = history.index.get_loc(signal_date)
        if not isinstance(idx, (int,)):
            pending.append(row)
            continue
        horizon = cfg.prediction_days
        if idx + horizon >= len(history):
            pending.append(row)
            continue
        final_date = history.index[idx + horizon]
        if final_date > pd.Timestamp(market_date):
            pending.append(row)
            continue
        if signal_date not in benchmark.index or final_date not in benchmark.index:
            pending.append(row)
            continue
        origin = float(history.loc[signal_date, "close"])
        observed = float(history.loc[final_date, "close"])
        index_start = float(benchmark.loc[signal_date, "close"])
        index_end = float(benchmark.loc[final_date, "close"])
        if any(not math.isfinite(v) or v <= 0 for v in (origin, observed, index_start, index_end)):
            pending.append(row)
            continue
        actual_return = observed / origin - 1.0
        benchmark_return = index_end / index_start - 1.0
        matured.append({
            **row,
            "resolution_date": final_date.date().isoformat(),
            "actual_return": actual_return,
            "benchmark_return": benchmark_return,
            "excess_return": actual_return - benchmark_return,
            "direction_correct": (actual_return > 0) == (row["expected_return"] > 0),
        })
        known.add(row["id"])

    journal["pending"] = pending[-250:]
    journal["completed"] = (journal["completed"] + matured)[-2000:]
    journal["last_evaluation_date"] = market_date
    return {"matured_today": len(matured), "awaiting_horizon": len(pending),
            "completed_total": len(journal["completed"])}


def sector_factors(journal: dict, cfg: StrategyConfig) -> dict[str, float]:
    """Shrink evidence toward neutrality and cap any sector tilt at 5%."""
    completed = journal["completed"]
    if len(completed) < cfg.adaptive_min_total_evaluations:
        return {}
    groups = defaultdict(list)
    for row in completed[-500:]:
        if row.get("excess_return") is not None and math.isfinite(float(row["excess_return"])):
            groups[row["sector"]].append(float(row["excess_return"]))
    factors = {}
    for sector, outcomes in groups.items():
        if len(outcomes) < cfg.adaptive_min_sector_evaluations:
            continue
        # Benchmark-beating rate is more robust to extreme outliers than mean PnL.
        positive = sum(value > 0 for value in outcomes)
        posterior_win_rate = (positive + 15) / (len(outcomes) + 30)
        factors[sector] = round(min(1.05, max(0.95, 1.0 + 0.10 * (posterior_win_rate - 0.5))), 6)
    return factors


def record_signals(
    journal: dict,
    targets: list[Target],
    forecasts: list[Forecast],
    market_date: str,
    sectors: dict[str, str],
) -> int:
    by_symbol = {forecast.symbol: forecast for forecast in forecasts}
    existing = {row["id"] for row in journal["pending"] + journal["completed"]}
    added = 0
    for target in targets:
        forecast = by_symbol.get(target.symbol)
        if forecast is None:
            continue
        key = f"{market_date}:{target.symbol}"
        if key in existing:
            continue
        journal["pending"].append({
            "id": key, "signal_date": market_date, "symbol": target.symbol,
            "sector": sectors.get(target.symbol, sector_of(target.symbol)),
            "expected_return": float(forecast.expected_return),
        })
        existing.add(key)
        added += 1
    journal["pending"] = journal["pending"][-250:]
    return added


def save_journal(journal: dict, path: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
