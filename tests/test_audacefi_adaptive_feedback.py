from integrations.audacefi.adaptive_feedback import select_by_asset
from integrations.audacefi.adaptive_learning import PRESETS, generate_candidates
from integrations.audacefi.strategy_qualification import qualification


def _ledger(symbol="SOL", net=.02, start=1_700_000_000_000, count=40):
    rows = []
    indicators = {"momentum_1h": .004, "momentum_3h": .012,
        "momentum_6h": .014, "momentum_12h": -.004,
        "momentum_24h": -.03, "above_ema20": True,
        "breakout_20": -.01, "ema20_gap": -.02, "rsi14": 25}
    for i in range(count):
        observed = start + i * 24 * 60 * 60_000
        rows.append({"symbol": symbol, "observedAt": observed,
            "indicators": indicators,
            "outcomes": {"24h": {"markedAt": observed + 24 * 60 * 60_000,
                "netReturnAfterEstimatedCosts": net}}})
    return rows


def _approved(profile="recovery"):
    return {"SOL": {"profile": profile, "parameters": PRESETS[profile]}}


def test_new_matured_results_promote_only_the_oos_approved_asset_policy():
    now = 1_700_000_000_000 + 60 * 24 * 60 * 60_000
    result = select_by_asset(_approved(), _ledger(), now_ms=now)
    assert result["activeBySymbol"] == {"ETH": "momentum", "SOL": "recovery"}
    assert result["evidenceBySymbol"]["SOL"]["status"] == "PROMOTED_FROM_MATURED_OOS_FEEDBACK"
    assert result["evidenceBySymbol"]["SOL"]["validationObservations"] == 12
    assert result["activeParametersBySymbol"]["SOL"] != PRESETS["recovery"]
    assert result["activeParametersBySymbol"]["SOL"]["intraday_m3_min"] >= \
        PRESETS["recovery"]["intraday_m3_min"]


def test_four_non_overlapping_live_labels_complete_a_disjoint_online_update():
    now = 1_700_000_000_000 + 8 * 24 * 60 * 60_000
    result = select_by_asset(_approved(), _ledger(count=4), now_ms=now)
    evidence = result["evidenceBySymbol"]["SOL"]
    assert result["activeBySymbol"]["SOL"] == "recovery"
    assert evidence["status"] == "PROMOTED_FROM_MATURED_OOS_FEEDBACK"
    assert evidence["trainingObservations"] == 2
    assert evidence["validationObservations"] == 2
    assert evidence["samples"] == 4


def test_matured_results_change_the_next_shared_strategy_decision():
    import io
    import json
    import sys
    from datetime import datetime, timedelta, timezone
    from integrations.audacefi import live_decision
    import pytest

    now_ms = 1_700_000_000_000 + 60 * 24 * 60 * 60_000
    learned = select_by_asset(_approved(), _ledger(), now_ms=now_ms)
    now = datetime.now(timezone.utc)
    context = {"verified": True, "category": "crypto-bro",
        "restrictions_verified": True, "wallet_ready": True,
        "network": "solana-devnet", "season_status": "active",
        "start_utc": (now - timedelta(hours=1)).isoformat(),
        "end_utc": (now + timedelta(days=2)).isoformat(),
        "eligible_symbols": ["SOL"], "max_position_weight": .35,
        "max_total_exposure": .35, "start_snapshot_confirmed": True}
    portfolio = {"equity_usdc": 10_000, "cash_usdc": 10_000,
        "peak_equity_usdc": 10_000, "positions": {}}
    signal = {"symbol": "SOL", "last_close_usd": 100,
        "median_15m_dollar_volume_usd": 2_000_000, "above_ema20": True,
        "momentum_1h": .004, "momentum_3h": .012, "momentum_6h": .014,
        "momentum_12h": -.004, "momentum_24h": -.03}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(live_decision, "completed_intraday_signal",
                        lambda symbol, _: {**signal, "symbol": symbol})

    def run(historical):
        request = {"fetchSignals": True, "signalMode": "intraday",
            "historicalStrategies": historical, "opportunityFeedback": _ledger(),
            "adaptiveState": {"active": "baseline"}, "context": context,
            "portfolio": portfolio}
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
        monkeypatch.setattr(sys, "stdout", output)
        assert live_decision.main() == 0
        return json.loads(output.getvalue())

    try:
        baseline = run({})
        recovered = run(_approved())
    finally:
        monkeypatch.undo()
    assert baseline["status"] == "HOLD"
    assert recovered["activeBySymbol"]["SOL"] == "recovery"
    assert recovered["adaptiveState"]["activeParametersBySymbol"]["SOL"] != \
        PRESETS["recovery"]
    assert recovered["status"] == "PROPOSED"
    assert recovered["actions"][0]["strategy"] == "recovery"


def test_recent_negative_matured_results_rollback_to_momentum_automatically():
    now = 1_700_000_000_000 + 60 * 24 * 60 * 60_000
    result = select_by_asset(_approved(), _ledger(net=-.02), now_ms=now)
    assert result["activeBySymbol"]["SOL"] == "momentum"
    assert result["evidenceBySymbol"]["SOL"]["status"] == "MOMENTUM_RETAINED"


def test_unmatured_or_future_outcomes_cannot_affect_selection():
    now = 1_700_000_000_000 + 4 * 24 * 60 * 60_000
    rows = _ledger(count=4)
    rows[-1]["outcomes"]["24h"]["markedAt"] += 48 * 60 * 60_000
    result = select_by_asset(_approved(), rows, now_ms=now)
    assert result["activeBySymbol"]["SOL"] == "recovery"
    assert result["evidenceBySymbol"]["SOL"]["samples"] == 3
    assert result["evidenceBySymbol"]["SOL"]["status"] == \
        "HISTORICAL_OOS_BOOTSTRAP_AWAITING_LIVE_FEEDBACK"


def test_realized_losses_for_asset_rollback_its_strategy_only():
    now = 1_700_000_000_000 + 60 * 24 * 60 * 60_000
    realized = [{"symbol": "SOL", "profile": "recovery", "pnl": -8.0} for _ in range(3)]
    result = select_by_asset(_approved(), _ledger(), now_ms=now, realized=realized)
    assert result["activeBySymbol"]["SOL"] == "momentum"
    assert result["evidenceBySymbol"]["SOL"]["status"] == "LIVE_REALIZED_LOSS_ROLLBACK"


def test_training_segment_changes_bounded_candidate_parameters_but_not_risk():
    rows = [[1_700_000_100 + i * 900, 100 * (1.00001 ** i), 50_000]
            for i in range(800)]
    calm = generate_candidates(rows)
    noisy_rows = [[1_700_000_100 + i * 900,
        100 * (1.001 ** i if i % 2 == 0 else .999 ** i), 50_000]
        for i in range(800)]
    volatile = generate_candidates(noisy_rows)
    assert calm["baseline"] == volatile["baseline"] == PRESETS["baseline"]
    assert calm["recovery"]["intraday_m3_min"] != volatile["recovery"]["intraday_m3_min"]
    assert calm["recovery"]["intraday_m3_min"] <= .02


def test_qualification_output_promotes_per_asset_with_dynamic_parameters():
    params = dict(PRESETS["recovery"])
    comparison = {
        "baseline": {"train": {"net_return": .01}, "holdout": {"net_return": .005}},
        "recovery": {"parameters": params,
            "train": {"net_return": .03, "trades": 12,
                "max_rolling_swaps_24h": 4, "max_intraday_drawdown": .01,
                "max_competition_drawdown": .01},
            "holdout": {"net_return": .02, "trades": 8,
                "max_rolling_swaps_24h": 4, "usdc_only_at_end": True,
                "max_intraday_drawdown": .01, "max_competition_drawdown": .01}},
    }
    row = {"symbol": "SOL", "status": "VALIDATED_HISTORICAL_CANDIDATE",
        "profile": "recovery", "comparison": comparison,
        "holdoutConsistency": {"folds": 12, "wilsonLower95": .55,
            "blockBootstrapMeanEdgeLower95": .001, "regimeCoverage": 2,
            "regimes": {"bull": {"folds": 6, "meanEdge": .01},
                        "bear": {"folds": 6, "meanEdge": .01}}}}
    result = qualification({"results": [row]}, "321")
    assert result["approvedSymbols"] == ["SOL"]
    assert result["approvedBySymbol"]["SOL"]["parameters"] == params
