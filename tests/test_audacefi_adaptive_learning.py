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


def test_training_does_not_fabricate_improvement_over_baseline():
    result = evaluate({"SOL": candles(), "ETH": candles(drift=.0018)})
    # All presets benefit from a strongly rising synthetic market. That does
    # not qualify a profitable alternative as an improvement over baseline.
    assert result["status"] == "NO_VALIDATED_IMPROVEMENT"


def test_fee_and_slippage_are_charged_on_each_round_trip():
    rows = validate_candles(candles(drift=.0003))
    evaluation = _simulate(rows, PRESETS["baseline"], 96, len(rows))
    assert evaluation["net_return"] <= .001


def test_active_profile_can_only_be_an_enumerated_preset():
    base = LeaguePolicy(maximum_season_drawdown=.018, max_position_weight=.35)
    assert policy_for("early", base).intraday_m6_min == .004
    assert policy_for("random", base) == base
    assert policy_for("early", base).maximum_season_drawdown == .018
    assert policy_for("early", base).max_position_weight == .35


def test_promotion_requires_three_distinct_consecutive_confirmations():
    suggestion = {"status": "VALIDATED_CANDIDATE", "profile": "early"}
    s1 = update_promotion(None, suggestion, window=100)
    assert s1 == {"active": "baseline", "pending": "early", "count": 1, "window": 100}
    assert update_promotion(s1, suggestion, window=100) == s1
    s2 = update_promotion(s1, suggestion, window=101)
    assert s2["active"] == "baseline" and s2["count"] == 2
    s3 = update_promotion(s2, suggestion, window=102)
    assert s3["active"] == "early" and s3["count"] == 0


def test_duplicate_or_overlapping_validation_blocks_do_not_count():
    suggestion = {"status": "VALIDATED_CANDIDATE", "profile": "early"}
    s1 = update_promotion(None, suggestion, window=100)
    assert update_promotion(s1, suggestion, window=100) == s1
    # A skipped block means the next validation is not consecutive evidence.
    s2 = update_promotion(s1, suggestion, window=102)
    assert s2["active"] == "baseline" and s2["count"] == 1


def test_recovery_is_not_promoted_from_short_live_validation():
    state = update_promotion(None,
        {"status": "VALIDATED_CANDIDATE", "profile": "recovery"}, window=100)
    assert state["active"] == "baseline" and state["pending"] is None


def test_recovery_requires_12_month_out_of_sample_advantage():
    comparison = {"recovery": {"holdout": {"net_return": .02}},
                  "baseline": {"holdout": {"net_return": .01}}}
    report = {"results": [{"symbol": "SOL",
                           "status": "VALIDATED_HISTORICAL_CANDIDATE",
                           "profile": "recovery", "comparison": comparison}]}
    assert qualification(report, "123")["approvedStrategy"] == "recovery"
    comparison["recovery"]["holdout"]["net_return"] = .011
    assert qualification(report, "124")["approvedStrategy"] == "momentum"


def test_recovery_requires_exact_candidate_filters_and_cost_edge():
    from integrations.audacefi.adaptive_learning import _qualifies
    p = PRESETS["recovery"]
    # All requested recovery filters pass, but the short-horizon move cannot
    # pay modeled fees and slippage, so it must not produce a trade.
    assert not _qualifies((.001, .0085, .0035, -.005, -.03, True, 2_000_000), p, .003)
    assert _qualifies((.002, .015, .012, -.005, -.03, True, 2_000_000), p, .003)


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
