from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
import math
import sys

import pandas as pd

from .config import StrategyConfig


@dataclass(frozen=True)
class Forecast:
    symbol: str
    current_close: float
    predicted_close: float
    expected_return: float


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
        future = pd.bdate_range(last_ts + pd.offsets.BDay(1), periods=self.cfg.prediction_days)
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
        return Forecast(symbol, current, predicted, expected_return)
