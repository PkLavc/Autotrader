from __future__ import annotations

import importlib
from dataclasses import dataclass
import hashlib
from pathlib import Path
import math
import random
import sys

import numpy as np
import pandas as pd

from .calendar import next_nyse_sessions
from .config import StrategyConfig


@dataclass(frozen=True)
class Forecast:
    symbol: str
    current_close: float
    predicted_close: float
    expected_return: float
    annualized_volatility: float | None = None


class KronosEngine:
    def __init__(self, cfg: StrategyConfig, kronos_dir: str = "vendor/Kronos") -> None:
        root = str(Path(kronos_dir).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        model_module = importlib.import_module("model")
        tokenizer = model_module.KronosTokenizer.from_pretrained(
            cfg.tokenizer_name,
            revision=cfg.tokenizer_revision,
        )
        model = model_module.Kronos.from_pretrained(
            cfg.model_name,
            revision=cfg.model_revision,
        )
        tokenizer.eval()
        model.eval()
        self.predictor = model_module.KronosPredictor(model, tokenizer, device="cpu", max_context=512)
        self.cfg = cfg

    def predict(self, symbol: str, history: pd.DataFrame) -> Forecast:
        last_ts = pd.Timestamp(history.index[-1])
        seed_material = f"{self.cfg.random_seed}:{symbol}:{last_ts.date().isoformat()}"
        seed = int.from_bytes(hashlib.sha256(seed_material.encode("utf-8")).digest()[:4], "big")
        random.seed(seed)
        np.random.seed(seed)
        try:
            torch = importlib.import_module("torch")
            torch.manual_seed(seed)
        except ImportError:
            pass
        future = next_nyse_sessions(last_ts, self.cfg.prediction_days)
        pred = self.predictor.predict(
            df=history[["open", "high", "low", "close", "volume", "amount"]],
            x_timestamp=pd.Series(history.index),
            y_timestamp=pd.Series(future),
            pred_len=self.cfg.prediction_days,
            T=self.cfg.temperature,
            top_p=self.cfg.top_p,
            sample_count=self.cfg.sample_count,
            verbose=False,
        )
        current = float(history["close"].iloc[-1])
        predicted = float(pred["close"].iloc[-1])
        expected_return = (predicted / current) - 1.0
        if not all(math.isfinite(v) for v in (current, predicted, expected_return)):
            raise RuntimeError(f"{symbol}: Kronos returned a non-finite forecast")
        returns = history["close"].pct_change().dropna().tail(60)
        volatility = float(returns.std(ddof=1) * math.sqrt(252)) if len(returns) >= 20 else None
        if volatility is not None and not math.isfinite(volatility):
            volatility = None
        return Forecast(symbol, current, predicted, expected_return, volatility)
