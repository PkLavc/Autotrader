"""Conservative walk-forward intraday parameter selection for AudaceFi Devnet.

Uses completed Coinbase 15-minute candles only. Training and holdout trades
enter at the NEXT completed close; results include round-trip fees and slippage.
An unvalidated variant NEVER relaxes the existing trading rules.
No network, credentials, order placement, or model-generated executable code.
"""
from __future__ import annotations

from dataclasses import replace
import math
from statistics import median

from .league_strategy import LeaguePolicy

# Parameter universe is fixed and small, preventing arbitrary risk changes.
PRESETS = {
    "baseline": {"intraday_m3_min": .003, "intraday_m6_min": .007,
                 "intraday_m12_min": -.005, "intraday_m24_min": -.02,
                 "minimum_net_edge": .004},
    "breakout": {"intraday_m3_min": .006, "intraday_m6_min": .012,
                 "intraday_m12_min": .004, "intraday_m24_min": -.005,
                 "minimum_net_edge": .006},
    "steady": {"intraday_m3_min": .004, "intraday_m6_min": .009,
               "intraday_m12_min": 0.0, "intraday_m24_min": -.01,
               "minimum_net_edge": .005},
    "early": {"intraday_m3_min": .001, "intraday_m6_min": .004,
              "intraday_m12_min": -.012, "intraday_m24_min": -.025,
              "minimum_net_edge": .003},
}
ROUND_TRIP_COST = .006  # Stress estimate: fees + slippage across both legs.
HOLD_CANDLES = 8
MIN_TRAIN_TRADES = 3
MIN_TEST_TRADES = 3
REQUIRED_TEST_EDGE = .002


def policy_for(profile: str, base: LeaguePolicy) -> LeaguePolicy:
    if profile not in PRESETS:
        return base
    return replace(base, **PRESETS[profile])


def validate_candles(rows: object) -> list[tuple[int, float, float]]:
    """Reject sparse/unfinished/duplicated bars; do not pad missing data."""
    if not isinstance(rows, list) or len(rows) < 255 or len(rows) > 1200:
        return []
    parsed = []
    try:
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                return []
            ts, price, volume = row
            if (not isinstance(ts, int) or isinstance(ts, bool) or ts % 900 or
                not isinstance(price, (int, float)) or isinstance(price, bool) or
                not isinstance(volume, (int, float)) or isinstance(volume, bool)):
                return []
            price, volume = float(price), float(volume)
            if not all(map(math.isfinite, (price, volume))) or price <= 0 or volume <= 0:
                return []
            parsed.append((ts, price, volume))
    except (TypeError, ValueError, OverflowError):
        return []
    if any(b[0] - a[0] != 900 for a, b in zip(parsed, parsed[1:])):
        return []
    return parsed


def _indicators(rows: list[tuple[int, float, float]], index: int) -> tuple | None:
    if index < 96:
        return None
    close = rows[index][1]
    m1, m3, m6, m12, m24 = (close / rows[index - j][1] - 1
                             for j in (4, 12, 24, 48, 96))
    ema = rows[index - 20][1]
    for k in range(index - 19, index + 1):
        ema = (2 / 21) * rows[k][1] + (19 / 21) * ema
    volume = median(price * vol for _, price, vol in rows[index-95:index+1])
    return m1, m3, m6, m12, m24, close > ema, volume


def _qualifies(indicators: tuple, preset: dict, baseline_cost: float) -> bool:
    m1, m3, m6, m12, m24, above, liquidity = indicators
    strength = .25 * m3 + .40 * m6 + .25 * m12 + .10 * m24
    return (above and liquidity >= 100_000 and m1 > 0 and
            m3 >= preset["intraday_m3_min"] and
            m6 >= preset["intraday_m6_min"] and
            m12 >= preset["intraday_m12_min"] and
            m24 >= preset["intraday_m24_min"] and
            strength >= preset["minimum_net_edge"] + baseline_cost)


def _simulate(rows: list[tuple[int, float, float]], preset: dict,
              first: int, last: int) -> dict:
    # last is exclusive for signal index, leaving next-open/next-exit in segment.
    returns: list[float] = []
    idx = max(96, first)
    while idx + HOLD_CANDLES + 1 < last:
        values = _indicators(rows, idx)
        if values and _qualifies(values, preset, .003):
            entry = rows[idx + 1][1]
            # Look at later candles ONLY for actual exits, never signal generation.
            end = idx + 1 + HOLD_CANDLES
            # Conservative intrabar stop, using closing values; next-step execution
            # plus extra slippage is not guaranteed, so stress cost is retained.
            for k in range(idx + 2, end + 1):
                if rows[k][1] <= entry * .965:
                    end = k
                    break
            net = rows[end][1] / entry - 1 - ROUND_TRIP_COST
            returns.append(net)
            idx = end + 1
        else:
            idx += 1
    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for gain in returns:
        equity *= 1 + min(gain * .35, 1)
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, 1 - equity / peak)
    return {
        "trades": len(returns),
        "net_return": round(equity - 1, 8),
        "max_drawdown": round(maximum_drawdown, 8),
        "win_rate": round(sum(gain > 0 for gain in returns) / len(returns), 6)
        if returns else 0.0,
    }


def evaluate(candles_by_symbol: dict[str, object]) -> dict:
    """Train on past candles, score on a disjoint forward holdout.

    Candidate selection looks at TRAINING only. Holdout is then used as an
    admission gate (not as a fit target), and repeated live validations are
    separately required before the live runner adopts a variant.
    """
    if not isinstance(candles_by_symbol, dict):
        return {"status": "INSUFFICIENT_DATA"}
    train_candidates = []
    held_out: dict[tuple[str, str], tuple[list, int]] = {}
    for symbol in ("SOL", "ETH"):
        rows = validate_candles(candles_by_symbol.get(symbol))
        if not rows:
            continue
        # Training ends before holdout starts; each trade fully closes inside
        # its own segment. No future close can influence a train signal.
        split = len(rows) - 88
        train = {name: _simulate(rows, p, 96, split) for name, p in PRESETS.items()}
        for name, result in train.items():
            if name == "baseline" or result["trades"] < MIN_TRAIN_TRADES or result["net_return"] <= 0:
                continue
            # Save validation data WITHOUT inspecting its performance during
            # selection. Select on training alone; then open the holdout once.
            train_candidates.append((result["net_return"], symbol, name, result))
            held_out[(symbol, name)] = (rows, split)
    if not train_candidates:
        return {"status": "NO_VALIDATED_IMPROVEMENT", "symbolsChecked": sorted(
            s for s in ("SOL", "ETH") if validate_candles(candles_by_symbol.get(s))
        )}
    # One candidate is nominated using training only (not best holdout P&L).
    winner = max(train_candidates, key=lambda item: (item[0], item[1], item[2]))
    train_return, symbol, name, training = winner
    rows, split = held_out[(symbol, name)]
    validation = _simulate(rows, PRESETS[name], split, len(rows))
    baseline = _simulate(rows, PRESETS["baseline"], split, len(rows))
    if not (validation["trades"] >= MIN_TEST_TRADES and
            validation["net_return"] > REQUIRED_TEST_EDGE and
            validation["net_return"] > baseline["net_return"] + REQUIRED_TEST_EDGE and
            validation["win_rate"] >= .5 and
            validation["max_drawdown"] <= .012 and
            training["max_drawdown"] <= .012):
        return {"status": "NO_VALIDATED_IMPROVEMENT", "symbolsChecked": sorted(
            s for s in ("SOL", "ETH") if validate_candles(candles_by_symbol.get(s))
        )}
    return {"status": "VALIDATED_CANDIDATE", "symbol": symbol, "profile": name,
            "train": training, "holdout": validation,
            "costBps": round(ROUND_TRIP_COST * 10000)}


def update_promotion(state: object, suggestion: dict, *, window: int) -> dict:
    """Deterministic hysteresis. Three consecutive NEW windows to change profile."""
    if not isinstance(window, int) or window < 0:
        raise ValueError("invalid window")
    old = state if isinstance(state, dict) else {}
    active = old.get("active", "baseline")
    if active not in PRESETS:
        active = "baseline"
    prev = old.get("window", -1)
    cooling = old.get("cooldownUntilWindow", -1)
    if isinstance(cooling, int) and cooling > window:
        return {"active": "baseline", "pending": None, "count": 0,
                "window": window, "cooldownUntilWindow": cooling}
    if not isinstance(prev, int) or prev >= window:
        return {"active": active, "pending": old.get("pending"),
                "count": old.get("count", 0), "window": prev}
    profile = suggestion.get("profile") if suggestion.get("status") == "VALIDATED_CANDIDATE" else None
    if profile not in PRESETS or profile == active:
        return {"active": active, "pending": None, "count": 0, "window": window}
    count = old.get("count", 0) + 1 if old.get("pending") == profile else 1
    if count >= 3:
        return {"active": profile, "pending": None, "count": 0, "window": window}
    return {"active": active, "pending": profile, "count": count, "window": window}
