"""Optional, manual Kronos crypto research; separate from equity ledgers."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import random

import numpy as np
import pandas as pd

from autotrader.config import StrategyConfig
from autotrader.data import load_daily_history
from autotrader.forecast import KronosEngine

TICKERS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}


def crypto_future_dates(last_session: pd.Timestamp, days: int) -> pd.DatetimeIndex:
    """Crypto trades seven days a week; never use an NYSE-only horizon."""
    if not 1 <= days <= 30:
        raise ValueError("invalid prediction horizon")
    first = pd.Timestamp(last_session).normalize() + pd.Timedelta(days=1)
    return pd.date_range(first, periods=days, freq="D")


def completed_crypto_bars(history: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Discard the currently forming UTC daily candle."""
    if now.tzinfo is None:
        raise ValueError("timezone-aware now required")
    today = now.astimezone(timezone.utc).date()
    return history.loc[pd.DatetimeIndex(history.index).date < today].copy()


class CryptoKronosEngine(KronosEngine):
    def predict_crypto(self, ticker: str, history: pd.DataFrame) -> float:
        last = pd.Timestamp(history.index[-1])
        seed_input = f"{self.cfg.random_seed}:{ticker}:{last.date().isoformat()}:crypto"
        seed = int.from_bytes(hashlib.sha256(seed_input.encode()).digest()[:4], "big")
        random.seed(seed)
        np.random.seed(seed)
        try:
            importlib.import_module("torch").manual_seed(seed)
        except ImportError:
            pass
        result = self.predictor.predict(
            df=history[["open", "high", "low", "close", "volume", "amount"]],
            x_timestamp=pd.Series(history.index),
            y_timestamp=pd.Series(crypto_future_dates(last, self.cfg.prediction_days)),
            pred_len=self.cfg.prediction_days,
            T=self.cfg.temperature,
            top_p=self.cfg.top_p,
            sample_count=self.cfg.sample_count,
            verbose=False,
        )
        current = float(history["close"].iloc[-1])
        predicted = float(result["close"].iloc[-1])
        if not math.isfinite(current) or not math.isfinite(predicted) or min(current, predicted) <= 0:
            raise ValueError("INVALID_CRYPTO_FORECAST")
        return predicted / current - 1


def run_forecasts(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = StrategyConfig()
    histories = {}
    for symbol, ticker in TICKERS.items():
        raw = load_daily_history(ticker, cfg.lookback + 2)
        closed = completed_crypto_bars(raw, now).tail(cfg.lookback)
        if len(closed) < cfg.lookback:
            raise RuntimeError("INSUFFICIENT_COMPLETED_HISTORY:" + ticker)
        histories[symbol] = closed
    engine = CryptoKronosEngine(cfg)
    signals = [
        {"symbol": symbol, "expected_return": engine.predict_crypto(ticker, histories[symbol])}
        for symbol, ticker in TICKERS.items()
    ]
    return {
        "asset_class": "crypto",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": cfg.model_name,
        "data_source": "Yahoo Finance crypto daily UTC completed candles",
        "signals": signals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Unscheduled crypto-only Kronos signals")
    parser.add_argument("--output", required=True, help="Private *.local.json file; never overwrite")
    args = parser.parse_args()
    report = run_forecasts()
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(json.dumps({"status": "CRYPTO_SIGNALS_READY", "signal_count": len(report["signals"]), "output": str(dest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
