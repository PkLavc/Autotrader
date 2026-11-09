"""Leakage-safe, bounded feedback selection from matured shadow outcomes.

Counterfactual labels are observations, never fills. This module may select only
between momentum and a strategy independently qualified by historical OOS.
"""
from __future__ import annotations

import math
from typing import Any

from .adaptive_learning import PRESETS

SUPPORTED = {"SOL", "ETH"}
MIN_OBSERVATIONS = 30
MIN_VALIDATION = 8
MAX_DRAWDOWN = .018
MIN_EDGE = .003
HORIZON_MS = 24 * 60 * 60 * 1000


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _accepts(profile: str, params: dict, indicators: dict) -> bool:
    get = lambda name: _number(indicators.get(name))
    m1, m3, m6, m12, m24 = (get(f"momentum_{period}") for period in
                             ("1h", "3h", "6h", "12h", "24h"))
    if None in (m1, m3, m6, m12, m24):
        return False
    family = params.get("intraday_strategy", "momentum")
    above = indicators.get("above_ema20") is True
    m3_min = float(params.get("intraday_m3_min", .003))
    m6_min = float(params.get("intraday_m6_min", .007))
    m12_min = float(params.get("intraday_m12_min", -.005))
    m24_min = float(params.get("intraday_m24_min", -.02))
    if family == "recovery":
        return -.06 <= m24 <= -.01 and m3 >= .008 and m6 >= .003 and m1 > 0 and above
    if family == "breakout":
        return (m1 > 0 and m3 >= m3_min and m6 >= m6_min and m12 >= m12_min and
                m24 >= m24_min and above and (get("breakout_20") or -1) >= .002)
    if family == "mean_reversion":
        return (m1 > 0 and m3 >= m3_min and m24 >= m24_min and
                (get("ema20_gap") or 0) <= -.008 and (get("rsi14") or 100) <= 32)
    if family == "multi_trend":
        return (m1 > 0 and m3 >= m3_min and m6 >= m6_min and m12 >= m12_min and
                m24 >= m24_min and above)
    return (m1 > 0 and m3 >= m3_min and m6 >= m6_min and m12 >= m12_min and
            m24 >= m24_min and above)


def _valid_candidate(profile: object, params: object) -> bool:
    if profile not in PRESETS or profile == "baseline" or not isinstance(params, dict):
        return False
    if params.get("intraday_strategy", "momentum") not in {
            "momentum", "recovery", "breakout", "mean_reversion", "multi_trend"}:
        return False
    for key, low, high in (("intraday_m3_min", 0, .02),
                           ("intraday_m6_min", 0, .02),
                           ("intraday_m12_min", -.10, .05),
                           ("intraday_m24_min", -.10, .05),
                           ("minimum_net_edge", .003, .02)):
        value = _number(params.get(key, PRESETS[profile].get(key, .004)))
        if value is None or not low <= value <= high:
            return False
    return True


def _curve(returns: list[float]) -> tuple[float, float]:
    equity = peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity *= max(.01, 1 + .35 * value)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    return equity - 1, max_dd


def select_by_asset(approved: object, ledger: object, *, now_ms: int,
                    prior: object = None, realized: object = None) -> dict:
    """Select per symbol from matured 24h outcomes, with a chronological holdout.

    Training and validation examples are distinct and ordered by observation
    time. At most one observation per symbol per 24h is used, so frequent
    scheduler runs cannot turn overlapping candles into fake sample size.
    """
    if not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("FEEDBACK_CLOCK_INVALID")
    approved = approved if isinstance(approved, dict) else {}
    ledger = ledger if isinstance(ledger, list) else []
    realized = realized if isinstance(realized, list) else []
    prior = prior if isinstance(prior, dict) else {}
    previous = prior.get("activeBySymbol", {})
    previous = previous if isinstance(previous, dict) else {}
    result: dict[str, Any] = {"activeBySymbol": {}, "activeParametersBySymbol": {},
                              "evidenceBySymbol": {}}
    for symbol in sorted(SUPPORTED):
        selected = "momentum"
        item = approved.get(symbol)
        profile = item.get("profile") if isinstance(item, dict) else None
        params = item.get("parameters") if isinstance(item, dict) else None
        if not _valid_candidate(profile, params):
            result["activeBySymbol"][symbol] = selected
            result["evidenceBySymbol"][symbol] = {"status": "NO_OOS_APPROVED_CANDIDATE"}
            continue

        realized_losses = [row for row in realized if isinstance(row, dict) and
            row.get("symbol") == symbol and row.get("profile") == profile and
            isinstance(row.get("pnl"), (int, float)) and
            not isinstance(row.get("pnl"), bool) and row["pnl"] < 0]
        if len(realized_losses) >= 3 and sum(float(row["pnl"]) for row in realized_losses[-3:]) < -20:
            result["activeBySymbol"][symbol] = "momentum"
            result["evidenceBySymbol"][symbol] = {
                "status": "LIVE_REALIZED_LOSS_ROLLBACK", "losingRoundTrips": 3}
            continue

        observations = []
        seen_days = set()
        for row in ledger:
            if not isinstance(row, dict) or row.get("symbol") != symbol:
                continue
            observed = row.get("observedAt")
            indicators = row.get("indicators")
            outcome = row.get("outcomes", {}).get("24h") if isinstance(row.get("outcomes"), dict) else None
            if (not isinstance(observed, int) or observed + HORIZON_MS > now_ms or
                    not isinstance(outcome, dict) or not isinstance(indicators, dict) or
                    not isinstance(outcome.get("markedAt"), int) or
                    outcome["markedAt"] < observed + HORIZON_MS):
                continue
            net = _number(outcome.get("netReturnAfterEstimatedCosts"))
            if net is None or abs(net) > .5:
                continue
            bucket = observed // HORIZON_MS
            if bucket in seen_days:
                continue
            seen_days.add(bucket)
            observations.append((observed, net, indicators))
        observations.sort(key=lambda row: row[0])
        if len(observations) < MIN_OBSERVATIONS:
            result["activeBySymbol"][symbol] = selected
            result["evidenceBySymbol"][symbol] = {
                "status": "INSUFFICIENT_MATURED_OBSERVATIONS", "count": len(observations)}
            continue
        split = max(1, int(len(observations) * .7))
        train, validation = observations[:split], observations[split:]
        if len(validation) < MIN_VALIDATION:
            result["activeBySymbol"][symbol] = selected
            result["evidenceBySymbol"][symbol] = {
                "status": "INSUFFICIENT_CHRONOLOGICAL_VALIDATION", "count": len(observations)}
            continue
        train_baseline = [net if _accepts("momentum", PRESETS["baseline"], indicators) else 0.0
                          for _, net, indicators in train]
        train_baseline_return = _curve(train_baseline)[0]
        # Online parameter search only tightens entries and raises the net-edge
        # hurdle. It cannot loosen risk or increase exposure.
        tuned = []
        for scale in (1.0, 1.05, 1.10):
            trial = dict(params)
            for key in ("intraday_m3_min", "intraday_m6_min",
                        "intraday_m12_min", "intraday_m24_min"):
                value = _number(trial.get(key))
                if value is not None:
                    trial[key] = round(value * scale if value >= 0 else value / scale, 6)
            edge = _number(trial.get("minimum_net_edge"))
            trial["minimum_net_edge"] = round(min(.02,
                (edge if edge is not None else .004) + (scale - 1) * .002), 6)
            train_candidate = [net if _accepts(profile, trial, indicators) else 0.0
                               for _, net, indicators in train]
            train_return, train_dd = _curve(train_candidate)
            tuned.append((train_return, -train_dd, scale, trial,
                          train_return - train_baseline_return))
        _, _, _, tuned_params, train_edge = max(tuned, key=lambda row: (row[0], row[1], row[2]))
        base_returns, candidate_returns = [], []
        candidate_trades = 0
        for _, net, indicators in validation:
            base_returns.append(net if _accepts("momentum", PRESETS["baseline"], indicators) else 0.0)
            candidate_passes = _accepts(profile, tuned_params, indicators)
            candidate_trades += int(candidate_passes)
            candidate_returns.append(net if candidate_passes else 0.0)
        base_return, _ = _curve(base_returns)
        candidate_return, candidate_dd = _curve(candidate_returns)
        newest = max(item[0] + HORIZON_MS for item in observations)
        old_newest = prior.get("feedbackWatermarkBySymbol", {}).get(symbol, 0)
        if newest <= old_newest:
            selected = previous.get(symbol, "momentum")
            if selected not in {"momentum", profile}:
                selected = "momentum"
            saved_params = prior.get("activeParametersBySymbol", {}).get(symbol)
            if selected == profile and isinstance(saved_params, dict):
                result["activeParametersBySymbol"][symbol] = saved_params
        elif (candidate_trades >= 3 and candidate_return > 0 and
                candidate_return > base_return + MIN_EDGE and
                candidate_dd <= MAX_DRAWDOWN and train_edge > 0 and newest > old_newest):
            selected = profile
            result["activeParametersBySymbol"][symbol] = tuned_params
        result["activeBySymbol"][symbol] = selected
        result["evidenceBySymbol"][symbol] = {
            "status": "PROMOTED_FROM_MATURED_OOS_FEEDBACK" if selected == profile else
                     "MOMENTUM_RETAINED",
            "profile": profile, "observations": len(observations),
            "trainingObservations": len(train), "validationObservations": len(validation),
            "validationNetReturn": round(candidate_return, 8),
            "baselineValidationNetReturn": round(base_return, 8),
            "validationMaxDrawdown": round(candidate_dd, 8),
            "validationTrades": candidate_trades,
            "trainingNetEdge": round(train_edge, 8),
            "selectedParameters": (tuned_params if selected == profile else None),
            "watermark": newest,
        }
        result.setdefault("feedbackWatermarkBySymbol", {})[symbol] = max(old_newest, newest)
    return result
