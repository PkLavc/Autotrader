"""Deterministic sector-balanced, liquidity-only pre-screen before costly Kronos inference.

This stage never sees future prices or forecasts. It exists to bound CPU costs while
broadening the *monitored* market universe; it is not an alpha model.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import math

import pandas as pd

from .config import StrategyConfig
from .portfolio import sector_of


def _liquidity(history: pd.DataFrame) -> float:
    if history.empty or not {"close", "volume"}.issubset(history.columns):
        return 0.0
    dollar_volume = (history["close"] * history["volume"]).tail(20)
    dollar_volume = pd.to_numeric(dollar_volume, errors="coerce")
    dollar_volume = dollar_volume.replace([float("inf"), -float("inf")], float("nan")).dropna()
    if len(dollar_volume) < 5:
        return 0.0
    value = float(dollar_volume.median())
    return value if math.isfinite(value) and value > 0 else 0.0


def select_forecast_symbols(
    histories: dict[str, pd.DataFrame],
    cfg: StrategyConfig,
    *,
    priority_symbols: Iterable[str] = (),
    sector_map: dict[str, str] | None = None,
) -> list[str]:
    """Preserve held/pending assets, then fill slots across sectors by liquidity.

    SPY is always first when available, so an unforecasted market benchmark cannot
    silently vanish from the selected candidate set.
    """
    limit = cfg.max_forecast_assets
    if limit < cfg.max_positions or limit < 1:
        raise ValueError("max_forecast_assets must cover at least max_positions")

    available = set(histories)
    sector_map = sector_map or {}
    result: list[str] = []
    for symbol in (cfg.benchmark_symbol, *priority_symbols):
        if symbol in available and symbol not in result:
            result.append(symbol)
    if len(result) > limit:
        raise ValueError("more held/pending securities than forecast capacity")

    groups: dict[str, list[str]] = defaultdict(list)
    for symbol in sorted(available - set(result)):
        groups[sector_map.get(symbol, sector_of(symbol))].append(symbol)
    for symbols in groups.values():
        symbols.sort(key=lambda symbol: (-_liquidity(histories[symbol]), symbol))

    # Round-robin gives smaller non-technology sectors access to the forecast
    # budget before technology-heavy symbols consume every CPU slot.
    while len(result) < limit and any(groups.values()):
        sectors = sorted(groups, key=lambda key: (-_liquidity(histories[groups[key][0]]), key)
                         if groups[key] else (float("inf"), key))
        for sector in sectors:
            if groups[sector] and len(result) < limit:
                result.append(groups[sector].pop(0))
    return result
