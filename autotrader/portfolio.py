from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import StrategyConfig
from .forecast import Forecast


# Broad, stable groups. Unknown symbols deliberately get their own group rather
# than all being silently classified as one sector.
SECTORS = {
    "SPY": "etf", "QQQ": "etf", "IWM": "etf",
    "AAPL": "technology", "MSFT": "technology", "NVDA": "technology",
    "AVGO": "technology", "AMD": "technology", "CRM": "technology",
    "ORCL": "technology", "ADBE": "technology", "TXN": "technology",
    "AMZN": "discretionary", "HD": "discretionary", "MCD": "discretionary",
    "GOOGL": "communication", "META": "communication", "NFLX": "communication",
    "DIS": "communication", "T": "communication",
    "JPM": "financials", "V": "financials", "MA": "financials",
    "BAC": "financials", "GS": "financials", "BRK-B": "financials",
    "LLY": "healthcare", "UNH": "healthcare", "JNJ": "healthcare",
    "ABBV": "healthcare", "MRK": "healthcare",
    "COST": "staples", "WMT": "staples", "PG": "staples",
    "KO": "staples", "PEP": "staples",
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    "CAT": "industrials", "DE": "industrials", "GE": "industrials",
    "UNP": "industrials",
    "NEE": "utilities", "DUK": "utilities",
    "LIN": "materials", "SHW": "materials",
    "PLD": "real_estate", "AMT": "real_estate",
}


def sector_of(symbol: str) -> str:
    return SECTORS.get(symbol, f"other:{symbol}")


@dataclass(frozen=True)
class Target:
    symbol: str
    weight: float
    score: float


def choose_targets(
    forecasts: Iterable[Forecast],
    cfg: StrategyConfig,
    *,
    held_symbols: Iterable[str] = (),
) -> list[Target]:
    """Diversify new targets and give *eligible* existing holdings modest inertia.

    Sector caps and retention apply to signal ordering only, never override the
    profitability floor, plausibility limit or portfolio cash/position caps.
    """
    held = set(held_symbols)
    eligible = [
        f for f in forecasts
        if cfg.min_forecast_return <= f.expected_return <= cfg.max_abs_forecast_return
    ]
    horizon_scale = (cfg.prediction_days / 252.0) ** 0.5

    def score(forecast: Forecast) -> float:
        horizon_vol = (forecast.annualized_volatility or 0.0) * horizon_scale
        risk_adjusted = forecast.expected_return / max(horizon_vol, 0.01)
        if forecast.symbol in held:
            risk_adjusted *= (1.0 + cfg.holding_score_bonus)
        return risk_adjusted

    eligible.sort(key=lambda f: (-score(f), f.symbol))
    chosen: list[Forecast] = []
    counts: dict[str, int] = {}
    for forecast in eligible:
        sector = sector_of(forecast.symbol)
        if counts.get(sector, 0) >= cfg.max_positions_per_sector:
            continue
        chosen.append(forecast)
        counts[sector] = counts.get(sector, 0) + 1
        if len(chosen) == cfg.max_positions:
            break

    if not chosen:
        return []

    investable = min(1.0 - cfg.cash_buffer, cfg.max_positions * cfg.max_weight_per_position)
    weight = min(cfg.max_weight_per_position, investable / len(chosen))
    return [Target(f.symbol, weight, score(f)) for f in chosen]
