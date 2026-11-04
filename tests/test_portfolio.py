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


def test_targets_reject_implausible_forecasts_and_rank_by_risk():
    cfg = StrategyConfig(max_positions=2, max_abs_forecast_return=0.30)
    forecasts = [
        Forecast("WILD", 100, 150, 0.50, 0.20),
        Forecast("CALM", 100, 106, 0.06, 0.10),
        Forecast("RISKY", 100, 108, 0.08, 0.80),
    ]
    targets = choose_targets(forecasts, cfg)
    assert [target.symbol for target in targets] == ["CALM", "RISKY"]
    assert "WILD" not in [target.symbol for target in targets]


def test_dual_share_classes_are_one_issuer_even_when_sector_map_differs():
    cfg = StrategyConfig(max_positions=3, max_positions_per_sector=3)
    forecasts = [
        Forecast("GOOG", 100, 112, 0.12, 0.2),
        Forecast("GOOGL", 100, 111, 0.11, 0.2),
        Forecast("MCD", 100, 110, 0.10, 0.2),
        Forecast("GE", 100, 109, 0.09, 0.2),
    ]
    targets = choose_targets(
        forecasts, cfg,
        sector_map={"GOOG": "tech_a", "GOOGL": "tech_b"},
    )
    symbols = {t.symbol for t in targets}
    assert len(symbols & {"GOOG", "GOOGL"}) == 1
    assert len(targets) == 3
