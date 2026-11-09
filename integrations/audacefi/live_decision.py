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
from statistics import median
from urllib.request import Request, urlopen

from autotrader.data import load_daily_history
from .crypto_research import completed_crypto_bars
from .league_strategy import LeaguePolicy, decide
from .adaptive_learning import evaluate, policy_for, update_promotion


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
    close = values[latest][0]
    moves = {f"momentum_{label}": close / values[latest - 900 * periods][0] - 1
             for label, periods in (("1h", 4), ("3h", 12), ("6h", 24),
                                    ("12h", 48), ("24h", 96))}
    ema = values[latest - 900 * 20][0]
    for step in range(19, -1, -1):
        ema = (2 / 21) * values[latest - 900 * step][0] + (19 / 21) * ema
    volume = median(values[latest - 900 * step][0] * values[latest - 900 * step][1]
                    for step in range(96))
    return {"symbol": symbol, "last_close_usd": close,
            "median_15m_dollar_volume_usd": volume,
            "above_ema20": close > ema, **moves,
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
        if not isinstance(profile, str) or profile not in {"baseline", "early", "steady", "breakout"}:
            raise ValueError("INVALID_ADAPTIVE_PROFILE")
        policy = policy_for(profile, policy)
    result = decide(context, portfolio, report, now=now, policy=policy)
    if intraday:
        result["adaptive"] = adaptive
        result["activeProfile"] = profile
        # The next profile is promoted only after three distinct, successful
        # completed-candle windows. It applies on the NEXT decision.
        result["adaptiveState"] = update_promotion(
            request.get("adaptiveState"), adaptive,
            window=int(now.timestamp() // 900),
        )
    json.dump(result, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
