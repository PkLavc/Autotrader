from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import StrategyConfig
from .forecast import Forecast


@dataclass(frozen=True)
class Target:
    symbol: str
    weight: float
    score: float


def choose_targets(forecasts: Iterable[Forecast], cfg: StrategyConfig) -> list[Target]:
    eligible = [
        f for f in forecasts
        if cfg.min_forecast_return <= f.expected_return <= cfg.max_abs_forecast_return
    ]
    horizon_scale = (cfg.prediction_days / 252.0) ** 0.5

    def score(forecast: Forecast) -> float:
        horizon_vol = (forecast.annualized_volatility or 0.0) * horizon_scale
        return forecast.expected_return / max(horizon_vol, 0.01)

    eligible.sort(key=score, reverse=True)
    chosen = eligible[: cfg.max_positions]
    if not chosen:
        return []

    investable = min(1.0 - cfg.cash_buffer, cfg.max_positions * cfg.max_weight_per_position)
    weight = min(cfg.max_weight_per_position, investable / len(chosen))
    return [Target(f.symbol, weight, score(f)) for f in chosen]
