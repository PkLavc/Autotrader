from autotrader.config import StrategyConfig
from autotrader.paper import PaperState, mark_to_market, performance_metrics, rebalance
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
    metrics = performance_metrics(state, cfg, {"SPY": 105.0})
    assert round(metrics["total_return"], 6) == 0.1
    assert round(metrics["max_drawdown"], 6) == -0.1
    assert round(metrics["benchmark_return"], 6) == 0.05
