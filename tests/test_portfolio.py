from autotrader.config import StrategyConfig
from autotrader.forecast import Forecast
from autotrader.portfolio import choose_targets


def test_targets_respect_caps():
    cfg = StrategyConfig(max_positions=3, max_weight_per_position=0.2, cash_buffer=0.1, min_forecast_return=0.01)
    forecasts = [
        Forecast("A", 100, 110, 0.10),
        Forecast("B", 100, 108, 0.08),
        Forecast("C", 100, 105, 0.05),
        Forecast("D", 100, 99, -0.01),
    ]
    targets = choose_targets(forecasts, cfg)
    assert [t.symbol for t in targets] == ["A", "B", "C"]
    assert all(t.weight <= 0.2 for t in targets)
    assert sum(t.weight for t in targets) <= 0.9
