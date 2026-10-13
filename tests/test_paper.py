from autotrader.config import StrategyConfig
import pandas as pd

from autotrader.paper import (
    PaperState,
    apply_corporate_actions,
    mark_to_market,
    performance_metrics,
    rebalance,
)
from autotrader.portfolio import Target


def _state(cash: float = 1000.0) -> PaperState:
    return PaperState(cash=cash, positions={}, nav_history=[], trades=[], last_prices={})


def test_rebalance_never_spends_more_than_cash():
    cfg = StrategyConfig(max_positions=2, max_weight_per_position=0.4, cash_buffer=0.2, rebalance_threshold=0)
    state = _state()
    prices = {"A": 100.0, "B": 50.0}
    trades = rebalance(state, [Target("A", 0.4, 0.1), Target("B", 0.4, 0.08)], prices, cfg)
    assert state.cash >= -1e-8
    assert mark_to_market(state, prices) > 990
    assert len(trades) == 2


def test_missing_current_quote_uses_last_known_mark():
    state = _state(500.0)
    state.positions = {"A": 5.0}
    state.last_prices = {"A": 100.0}
    assert mark_to_market(state, {}) == 1000.0


def test_metrics_include_return_and_drawdown():
    cfg = StrategyConfig(initial_cash=1000.0, benchmark_symbol="SPY")
    state = _state()
    state.nav_history = [
        {"timestamp": "1", "nav": 1000.0, "cash": 1000.0},
        {"timestamp": "2", "nav": 900.0, "cash": 900.0},
        {"timestamp": "3", "nav": 1100.0, "cash": 1100.0},
    ]
    state.benchmark_start_price = 100.0
    state.benchmark_value = 1.05
    metrics = performance_metrics(state, cfg, {"SPY": 105.0})
    assert round(metrics["total_return"], 6) == 0.1
    assert round(metrics["max_drawdown"], 6) == -0.1
    assert round(metrics["benchmark_return"], 6) == 0.05


def test_rebalance_records_execution_model_and_costs():
    cfg = StrategyConfig(
        initial_cash=1000.0,
        max_positions=1,
        max_weight_per_position=0.5,
        cash_buffer=0.5,
        rebalance_threshold=0,
        slippage_bps=10,
        commission_per_share=0,
        minimum_commission=0,
    )
    state = _state()
    trades = rebalance(
        state, [Target("A", 0.5, 1.0)], {"A": 100.0}, cfg,
        signal_date="2026-10-12", execution_date="2026-10-13",
        price_source="session_open",
    )
    assert len(trades) == 1
    assert trades[0]["price_source"] == "session_open"
    assert trades[0]["signal_date"] == "2026-10-12"
    assert trades[0]["commission"] == 0
    assert state.cumulative_costs["slippage"] > 0
    assert state.cash >= 0


def test_sell_fee_estimate_uses_current_sec_taf_and_cat_schedule():
    cfg = StrategyConfig(slippage_bps=0, rebalance_threshold=0)
    state = _state()
    state.positions = {"A": 10}
    state.tax_state["cost_basis"] = {"A": {"qty": 10, "cost": 800}}
    trades = rebalance(state, [], {"A": 100}, cfg, execution_date="2026-10-13")
    trade = trades[0]
    assert trade["sec_fee"] == 0.03
    assert trade["taf_fee"] == 0.01
    assert trade["cat_fee"] == 0.01
    assert trade["realized_gain"] > 199
    assert state.cash == 1999.95


def test_metrics_do_not_report_unknown_tax_as_zero_tax_due():
    cfg = StrategyConfig(initial_cash=1000)
    state = _state()
    metrics = performance_metrics(state, cfg, {})
    assert metrics["tax_estimate"]["status"] == "not_calculated_user_specific"
    assert "not modeled" in metrics["tax_estimate"]["note"]


def test_corporate_actions_adjust_quantity_and_credit_dividend_once():
    state = _state(500.0)
    state.positions = {"A": 2.0}
    history = pd.DataFrame(
        {
            "stock_splits": [0.0, 2.0],
            "dividends": [0.0, 1.25],
        },
        index=pd.to_datetime(["2026-10-12", "2026-10-13"]),
    )
    first = apply_corporate_actions(state, {"A": history}, "2026-10-12", "2026-10-13")
    second = apply_corporate_actions(state, {"A": history}, "2026-10-12", "2026-10-13")
    assert len(first) == 2
    assert second == []
    assert state.positions["A"] == 4.0
    assert state.cash == 505.0
