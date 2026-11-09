"""Use the existing league strategy with completed SOL daily candles.

The fast scheduled signal is explicitly momentum based; it does not claim to
be a Kronos forecast. The league strategy still applies its entry and exit
rules to the verified account and wallet context supplied on stdin.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sys
from urllib.request import Request, urlopen

from autotrader.data import load_daily_history
from .crypto_research import completed_crypto_bars
from .league_strategy import LeaguePolicy, decide
from .adaptive_learning import evaluate, policy_for, update_promotion
from .adaptive_feedback import select_by_asset
from .intraday_signal import signal_from_rows


def completed_intraday_signal(symbol: str, now: datetime) -> dict:
    if symbol not in {"SOL", "ETH"}:
        raise ValueError("UNSUPPORTED_INTRADAY_SYMBOL")
    request = Request(
        f"https://api.exchange.coinbase.com/products/{symbol}-USD/candles?granularity=900",
        headers={"Accept": "application/json", "User-Agent": "Autotrader-AudaceFi/1.0"},
    )
    with urlopen(request, timeout=12) as response:
        raw = response.read(100_001)
    if len(raw) > 100_000:
        raise ValueError("CANDLES_TOO_LARGE")
    candles = json.loads(raw)
    if not isinstance(candles, list) or len(candles) < 100 or len(candles) > 400:
        raise ValueError("CANDLES_INVALID")
    values = {}
    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 6:
            raise ValueError("CANDLE_INVALID")
        timestamp, close, volume = candle[0], float(candle[4]), float(candle[5])
        if not isinstance(timestamp, int) or timestamp % 900 or not all(
            math.isfinite(value) and value > 0 for value in (close, volume)
        ):
            raise ValueError("CANDLE_VALUE_INVALID")
        if timestamp + 900 <= now.timestamp() - 30:
            values[timestamp] = (close, volume)
    if not values:
        raise ValueError("NO_COMPLETED_CANDLE")
    latest = max(values)
    if now.timestamp() - (latest + 900) > 1800:
        raise ValueError("CANDLES_STALE")
    if any(latest - 900 * step not in values for step in range(97)):
        raise ValueError("CANDLE_WINDOW_GAPPED")
    rows = [(ts, values[ts][0], values[ts][1]) for ts in sorted(values)]
    signal = signal_from_rows(symbol, rows, len(rows) - 1)
    if signal is None:
        raise ValueError("CANDLE_SIGNAL_INVALID")
    return {**signal,
            # Private to the local training process; never sent to the exchange.
            "_candles": [[ts, *values[ts]] for ts in sorted(values)]}


def main() -> int:
    request = json.load(sys.stdin)
    context = request["context"]
    portfolio = request["portfolio"]
    now = datetime.now(timezone.utc)
    report: dict = {}
    intraday = request.get("signalMode") == "intraday"
    adaptive = {"status": "NOT_EVALUATED"}
    if request.get("fetchSignals") is True and intraday:
        observations = [completed_intraday_signal(symbol, now) for symbol in
                        context["eligible_symbols"] if symbol in {"SOL", "ETH"}]
        if not observations:
            raise RuntimeError("NO_SUPPORTED_INTRADAY_ASSET")
        # Fit on completed history only. No unvalidated candidate is traded.
        adaptive = evaluate({row["symbol"]: row.get("_candles") for row in observations})
        signals = [{k: v for k, v in row.items() if k != "_candles"}
                   for row in observations]
        report = {"asset_class": "crypto", "generated_at": now.isoformat(),
                  "model": "coinbase_completed_15m", "signals": signals}
    elif request.get("fetchSignals") is True:
        closed = completed_crypto_bars(load_daily_history("SOL-USD", 45), now)
        if len(closed) < 31:
            raise RuntimeError("INSUFFICIENT_COMPLETED_SOL_HISTORY")
        close = closed["close"].astype(float)
        volume = closed["volume"].astype(float)
        momentum_3d = float(close.iloc[-1] / close.iloc[-4] - 1)
        momentum_7d = float(close.iloc[-1] / close.iloc[-8] - 1)
        volatility = float(close.pct_change().dropna().tail(30).std(ddof=1) * math.sqrt(365))
        liquidity = float((close * volume).tail(20).median())
        expected_return = max(0.0, min(0.25, 0.5 * momentum_3d + 0.5 * momentum_7d))
        if not all(math.isfinite(value) for value in (
            momentum_3d, momentum_7d, volatility, liquidity, expected_return
        )):
            raise RuntimeError("NONFINITE_SOL_SIGNAL")
        report = {
            "asset_class": "crypto", "generated_at": now.isoformat(),
            "model": "completed_daily_momentum", "signals": [{
                "symbol": "SOL", "expected_return": expected_return,
                "momentum_3d": momentum_3d, "momentum_7d": momentum_7d,
                "volatility_annualized": volatility,
                "avg_dollar_volume_usd": liquidity,
            }],
        }
    policy = LeaguePolicy(risk_budget_per_trade=.012, maximum_season_drawdown=.018,
                          stop_distance_min=.025, stop_distance_max=.035,
                          closing_buffer_hours=16, new_entry_cutoff_hours=24,
                          maximum_signal_age_hours=.5, round_trip_cost_bps=30,
                          minimum_net_edge=.004) if intraday else None
    if intraday:
        profile = request.get("activeProfile", "baseline")
        if not isinstance(profile, str) or profile not in {
                "baseline", "early", "steady", "breakout", "recovery",
                "mean_reversion", "multi_trend"}:
            raise ValueError("INVALID_ADAPTIVE_PROFILE")
        # Each asset must pass its own untouched historical OOS qualification.
        # Matured counterfactuals may choose/roll back only within that gate.
        approved_by_symbol = request.get("historicalStrategies")
        if not isinstance(approved_by_symbol, dict):
            approved_by_symbol = {}
        qualification_diagnostics = request.get("historicalDiagnostics")
        if not isinstance(qualification_diagnostics, dict):
            qualification_diagnostics = {}
        prior_state = request.get("adaptiveState")
        feedback = select_by_asset(approved_by_symbol,
            request.get("opportunityFeedback", []),
            now_ms=int(now.timestamp() * 1000), prior=prior_state,
            realized=request.get("realizedFeedback", []))
        profile_by_symbol = feedback["activeBySymbol"]
        if request.get("enforceEntryQualification") is True:
            report["entry_eligible_symbols"] = sorted(
                symbol for symbol, selected in profile_by_symbol.items()
                if selected != "momentum" and
                isinstance(approved_by_symbol.get(symbol), dict) and
                approved_by_symbol[symbol].get("profile") == selected)
        policy_by_symbol = {}
        for symbol, selected in profile_by_symbol.items():
            qualification = approved_by_symbol.get(symbol)
            approved_profile = qualification.get("profile") if isinstance(qualification, dict) else None
            params = feedback.get("activeParametersBySymbol", {}).get(symbol)
            if not isinstance(params, dict):
                params = qualification.get("parameters") if isinstance(qualification, dict) else None
            active_profile = selected if selected == approved_profile else "baseline"
            policy_by_symbol[symbol] = policy_for(active_profile, policy)
            if active_profile == approved_profile and isinstance(params, dict):
                from dataclasses import replace
                allowed = {key: value for key, value in params.items() if key in {
                    "intraday_strategy", "intraday_m3_min", "intraday_m6_min",
                    "intraday_m12_min", "intraday_m24_min", "minimum_net_edge"}}
                policy_by_symbol[symbol] = replace(policy_by_symbol[symbol], **allowed)
        report["strategy_by_symbol"] = {
            symbol: {"profile": profile_by_symbol[symbol],
                     **(feedback.get("activeParametersBySymbol", {}).get(symbol) or
                        approved_by_symbol.get(symbol, {}).get("parameters", {})
                        if isinstance(approved_by_symbol.get(symbol), dict) and
                        profile_by_symbol[symbol] != "momentum" else {})}
            for symbol in profile_by_symbol}
        profile = (next(iter(set(profile_by_symbol.values())))
                   if len(set(profile_by_symbol.values())) == 1 else "per_asset")
        policy = policy_by_symbol.get("SOL", policy)
    result = decide(context, portfolio, report, now=now, policy=policy)
    if intraday:
        result["activeBySymbol"] = profile_by_symbol
        result["adaptive"] = adaptive
        result["activeProfile"] = profile
        # The next profile is promoted only after three distinct, successful
        # completed-candle windows. It applies on the NEXT decision.
        result["adaptiveState"] = update_promotion(
            {**(request.get("adaptiveState") if isinstance(request.get("adaptiveState"), dict) else {}),
             "active": "baseline"}, adaptive,
            # The operator's idempotence marker is a completed 15m candle.
            # Short windows are diagnostic only; 12m history is the only
            # path that may approve a new strategy.
            window=int(now.timestamp() // 900),
        )
        result["adaptiveState"].update(feedback)
        actions_by_symbol = {row.get("symbol"): row for row in result.get("actions", [])}
        diagnostics = []
        for signal in signals:
            if signal.get("symbol") not in {"SOL", "ETH"}:
                continue
            m1, m3, m6, m12, m24 = (float(signal[key]) for key in (
                "momentum_1h", "momentum_3h", "momentum_6h",
                "momentum_12h", "momentum_24h"))
            base = {"momentum_1h_positive": m1 > 0,
                    "momentum_3h_min": m3 >= policy.intraday_m3_min,
                    "momentum_6h_min": m6 >= policy.intraday_m6_min,
                    "momentum_12h_min": m12 >= policy.intraday_m12_min,
                    "momentum_24h_min": m24 >= policy.intraday_m24_min}
            selected = actions_by_symbol.get(signal["symbol"])
            strategy = profile_by_symbol.get(signal["symbol"], "momentum")
            signal_policy = policy_by_symbol.get(signal["symbol"], policy)
            checks = dict(base)
            checks["momentum_3h_min"] = m3 >= signal_policy.intraday_m3_min
            checks["momentum_6h_min"] = m6 >= signal_policy.intraday_m6_min
            checks["momentum_12h_min"] = m12 >= signal_policy.intraday_m12_min
            checks["momentum_24h_min"] = m24 >= signal_policy.intraday_m24_min
            if strategy == "recovery":
                checks = {"recovery_24h_between_minus6_and_minus1": -.06 <= m24 <= -.01,
                          "recovery_3h_at_least_0_8pct": m3 >= .008,
                          "recovery_6h_at_least_0_3pct": m6 >= .003,
                          "recovery_1h_positive": m1 > 0}
            elif strategy == "breakout":
                checks["close_breaks_20_candle_high_by_0_2pct"] = signal.get("breakout_20", -1) >= .002
            elif strategy == "mean_reversion":
                checks = {"reversal_1h_positive": m1 > 0,
                          "reversal_3h_min": m3 >= policy.intraday_m3_min,
                          "drawdown_24h_bounded": m24 >= policy.intraday_m24_min,
                          "price_0_8pct_below_ema20": signal.get("ema20_gap", 0) <= -.008,
                          "rsi14_oversold": signal.get("rsi14", 100) <= 32}
            checks["median_dollar_volume_at_least_100k"] = (
                signal["median_15m_dollar_volume_usd"] >= 100_000)
            if strategy != "mean_reversion":
                checks["price_above_ema20"] = signal.get("above_ema20") is True
            if strategy == "recovery":
                strength = .60*m3 + .40*m6
            elif strategy == "breakout":
                strength = .40*signal.get("breakout_20", 0) + .35*m3 + .25*m6
            elif strategy == "mean_reversion":
                strength = .55*m1 + .45*m3
            elif strategy == "multi_trend":
                strength = .10*m1 + .20*m3 + .25*m6 + .25*m12 + .20*m24
            else:
                strength = .25*m3 + .40*m6 + .25*m12 + .10*m24
            checks["estimated_net_edge_after_costs"] = (
                strength >= signal_policy.minimum_net_edge + signal_policy.round_trip_cost_bps / 10_000)
            failed = [name for name, passed in checks.items() if not passed]
            diagnostics.append({"symbol": signal["symbol"],
                "observedAt": now.isoformat(),
                "referencePriceUsd": signal["last_close_usd"],
                "indicators": {key: signal.get(key) for key in (
                    "momentum_1h", "momentum_3h", "momentum_6h",
                    "momentum_12h", "momentum_24h", "above_ema20",
                    "ema20_gap", "breakout_20", "rsi14")},
                "strategySelected": selected.get("strategy") if selected else strategy,
                "learningEvidence": {
                    **qualification_diagnostics.get(signal["symbol"], {}),
                    **feedback.get("evidenceBySymbol", {}).get(signal["symbol"], {}),
                },
                "historicalStrategyApproval": (approved_by_symbol.get(signal["symbol"], {}).get("profile", "momentum")
                    if isinstance(approved_by_symbol.get(signal["symbol"]), dict) else "momentum"),
                "filters": checks,
                "entryEligible": signal["symbol"] in report.get("entry_eligible_symbols", [])
                    if request.get("enforceEntryQualification") is True else None,
                "reason": ("ORDER_PROPOSED" if selected else
                           "NO_VALIDATED_ENTRY_STRATEGY" if
                           request.get("enforceEntryQualification") is True and
                           signal["symbol"] not in report.get("entry_eligible_symbols", []) else
                           ",".join(failed) if failed else result.get("reason", "NO_ORDER"))})
        result["assetDiagnostics"] = diagnostics
    json.dump(result, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
