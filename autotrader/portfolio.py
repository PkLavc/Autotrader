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
    eligible = [f for f in forecasts if f.expected_return >= cfg.min_forecast_return]
    eligible.sort(key=lambda f: f.expected_return, reverse=True)
    chosen = eligible[: cfg.max_positions]
    if not chosen:
        return []

    investable = min(1.0 - cfg.cash_buffer, cfg.max_positions * cfg.max_weight_per_position)
    weight = min(cfg.max_weight_per_position, investable / len(chosen))
    return [Target(f.symbol, weight, f.expected_return) for f in chosen]
