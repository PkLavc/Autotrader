from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from autotrader import eulerpool


def test_normalizes_observed_candles_with_timestamp_and_no_volume():
    payload = [
        {"timestamp": "2026-11-02T00:00:00Z", "open": 100, "high": 103, "low": 99, "close": 101},
        {"timestamp": 1793664000000, "open": 101, "high": 104, "low": 100, "close": 102},
    ]
    rows = eulerpool.normalize_candles(payload)
    assert rows["2026-11-02"]["close"] == 101
    assert len(rows) == 2


def test_price_audit_verifies_same_session_without_changing_prices(monkeypatch):
    monkeypatch.setenv("EULERPOOL_API_KEY", "test-key")
    monkeypatch.setattr(eulerpool, "fetch_candles",
                        lambda symbol, key: {"2026-11-02": {"close": 101.0}})
    original = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-11-02"]))
    report = eulerpool.audit_target_prices([SimpleNamespace(symbol="AAPL")],
                                           {"AAPL": original}, "2026-11-02")
    assert report["status"] == "active"
    assert report["results"][0]["status"] == "verified"
    assert original["close"].iloc[0] == 100.0


def test_price_audit_does_not_accept_stale_or_divergent_response(monkeypatch):
    monkeypatch.setenv("EULERPOOL_API_KEY", "test-key")
    frame = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-11-02"]))
    target = [SimpleNamespace(symbol="AAPL")]
    monkeypatch.setattr(eulerpool, "fetch_candles",
                        lambda symbol, key: {"2026-10-30": {"close": 100.0}})
    stale = eulerpool.audit_target_prices(target, {"AAPL": frame}, "2026-11-02")
    assert stale["results"][0]["status"] == "missing_market_session"
    monkeypatch.setattr(eulerpool, "fetch_candles",
                        lambda symbol, key: {"2026-11-02": {"close": 120.0}})
    divergent = eulerpool.audit_target_prices(target, {"AAPL": frame}, "2026-11-02")
    assert divergent["results"][0]["status"] == "divergent"


def test_price_audit_skips_if_key_is_missing(monkeypatch):
    monkeypatch.delenv("EULERPOOL_API_KEY", raising=False)
    assert eulerpool.audit_target_prices([], {}, "2026-11-02")["status"] == "disabled_missing_secret"

def test_price_audit_can_verify_recent_historical_data_without_claiming_freshness(monkeypatch):
    monkeypatch.setenv("EULERPOOL_API_KEY", "test-key")
    monkeypatch.setattr(eulerpool, "fetch_candles",
                        lambda symbol, key: {"2026-11-02": {"close": 101.0}})
    frame = pd.DataFrame({"close": [101.0, 102.0]},
                         index=pd.to_datetime(["2026-11-02", "2026-11-03"]))
    result = eulerpool.audit_target_prices([SimpleNamespace(symbol="AAPL")],
                                           {"AAPL": frame}, "2026-11-03")
    assert result["status"] == "delayed_data_only"
    assert result["verified"] == 0
    assert result["stale_verified"] == 1
    assert result["results"][0]["status"] == "stale_verified"
