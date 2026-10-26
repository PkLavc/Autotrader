from __future__ import annotations

import pandas as pd
import pytest

from autotrader.config import StrategyConfig
from autotrader.forecast import Forecast
from autotrader.portfolio import SECTORS, choose_targets, sector_of
from autotrader.universe import select_forecast_symbols


def _hist(volume: int) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-01", periods=400)
    return pd.DataFrame({"close": [100.0] * 400,
                         "volume": [volume] * 400}, index=index)


def test_expanded_universe_has_cross_sector_coverage():
    cfg = StrategyConfig()
    assert len(cfg.symbols) >= 40
    assert len(cfg.symbols) == len(set(cfg.symbols))
    assert set(cfg.symbols).issubset(SECTORS)
    assert len({sector_of(symbol) for symbol in cfg.symbols}) >= 9
    assert cfg.max_forecast_assets < len(cfg.symbols)


def test_forecast_screen_is_bounded_diverse_deterministic_and_keeps_holdings():
    cfg = StrategyConfig(max_forecast_assets=8)
    histories = {symbol: _hist((len(cfg.symbols) - index) * 1000)
                 for index, symbol in enumerate(cfg.symbols)}
    selected = select_forecast_symbols(
        histories, cfg, priority_symbols=("AVGO", "DIS", "AVGO"),
    )
    assert selected[:3] == ["SPY", "AVGO", "DIS"]
    assert len(selected) == 8
    assert len(set(selected)) == 8
    assert len({sector_of(s) for s in selected}) >= 4
    assert selected == select_forecast_symbols(
        histories, cfg, priority_symbols=("AVGO", "DIS", "AVGO"),
    )


def test_forecast_screen_never_uses_missing_symbols():
    cfg = StrategyConfig(max_forecast_assets=5)
    selected = select_forecast_symbols(
        {"SPY": _hist(1000), "AAPL": _hist(1000)}, cfg, priority_symbols=("NFLX",)
    )
    assert set(selected) == {"SPY", "AAPL"}


def test_forecast_screen_requires_capacity_for_max_positions():
    cfg = StrategyConfig(max_positions=6, max_forecast_assets=5)
    with pytest.raises(ValueError, match="max_forecast_assets"):
        select_forecast_symbols({"SPY": _hist(1000)}, cfg)


def test_target_selection_caps_each_sector():
    cfg = StrategyConfig(max_positions=4, max_positions_per_sector=2)
    forecasts = [
        Forecast("AAPL", 100, 110, 0.10, 0.20),
        Forecast("MSFT", 100, 109, 0.09, 0.20),
        Forecast("AMD", 100, 108, 0.08, 0.20),
        Forecast("JPM", 100, 107, 0.07, 0.20),
        Forecast("JNJ", 100, 106, 0.06, 0.20),
    ]
    targets = choose_targets(forecasts, cfg)
    assert len(targets) == 4
    assert sum(sector_of(t.symbol) == "technology" for t in targets) == 2
    assert all(t.weight <= cfg.max_weight_per_position for t in targets)


def test_holding_inertia_only_prefers_still_eligible_signals():
    cfg = StrategyConfig(max_positions=1, holding_score_bonus=0.08)
    signals = [
        Forecast("JNJ", 100, 110, 0.10, 0.20),
        Forecast("JPM", 100, 109.9, 0.099, 0.20),
    ]
    assert choose_targets(signals, cfg)[0].symbol == "JNJ"
    assert choose_targets(signals, cfg, held_symbols=("JPM",))[0].symbol == "JPM"
    dropped = [signals[0], Forecast("JPM", 100, 95, -0.05, 0.20)]
    assert choose_targets(dropped, cfg, held_symbols=("JPM",))[0].symbol == "JNJ"
