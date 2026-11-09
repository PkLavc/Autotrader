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

from autotrader.data import load_daily_history
from .crypto_research import completed_crypto_bars
from .league_strategy import decide


def main() -> int:
    request = json.load(sys.stdin)
    context = request["context"]
    portfolio = request["portfolio"]
    now = datetime.now(timezone.utc)
    report: dict = {}
    if request.get("fetchSignals") is True:
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
    decision = decide(context, portfolio, report, now=now)
    json.dump(decision, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
