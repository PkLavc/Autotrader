from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from autotrader.config import StrategyConfig
from autotrader.discovery import discover_market, parse_constituents
from autotrader.forecast import Forecast
from autotrader.learning import (
    load_journal, record_signals, resolve_outcomes, save_journal, sector_factors,
)
from autotrader.portfolio import Target, choose_targets, sector_of
from autotrader.universe import select_forecast_symbols


def _feed(size: int = 490) -> str:
    sectors = ["Financials", "Energy", "Health Care", "Information Technology",
               "Consumer Staples", "Industrials", "Utilities", "Materials", "Real Estate"]
    rows = ["Symbol,Security,GICS Sector"]
    for i in range(size):
        rows.append(f"X{i:04d},Company {i},{sectors[i % len(sectors)]}")
    return "\n".join(rows)


def test_discovery_csv_rejects_incomplete_or_unsupported_feed():
    with pytest.raises(ValueError, match="outside safe bounds"):
        parse_constituents(_feed(399))
    constituents = parse_constituents(_feed())
    assert len(constituents) == 490
    assert constituents["X0000"] == "financials"


def test_discovery_changes_automatically_and_preserves_base_watchlist():
    cfg = StrategyConfig(daily_discovery_count=20)
    constituents = parse_constituents(_feed())
    day1, sectors, meta = discover_market("2026-10-22", cfg, constituents=constituents)
    day2, _, meta2 = discover_market("2026-10-23", cfg, constituents=constituents)
    assert len(day1) == len(cfg.symbols) + 20
    assert set(cfg.symbols).issubset(day1)
    assert len(set(day1)) == len(day1)
    assert meta["candidate_pool_size"] >= 450
    assert meta["discovered_today"] != meta2["discovered_today"]
    assert all(sectors[symbol] in {
        "financials", "energy", "healthcare", "technology", "staples",
        "industrials", "utilities", "materials", "real_estate",
    } for symbol in meta["discovered_today"])


def test_discovery_fail_closed_when_external_feed_missing():
    cfg = StrategyConfig()
    symbols, sectors, meta = discover_market("2026-10-22", cfg, constituents={})
    assert tuple(symbols) == cfg.symbols
    assert meta["discovered_today"] == []
    assert sectors["SPY"] == "etf"


def _frame(values: list[float], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"close": values, "open": values, "high": values,
                         "low": values, "volume": [1_000_000] * len(values)}, index=index)


def test_outcomes_resolve_only_after_five_market_sessions(tmp_path):
    cfg = StrategyConfig(prediction_days=5)
    dates = pd.bdate_range("2026-10-05", periods=7)
    stock = _frame([100, 101, 102, 103, 104, 110, 109], dates)
    spy = _frame([100, 100, 100, 100, 100, 102, 101], dates)
    journal = load_journal(str(tmp_path / "learning.json"))
    forecast = Forecast("AAPL", 100, 108, 0.08, 0.20)
    targets = [Target("AAPL", 0.18, 1.0)]
    assert record_signals(journal, targets, [forecast], "2026-10-05", {"AAPL": "technology"}) == 1
    assert record_signals(journal, targets, [forecast], "2026-10-05", {"AAPL": "technology"}) == 0
    early = resolve_outcomes(journal, {"AAPL": stock.iloc[:5]}, spy.iloc[:5], "2026-10-09", cfg)
    assert early["matured_today"] == 0
    assert sector_factors(journal, cfg) == {}
    later = resolve_outcomes(journal, {"AAPL": stock.iloc[:6]}, spy.iloc[:6], "2026-10-12", cfg)
    assert later["matured_today"] == 1
    assert journal["completed"][0]["actual_return"] == pytest.approx(.10)
    assert journal["completed"][0]["excess_return"] == pytest.approx(.08)
    assert journal["pending"] == []
    assert resolve_outcomes(journal, {"AAPL": stock}, spy, "2026-10-13", cfg)["matured_today"] == 0
    save_journal(journal, str(tmp_path / "learning.json"))
    assert len(load_journal(str(tmp_path / "learning.json"))["completed"]) == 1


def test_adaptation_is_neutral_until_enough_valid_completed_forecasts():
    cfg = StrategyConfig(adaptive_min_total_evaluations=40,
                         adaptive_min_sector_evaluations=12)
    journal = {"pending": [], "completed": [
        {"sector": "technology", "excess_return": 0.03} for _ in range(39)
    ]}
    assert sector_factors(journal, cfg) == {}
    journal["completed"].append({"sector": "healthcare", "excess_return": -0.01})
    factors = sector_factors(journal, cfg)
    assert 1 < factors["technology"] <= 1.05
    assert "healthcare" not in factors
    signals = [Forecast("AAPL", 100, 110, 0.10, 0.20),
               Forecast("JNJ", 100, 109, 0.09, 0.20)]
    targets = choose_targets(signals, StrategyConfig(max_positions=2),
                             sector_multipliers={"technology": 99,
                                                 "healthcare": -99})
    assert len(targets) == 2
    assert all(target.weight <= .18 for target in targets)


def test_new_symbols_are_sector_capped_and_scoped_when_inference_selected():
    cfg = StrategyConfig(max_forecast_assets=5)
    index = pd.bdate_range("2025-01-01", periods=400)
    histories = {
        s: _frame([100] * len(index), index)
        for s in ("SPY", "X0001", "X0002", "AAPL", "MSFT", "DIS")
    }
    sectors = {"X0001": "technology", "X0002": "financials"}
    chosen = select_forecast_symbols(histories, cfg, sector_map=sectors,
                                     priority_symbols=("X0001",),
                                     universe_symbols=("SPY", "X0001", "X0002", "AAPL", "MSFT"))
    assert "DIS" not in chosen
    assert "X0001" in chosen
    assert len(chosen) == 5
