from datetime import datetime, timedelta, timezone

import pytest

from integrations.audacefi.league_backtest import replay


START = datetime(2026, 11, 9, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 14, 0, tzinfo=timezone.utc)


def sample(at, price, *, positive=True):
    return {
        "timestamp": at.isoformat(),
        "prices_usdc": {"SOL": price},
        "report": {
            "asset_class": "crypto", "generated_at": at.isoformat(),
            "signals": [{
                "symbol": "SOL", "expected_return": .16,
                "momentum_3d": .05 if positive else -.04,
                "momentum_7d": .10 if positive else -.10,
                "volatility_annualized": .70,
                "avg_dollar_volume_usd": 120000000,
            }],
        },
    }


def test_five_day_spot_replay_respects_snapshots_and_costs():
    records = [
        sample(START, 100),
        sample(START + timedelta(hours=6), 100),
        sample(END-timedelta(hours=5), 110),
        sample(END, 111),
    ]
    result = replay(records, start_utc=START.isoformat(),
                    end_utc=END.isoformat(), eligible_symbols=["SOL"])
    assert result["simulation_eligible"] is True
    assert result["official_result"] is False
    assert result["executionAuthorized"] is False
    assert [row["side"] for row in result["events"]] == ["BUY", "SELL"]
    assert result["net_return_pct"] > 0
    assert result["final_usdc_equivalent"] == result["equity_curve"][-1]["cash_usdc"]


def test_risk_and_no_edge_never_force_trades():
    records = [
        sample(START, 100),
        sample(START+timedelta(days=1), 96, positive=False),
        sample(END, 90),
    ]
    result = replay(records, start_utc=START.isoformat(),
                    end_utc=END.isoformat(), eligible_symbols=["SOL"])
    assert result["trade_count"] == 0
    assert result["simulation_eligible"] is True


def test_snapshot_without_final_coverage_is_not_marked_qualified():
    records = [
        sample(START, 100),
        sample(START+timedelta(days=1), 100),
        sample(END-timedelta(hours=12), 102),
    ]
    result = replay(records, start_utc=START.isoformat(),
                    end_utc=END.isoformat(), eligible_symbols=["SOL"])
    assert result["simulation_eligible"] is False


def test_replay_rejects_duplicate_or_out_of_order_records():
    records = [
        sample(START, 100),
        sample(START+timedelta(days=1), 102),
        sample(START+timedelta(days=1), 105),
    ]
    with pytest.raises(ValueError, match="increasing"):
        replay(records, start_utc=START.isoformat(),
               end_utc=END.isoformat(), eligible_symbols=["SOL"])
