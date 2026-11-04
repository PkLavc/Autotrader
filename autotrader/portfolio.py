from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import math
import pandas as pd

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
    "GOOGL": "communication", "GOOG": "communication", "META": "communication", "NFLX": "communication",
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


# Dual share classes are one economic exposure, not separate companies.
ISSUER_ALIASES = {
    "GOOG": "ALPHABET", "GOOGL": "ALPHABET",
    "FOX": "FOX_CORP", "FOXA": "FOX_CORP",
    "NWS": "NEWS_CORP", "NWSA": "NEWS_CORP",
    "BRK-A": "BERKSHIRE", "BRK-B": "BERKSHIRE",
}


def issuer_of(symbol: str) -> str:
    return ISSUER_ALIASES.get(symbol, symbol)


def sector_of(symbol: str) -> str:
    return SECTORS.get(symbol, f"other:{symbol}")


@dataclass(frozen=True)
class Target:
    symbol: str
    weight: float
    score: float
    stop_distance_pct: float = 0.08


def _risk_profile(forecast: Forecast, cfg: StrategyConfig, history: pd.DataFrame | None) -> tuple[float, float] | None:
    volatility = forecast.annualized_volatility or 0.35
    if not math.isfinite(volatility) or volatility <= 0:
        return None
    atr_pct = volatility / math.sqrt(252.0)
    if history is not None:
        if len(history) < 60 or not {"close", "high", "low"}.issubset(history):
            return None
        close = pd.to_numeric(history["close"], errors="coerce")
        high = pd.to_numeric(history["high"], errors="coerce")
        low = pd.to_numeric(history["low"], errors="coerce")
        if not all(math.isfinite(float(x)) and float(x) > 0
                   for x in [close.iloc[-1], close.tail(50).mean(), high.iloc[-1], low.iloc[-1]]):
            return None
        ma20, ma50 = float(close.tail(20).mean()), float(close.tail(50).mean())
        if float(close.iloc[-1]) < ma50 or ma20 < ma50 * 0.995:
            return None
        returns = close.pct_change().tail(21).dropna()
        if len(returns) < 20:
            return None
        volatility = float(returns.std(ddof=1)) * math.sqrt(252)
        if not math.isfinite(volatility) or volatility > cfg.max_annualized_volatility:
            return None
        prev = close.shift(1)
        true_range = pd.concat([(high-low).abs(), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
        atr_pct = float(true_range.tail(14).mean()) / float(close.iloc[-1])
        if not math.isfinite(atr_pct) or atr_pct <= 0:
            return None
    distance = max(cfg.min_stop_distance_pct, min(cfg.max_stop_distance_pct, 2.0 * atr_pct))
    horizon_vol = max(volatility * math.sqrt(cfg.prediction_days / 252.0), 0.01)
    return distance, horizon_vol


def choose_targets(
    forecasts: Iterable[Forecast],
    cfg: StrategyConfig,
    *,
    held_symbols: Iterable[str] = (),
    sector_map: dict[str, str] | None = None,
    sector_multipliers: dict[str, float] | None = None,
    histories: dict[str, pd.DataFrame] | None = None,
    excluded_symbols: Iterable[str] = (),
) -> list[Target]:
    """One risk-budgeted strategy: trend + edge + volatility + issuer diversification."""
    held, excluded = set(held_symbols), set(excluded_symbols)
    sector_map = sector_map or {}
    sector_multipliers = sector_multipliers or {}
    profiles = {}
    eligible = []
    for forecast in forecasts:
        if forecast.symbol in excluded or not math.isfinite(forecast.expected_return):
            continue
        if not cfg.min_forecast_return <= forecast.expected_return <= cfg.max_abs_forecast_return:
            continue
        history = histories.get(forecast.symbol) if histories is not None else None
        if histories is not None and history is None:
            continue
        profile = _risk_profile(forecast, cfg, history)
        if profile is None or forecast.expected_return / profile[1] < cfg.min_risk_adjusted_signal:
            continue
        profiles[forecast.symbol] = profile
        eligible.append(forecast)

    def score(forecast: Forecast) -> float:
        adjusted = forecast.expected_return / profiles[forecast.symbol][1]
        if forecast.symbol in held:
            adjusted *= 1.0 + cfg.holding_score_bonus
        sector = sector_map.get(forecast.symbol, sector_of(forecast.symbol))
        return adjusted * min(1.05, max(0.95, sector_multipliers.get(sector, 1.0)))

    eligible.sort(key=lambda f: (-score(f), f.symbol))
    chosen = []
    issuers = set()
    sectors = {}
    for forecast in eligible:
        sector = sector_map.get(forecast.symbol, sector_of(forecast.symbol))
        issuer = issuer_of(forecast.symbol)
        if issuer in issuers or sectors.get(sector, 0) >= cfg.max_positions_per_sector:
            continue
        chosen.append(forecast)
        issuers.add(issuer)
        sectors[sector] = sectors.get(sector, 0) + 1
        if len(chosen) >= cfg.max_positions:
            break

    remaining = min(1.0 - cfg.cash_buffer, cfg.max_positions * cfg.max_weight_per_position)
    targets = []
    for forecast in chosen:
        distance, _ = profiles[forecast.symbol]
        weight = min(remaining, cfg.max_weight_per_position, cfg.risk_per_trade / distance)
        if weight < 0.001:
            continue
        targets.append(Target(forecast.symbol, weight, score(forecast), distance))
        remaining -= weight
    return targets
