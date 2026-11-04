"""Deterministic league rules: no live wallet, network or signer used."""
from datetime import datetime, timedelta, timezone

from integrations.audacefi.league_strategy import LeaguePolicy, decide


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def context(**changes):
    base = {
        "verified": True,
        "restrictions_verified": True,
        "category": "crypto-bro",
        "wallet_ready": True,
        "network": "solana-devnet",
        "season_status": "active",
        "start_utc": "2026-11-09T00:00:00Z",
        "end_utc": "2026-11-14T00:00:00Z",
        "start_snapshot_confirmed": True,
        "eligible_symbols": ["BTC", "ETH", "SOL"],
        "max_position_weight": .50,
        "max_total_exposure": .85,
    }
    base.update(changes)
    return base


def wallet(**changes):
    base = {
        "equity_usdc": 1000,
        "peak_equity_usdc": 1020,
        "cash_usdc": 1000,
        "positions": {},
    }
    base.update(changes)
    return base


def signals(**changes):
    base = {
        "asset_class": "crypto", "generated_at": NOW.isoformat(),
        "signals": [
            {"symbol": "SOL", "expected_return": .12,
             "momentum_3d": .04, "momentum_7d": .08,
             "volatility_annualized": .8, "avg_dollar_volume_usd": 250000000},
            {"symbol": "BTC", "expected_return": .08,
             "momentum_3d": .03, "momentum_7d": .05,
             "volatility_annualized": .55, "avg_dollar_volume_usd": 1000000000},
        ],
    }
    base.update(changes)
    return base


def test_active_season_proposes_risk_sized_spot_buys_only():
    result = decide(context(), wallet(), signals(), now=NOW)
    assert result["status"] == "PROPOSED"
    assert result["executionAuthorized"] is False
    assert result["actions"]
    assert all(a["side"] == "BUY_WITH_USDC" for a in result["actions"])
    assert sum(a["maximum_input_usdc"] for a in result["actions"]) <= 700
    assert max(a["maximum_input_usdc"] for a in result["actions"]) <= 350


def test_only_officially_allowed_crypto_can_be_proposed():
    result = decide(context(eligible_symbols=["BTC"]), wallet(), signals(), now=NOW)
    assert result["status"] == "PROPOSED"
    assert [a["symbol"] for a in result["actions"]] == ["BTC"]


def test_no_season_or_unverified_rules_means_no_trading():
    assert decide(context(verified=False), wallet(), signals(), now=NOW)["status"] == "HOLD"
    assert decide(context(restrictions_verified=False), wallet(), signals(), now=NOW)["status"] == "HOLD"
    assert decide(context(network="solana-mainnet"), wallet(), signals(), now=NOW)["status"] == "HOLD"
    assert decide(context(start_snapshot_confirmed=False), wallet(), signals(), now=NOW)["status"] == "HOLD"


def test_no_entry_without_uptrend_or_after_expiry():
    down = signals(signals=[{
        "symbol": "SOL", "expected_return": .20,
        "momentum_3d": -.04, "momentum_7d": -.08,
        "volatility_annualized": .8, "avg_dollar_volume_usd": 100000000,
    }])
    assert decide(context(), wallet(), down, now=NOW)["reason"] == "NO_POSITIVE_NET_EDGE"
    stale = signals(generated_at=(NOW-timedelta(hours=9)).isoformat())
    assert decide(context(), wallet(), stale, now=NOW)["reason"] == "SIGNALS_STALE"


def test_final_snapshot_prioritizes_usdc_exit_even_if_model_unavailable():
    p = wallet(cash_usdc=750, positions={"SOL": {
        "value_usdc": 250, "price_usdc": 120,
        "entry_price_usdc": 100, "peak_price_usdc": 130,
    }})
    end_window = datetime(2026, 11, 13, 19, tzinfo=timezone.utc)
    result = decide(context(), p, {}, now=end_window)
    assert result["status"] == "EXIT_REQUIRED"
    assert result["actions"][0]["side"] == "SELL_TO_USDC"
    assert result["executionAuthorized"] is False
    assert decide(context(), wallet(), {}, now=end_window)["reason"] == "USDC_ONLY_READY"


def test_risk_exit_precedes_fresh_model_buy_signals():
    p = wallet(cash_usdc=750, positions={"SOL": {
        "value_usdc": 250, "price_usdc": 89,
        "entry_price_usdc": 100, "peak_price_usdc": 115,
    }})
    result = decide(context(), p, signals(), now=NOW)
    assert result["status"] == "EXIT_REQUIRED"
    assert result["actions"][0]["symbol"] == "SOL"


def test_defensive_exit_when_season_drawdown_exceeded():
    p = wallet(equity_usdc=800, peak_equity_usdc=1000, cash_usdc=600,
               positions={"BTC": {
                   "value_usdc": 200, "price_usdc": 100,
                   "entry_price_usdc": 110, "peak_price_usdc": 115,
               }})
    result = decide(context(), p, signals(), now=NOW)
    assert result["status"] == "EXIT_REQUIRED"
    assert result["reason"] == "SEASON_DRAWDOWN_LIMIT"


def test_invalid_portfolio_holds_without_guessing_account_balance():
    p = wallet(cash_usdc=200, positions={})
    assert decide(context(), p, signals(), now=NOW)["reason"] == "PORTFOLIO_RECONCILIATION_FAILED"


def test_cannot_enter_in_final_sixteen_hours():
    almost_end = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
    s = signals(generated_at=almost_end.isoformat())
    result = decide(context(), wallet(), s, now=almost_end)
    assert result["status"] == "HOLD"
    assert result["reason"] == "NEW_ENTRIES_CLOSED_BEFORE_SNAPSHOT"


def test_preseason_requires_usdc_only():
    prior = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    p = wallet(cash_usdc=750, positions={"SOL": {
        "value_usdc": 250, "price_usdc": 120,
        "entry_price_usdc": 100, "peak_price_usdc": 130,
    }})
    assert decide(context(season_status="scheduled"), p, signals(), now=prior)["reason"] == "START_SNAPSHOT_MUST_BE_USDC_ONLY"


def test_league_policy_never_exceeds_verified_exchange_limits():
    result = decide(context(max_position_weight=.05, max_total_exposure=.08),
                    wallet(), signals(), now=NOW)
    assert result["status"] == "PROPOSED"
    assert sum(a["maximum_input_usdc"] for a in result["actions"]) <= 80
    assert all(a["maximum_input_usdc"] <= 50 for a in result["actions"])
