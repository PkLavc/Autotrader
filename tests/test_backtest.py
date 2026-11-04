import pytest

from autotrader.backtest import walk_forward
from autotrader.config import StrategyConfig


def _session(market_date, open_price, close_price, predicted_return):
    prices = {
        "SPY": {"open": open_price, "close": close_price},
        "AAPL": {"open": open_price, "close": close_price},
    }
    forecasts = [
        {
            "symbol": "AAPL",
            "current_close": close_price,
            "predicted_close": close_price * (1 + predicted_return),
            "expected_return": predicted_return,
            "annualized_volatility": 0.20,
        }
    ]
    return {"market_date": market_date, "prices": prices, "forecasts": forecasts}


def test_walk_forward_never_fills_signal_on_same_close():
    cfg = StrategyConfig(
        initial_cash=1000,
        max_positions=1,
        max_weight_per_position=0.5,
        cash_buffer=0.5,
        min_forecast_return=0.01,
        rebalance_threshold=0,
    )
    result = walk_forward(
        [
            _session("2026-10-12", 100, 101, 0.05),
            _session("2026-10-13", 110, 111, 0.05),
        ],
        cfg,
    )
    assert result["signals"][0]["executed_trades"] == 0
    assert result["signals"][1]["executed_trades"] == 1
    assert result["trades"][0]["reference_price"] == 110
    assert result["trades"][0]["signal_date"] == "2026-10-12"


def test_walk_forward_rejects_duplicate_sessions():
    record = _session("2026-10-12", 100, 101, 0.05)
    with pytest.raises(ValueError, match="unique and increasing"):
        walk_forward([record, record], StrategyConfig())


def test_walk_forward_trails_stops_without_future_close_lookahead():
    cfg = StrategyConfig(initial_cash=1000, max_positions=1, max_weight_per_position=.5,
                         cash_buffer=.5, rebalance_threshold=0, slippage_bps=0)
    records = [
        _session("2026-10-12", 100, 101, .10),
        _session("2026-10-13", 102, 120, .10),
        _session("2026-10-14", 108, 108, .10),
    ]
    result = walk_forward(records, cfg)
    assert any(t["side"] == "SELL" and t["execution_date"] == "2026-10-14"
               for t in result["trades"])
    assert result["validation_level"] == "execution_only_without_point_in_time_ohlc_filters"
