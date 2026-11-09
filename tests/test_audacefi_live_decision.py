from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import math
import sys

import pandas as pd

from integrations.audacefi import live_decision


def test_scheduled_decision_uses_crypto_strategy_and_caps_order(monkeypatch):
    now = datetime.now(timezone.utc)
    days = pd.date_range(end=(now - timedelta(days=1)).date(), periods=45, freq="D")
    prices = [100 * 1.018**index * (1 + .003 * math.sin(index)) for index in range(45)]
    bars = pd.DataFrame({"close": prices, "volume": [1_000_000] * 45}, index=days)
    monkeypatch.setattr(live_decision, "load_daily_history", lambda symbol, lookback: bars)
    request = {
        "fetchSignals": True,
        "context": {
            "verified": True, "category": "crypto-bro", "restrictions_verified": True,
            "wallet_ready": True, "network": "solana-devnet", "season_status": "active",
            "start_utc": (now - timedelta(hours=1)).isoformat(),
            "end_utc": (now + timedelta(days=2)).isoformat(),
            "eligible_symbols": ["SOL"], "max_position_weight": .0025,
            "max_total_exposure": .0025, "start_snapshot_confirmed": True,
        },
        "portfolio": {"equity_usdc": 10000, "cash_usdc": 10000,
                      "peak_equity_usdc": 10000, "positions": {}},
    }
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", output)

    assert live_decision.main() == 0
    decision = json.loads(output.getvalue())
    assert decision["status"] == "PROPOSED"
    assert decision["actions"][0]["side"] == "BUY_WITH_USDC"
    assert decision["actions"][0]["symbol"] == "SOL"
    assert decision["actions"][0]["maximum_input_usdc"] <= 25


def test_final_window_requires_sell_without_forecast(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(live_decision, "load_daily_history", lambda *_: (_ for _ in ()).throw(
        AssertionError("final exit must not fetch signals")))
    request = {
        "fetchSignals": False,
        "context": {
            "verified": True, "category": "crypto-bro", "restrictions_verified": True,
            "wallet_ready": True, "network": "solana-devnet", "season_status": "active",
            "start_utc": (now - timedelta(days=2)).isoformat(),
            "end_utc": (now + timedelta(hours=7)).isoformat(),
            "eligible_symbols": ["SOL"], "max_position_weight": .0025,
            "max_total_exposure": .0025, "start_snapshot_confirmed": True,
        },
        "portfolio": {"equity_usdc": 10000, "cash_usdc": 9975,
                      "peak_equity_usdc": 10000, "positions": {"SOL": {
                          "value_usdc": 25, "price_usdc": 150,
                          "entry_price_usdc": 150, "peak_price_usdc": 150,
                      }}},
    }
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", output)

    assert live_decision.main() == 0
    decision = json.loads(output.getvalue())
    assert decision["status"] == "EXIT_REQUIRED"
    assert decision["actions"][0]["side"] == "SELL_TO_USDC"
