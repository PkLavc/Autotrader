from __future__ import annotations

from dataclasses import replace
import math
import numpy as np
import pandas as pd

from autotrader.config import StrategyConfig
from autotrader.forecast import KronosEngine


def main() -> int:
    cfg = replace(StrategyConfig(), lookback=32, prediction_days=2, sample_count=1)
    engine = KronosEngine(cfg)

    dates = pd.bdate_range("2026-01-02", periods=32)
    close = np.linspace(100.0, 104.0, num=32)
    history = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.linspace(1_000_000, 1_100_000, num=32),
        },
        index=dates,
    )
    history["amount"] = history["close"] * history["volume"]
    forecast = engine.predict("SMOKE", history)
    assert math.isfinite(forecast.predicted_close)
    assert math.isfinite(forecast.expected_return)
    print(f"Kronos-base compatibility OK: predicted_close={forecast.predicted_close:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
