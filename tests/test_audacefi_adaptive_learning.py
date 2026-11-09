"""Offline, deterministic validation of the AudaceFi adaptive evaluator."""
from __future__ import annotations

from integrations.audacefi.adaptive_learning import (
    PRESETS, evaluate, policy_for, update_promotion, validate_candles,
    _simulate,
)
from integrations.audacefi.strategy_qualification import qualification
from integrations.audacefi.league_strategy import LeaguePolicy


def candles(count=300, drift=.002, start=1_700_000_100):
    start = start - start % 900
    return [[start + i * 900, 100 * (1 + drift) ** i, 50_000]
            for i in range(count)]


def test_incomplete_history_never_promotes():
    assert evaluate({"SOL": candles(120)})["status"] == "NO_VALIDATED_IMPROVEMENT"
    assert evaluate({"SOL": []})["status"] == "NO_VALIDATED_IMPROVEMENT"


def test_sparse_or_untrusted_history_fails_closed():
    rows = candles()
    rows[110][0] += 900
    assert validate_candles(rows) == []
    rows = candles()
    rows[20][1] = float("nan")
    assert validate_candles(rows) == []
    assert validate_candles(candles()) != []


def test_completed_signal_contains_indicators_used_by_generated_families():
    from integrations.audacefi.intraday_signal import signal_from_rows
    rows = validate_candles(candles(160, drift=.0004))
    signal = signal_from_rows("SOL", rows, len(rows) - 1)
    assert signal is not None
    assert signal["breakout_20"] > 0
    assert signal["ema20_gap"] > 0
    assert 0 <= signal["rsi14"] <= 100


def test_training_does_not_fabricate_improvement_over_baseline():
    result = evaluate({"SOL": candles(), "ETH": candles(drift=.0018)})
    # All presets benefit from a strongly rising synthetic market. That does
    # not qualify a profitable alternative as an improvement over baseline.
    assert result["status"] == "NO_VALIDATED_IMPROVEMENT"


def test_fee_and_slippage_are_charged_on_each_round_trip():
    rows = validate_candles(candles(drift=.0003))
    evaluation = _simulate(rows, PRESETS["baseline"], 96, len(rows))
    assert evaluation["estimated_costs_usdc"] > 0
    assert evaluation["estimated_round_trip_cost_bps"] == 30
    assert evaluation["max_rolling_swaps_24h"] <= 10


def test_active_profile_can_only_be_an_enumerated_preset():
    base = LeaguePolicy(maximum_season_drawdown=.018, max_position_weight=.35)
    assert policy_for("early", base).intraday_m6_min == .004
    assert policy_for("random", base) == base
    assert policy_for("early", base).maximum_season_drawdown == .018
    assert policy_for("early", base).max_position_weight == .35


def test_candidate_grid_covers_crypto_signal_families_without_changing_risk():
    from integrations.audacefi.adaptive_learning import generate_candidates
    candidates = generate_candidates()
    assert {"baseline", "recovery", "breakout", "mean_reversion", "multi_trend",
            "momentum_loose", "momentum_tight"} <= set(candidates)
    base = LeaguePolicy(maximum_season_drawdown=.018, max_position_weight=.35,
                        max_total_exposure=.35)
    for profile in candidates:
        policy = policy_for(profile, base)
        assert policy.maximum_season_drawdown == .018
        assert policy.max_position_weight == .35
        assert policy.max_total_exposure == .35


def test_short_validation_cannot_promote_any_new_strategy():
    suggestion = {"status": "VALIDATED_CANDIDATE", "profile": "early"}
    s1 = update_promotion(None, suggestion, window=100)
    assert s1 == {"active": "baseline", "pending": None, "count": 0, "window": 100}
    existing = {"active": "steady", "pending": "early", "count": 2, "window": 100}
    assert update_promotion(existing, suggestion, window=101) == {
        "active": "steady", "pending": None, "count": 0, "window": 101}


def test_overlapping_short_validation_does_not_authorize_a_variant():
    suggestion = {"status": "VALIDATED_CANDIDATE", "profile": "early"}
    s1 = update_promotion(None, suggestion, window=100)
    assert update_promotion(s1, suggestion, window=100) == s1
    s2 = update_promotion(s1, suggestion, window=102)
    assert s2["active"] == "baseline" and s2["count"] == 0


def test_recovery_is_not_promoted_from_short_live_validation():
    state = update_promotion(None,
        {"status": "VALIDATED_CANDIDATE", "profile": "recovery"}, window=100)
    assert state["active"] == "baseline" and state["pending"] is None


def test_recovery_requires_12_month_out_of_sample_advantage():
    comparison = {"recovery": {"train": {"net_return": .03, "trades": 12,
                                          "max_drawdown": .04,
                                          "max_intraday_drawdown": .01,
                                          "max_competition_drawdown": .01,
                                          "max_rolling_swaps_24h": 4},
                                "holdout": {"net_return": .02, "trades": 8, "max_drawdown": .01,
                                            "max_intraday_drawdown": .01,
                                            "max_competition_drawdown": .01,
                                            "max_rolling_swaps_24h": 4,
                                            "usdc_only_at_end": True}},
                  "baseline": {"train": {"net_return": .02},
                               "holdout": {"net_return": .01}}}
    row = {"symbol": "SOL", "status": "VALIDATED_HISTORICAL_CANDIDATE",
           "profile": "recovery", "comparison": comparison,
           "holdoutConsistency": {"folds": 12,
               "foldsBeatingBaseline": 10, "wilsonLower95": .55,
               "blockBootstrapMeanEdgeLower95": .001,
               "regimeCoverage": 2, "regimes": {
                   "bull": {"folds": 6, "meanEdge": .01},
                   "bear": {"folds": 6, "meanEdge": .01}}}}
    report = {"results": [row, {**row, "symbol": "ETH"}]}
    assert qualification(report, "123")["approvedStrategy"] == "recovery"
    comparison["recovery"]["holdout"]["net_return"] = .011
    assert qualification(report, "124")["approvedStrategy"] == "momentum"


def test_recovery_requires_exact_candidate_filters_and_cost_edge():
    from datetime import datetime, timezone
    from integrations.audacefi.league_strategy import decide
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    context = {"verified": True, "category": "crypto-bro",
        "restrictions_verified": True, "wallet_ready": True,
        "network": "solana-devnet", "season_status": "active",
        "start_utc": "2026-11-09T00:00:00Z", "end_utc": "2026-11-14T00:00:00Z",
        "eligible_symbols": ["SOL"], "max_position_weight": .35,
        "max_total_exposure": .35, "start_snapshot_confirmed": True}
    portfolio = {"equity_usdc": 10_000, "cash_usdc": 10_000,
                 "peak_equity_usdc": 10_000, "positions": {}}
    policy = policy_for("recovery", LeaguePolicy(
        maximum_season_drawdown=.018, round_trip_cost_bps=30,
        minimum_net_edge=.004, max_position_weight=.35, max_total_exposure=.35))
    def result(m3, m6):
        return decide(context, portfolio, {"asset_class": "crypto",
            "model": "coinbase_completed_15m", "generated_at": now.isoformat(),
            "signals": [{"symbol": "SOL", "last_close_usd": 100,
                "median_15m_dollar_volume_usd": 2_000_000, "above_ema20": True,
                "momentum_1h": .002, "momentum_3h": m3, "momentum_6h": m6,
                "momentum_12h": -.005, "momentum_24h": -.03}]}, now=now, policy=policy)
    # Filters pass, but strength cannot pay the 30bps estimate plus the 40bps
    # required net edge. The shared engine therefore holds.
    assert result(.0085, .0035)["status"] == "HOLD"
    assert result(.015, .012)["actions"][0]["strategy"] == "recovery"


def test_breakout_candidate_uses_shared_engine_and_requires_breakout_confirmation():
    from datetime import datetime, timezone
    from integrations.audacefi.league_strategy import decide
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    context = {"verified": True, "category": "crypto-bro",
        "restrictions_verified": True, "wallet_ready": True,
        "network": "solana-devnet", "season_status": "active",
        "start_utc": "2026-11-09T00:00:00Z", "end_utc": "2026-11-14T00:00:00Z",
        "eligible_symbols": ["SOL"], "max_position_weight": .35,
        "max_total_exposure": .35, "start_snapshot_confirmed": True}
    portfolio = {"equity_usdc": 10_000, "cash_usdc": 10_000,
                 "peak_equity_usdc": 10_000, "positions": {}}
    policy = policy_for("breakout", LeaguePolicy(maximum_season_drawdown=.018,
        max_position_weight=.35, max_total_exposure=.35, round_trip_cost_bps=30,
        minimum_net_edge=.004))
    def decide_with(breakout):
        return decide(context, portfolio, {"asset_class": "crypto",
            "model": "coinbase_completed_15m", "generated_at": now.isoformat(),
            "signals": [{"symbol": "SOL", "last_close_usd": 100,
                "median_15m_dollar_volume_usd": 2_000_000, "above_ema20": True,
                "breakout_20": breakout, "ema20_gap": .01, "rsi14": 60,
                "momentum_1h": .01, "momentum_3h": .01, "momentum_6h": .01,
                "momentum_12h": .01, "momentum_24h": .01}]}, now=now, policy=policy)
    assert decide_with(.001)["status"] == "HOLD"
    assert decide_with(.01)["actions"][0]["strategy"] == "breakout"


def test_unvalidated_candidate_resets_streak_without_changing_live_policy():
    s1 = update_promotion(None, {"status": "VALIDATED_CANDIDATE", "profile": "steady"}, window=200)
    s2 = update_promotion(s1, {"status": "NO_VALIDATED_IMPROVEMENT"}, window=201)
    assert s2["active"] == "baseline"
    assert s2["pending"] is None and s2["count"] == 0


def test_attempted_arbitrary_variant_does_not_change_live_policy():
    suggestion = {"status": "VALIDATED_CANDIDATE", "profile": "disable_risk"}
    s = update_promotion(None, suggestion, window=80)
    assert s["active"] == "baseline"
    assert s["pending"] is None


def test_out_of_sample_qualification_can_select_generated_families():
    comparison = {
        "baseline": {"train": {"net_return": .01},
                     "holdout": {"net_return": .005}},
        "multi_trend": {
            "train": {"net_return": .03, "trades": 12,
                "max_intraday_drawdown": .01, "max_competition_drawdown": .01,
                "max_rolling_swaps_24h": 4},
            "holdout": {"net_return": .02, "trades": 8,
                "max_rolling_swaps_24h": 4, "usdc_only_at_end": True,
                "max_intraday_drawdown": .01, "max_competition_drawdown": .01}},
    }
    row = {"symbol": "SOL", "status": "VALIDATED_HISTORICAL_CANDIDATE",
        "profile": "multi_trend", "comparison": comparison,
        "holdoutConsistency": {"folds": 12, "wilsonLower95": .55,
                "blockBootstrapMeanEdgeLower95": .001,
                "regimeCoverage": 2, "regimes": {"bull": {"folds": 6, "meanEdge": .01},
                    "bear": {"folds": 6, "meanEdge": .01}}}}
    report = {"results": [row, {**row, "symbol": "ETH"}]}
    result = qualification(report, "321")
    assert result["approvedStrategy"] == "multi_trend"
    assert result["approvedSymbols"] == ["SOL", "ETH"]
    assert qualification({"results": [row]}, "322")["approvedStrategy"] == "momentum"
    row["holdoutConsistency"]["blockBootstrapMeanEdgeLower95"] = -.001
    assert qualification(report, "323")["approvedStrategy"] == "momentum"
    assert result["schemaVersion"] == 2


def test_universe_scan_rejects_bad_mints_before_price_or_route_research():
    from integrations.audacefi.universe_research import valid_mint
    assert valid_mint("So11111111111111111111111111111111111111112")
    assert valid_mint("111") is False
    assert valid_mint("not-a-mint") is False
