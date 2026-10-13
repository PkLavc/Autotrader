import pytest

from autotrader.broker import BrokerLimits, BrokerPosition, plan_rebalance, validate_targets


def test_broker_plan_is_long_only_cash_limited_and_sells_first():
    limits = BrokerLimits(max_order_notional=100, max_total_notional=180)
    positions = [BrokerPosition("OLD", 10, 1000, 100)]
    orders = plan_rebalance(
        equity=2000,
        cash=80,
        positions=positions,
        targets=[{"symbol": "NEW", "weight": 0.18, "score": 1.0}],
        signal_date="2026-10-12",
        limits=limits,
    )
    assert [order.side for order in orders] == ["sell", "buy"]
    assert orders[0].estimated_notional <= 100
    assert orders[1].notional == 80
    assert sum(order.estimated_notional for order in orders) <= 180


def test_broker_order_ids_are_stable_for_duplicate_protection():
    kwargs = dict(
        equity=1000,
        cash=1000,
        positions=[],
        targets=[{"symbol": "AAPL", "weight": 0.18}],
        signal_date="2026-10-12",
        limits=BrokerLimits(),
    )
    first = plan_rebalance(**kwargs)
    second = plan_rebalance(**kwargs)
    assert first[0].client_order_id == second[0].client_order_id


def test_unsafe_targets_are_rejected():
    with pytest.raises(ValueError, match="unsafe target"):
        validate_targets([{"symbol": "AAPL", "weight": 0.50}], BrokerLimits())
    with pytest.raises(ValueError, match="duplicate"):
        validate_targets(
            [{"symbol": "AAPL", "weight": 0.10}, {"symbol": "aapl", "weight": 0.10}],
            BrokerLimits(),
        )


def test_broker_plan_rejects_short_positions_and_invalid_limits():
    with pytest.raises(ValueError, match="short or negative"):
        plan_rebalance(
            equity=1000,
            cash=100,
            positions=[BrokerPosition("AAPL", -1, -100, 100)],
            targets=[],
            signal_date="2026-10-12",
            limits=BrokerLimits(),
        )
    with pytest.raises(ValueError, match="invalid broker safety limits"):
        validate_targets([], BrokerLimits(max_order_notional=0))
