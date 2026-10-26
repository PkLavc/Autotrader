import pytest

import json
import sys

import pandas as pd

from autotrader.forecast import Forecast
from autotrader import run


def _history(session: str) -> pd.DataFrame:
    end = pd.Timestamp(session)
    index = pd.bdate_range(end=end, periods=400)
    return pd.DataFrame(
        {
            "open": [100.0] * 400,
            "high": [102.0] * 400,
            "low": [99.0] * 400,
            "close": [101.0] * 400,
            "volume": [1000.0] * 400,
            "amount": [101000.0] * 400,
            "dividends": [0.0] * 400,
            "stock_splits": [0.0] * 400,
        },
        index=index,
    )


@pytest.fixture(autouse=True)
def fixed_discovery(monkeypatch):
    # Unit tests must never depend on the remote S&P 500 constituent service.
    monkeypatch.setattr(run, "discover_market",
                        lambda market_date, cfg: (
                            list(cfg.symbols), {}, {
                                "status": "test", "discovered_today": [],
                                "monitored_today": len(cfg.symbols),
                            }
                        ))


class FakeEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def predict(self, symbol, history):
        return Forecast(symbol, 101.0, 106.05, 0.05, 0.20)


def test_pipeline_defers_close_signal_until_next_session_open(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    report = tmp_path / "latest.json"
    current_session = {"value": "2026-10-12"}
    monkeypatch.setattr(run, "load_daily_history", lambda symbol, lookback: _history(current_session["value"]))
    monkeypatch.setattr(run, "is_completed_market_session", lambda session: True)
    monkeypatch.setattr(run, "KronosEngine", FakeEngine)
    monkeypatch.setattr(sys, "argv", ["autotrader", "--state", str(state), "--report", str(report), "--learning", str(tmp_path / "learning.json")])

    assert run.main() == 0
    first_state = json.loads(state.read_text(encoding="utf-8"))
    first_report = json.loads(report.read_text(encoding="utf-8"))
    assert first_state["trades"] == []
    assert first_report["market_session"] == "2026-10-12"
    assert first_state["pending_signal_date"] == "2026-10-12"

    current_session["value"] = "2026-10-13"
    assert run.main() == 0
    second_state = json.loads(state.read_text(encoding="utf-8"))
    assert len(second_state["trades"]) == 5
    assert {trade["price_source"] for trade in second_state["trades"]} == {"session_open"}
    assert {trade["signal_date"] for trade in second_state["trades"]} == {"2026-10-12"}


def test_pipeline_skips_duplicate_market_session(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    report = tmp_path / "latest.json"
    monkeypatch.setattr(run, "load_daily_history", lambda symbol, lookback: _history("2026-10-12"))
    monkeypatch.setattr(run, "is_completed_market_session", lambda session: True)
    monkeypatch.setattr(run, "KronosEngine", FakeEngine)
    monkeypatch.setattr(sys, "argv", ["autotrader", "--state", str(state), "--report", str(report), "--learning", str(tmp_path / "learning.json")])
    run.main()
    before = state.read_text(encoding="utf-8")
    assert run.main() == 0
    assert state.read_text(encoding="utf-8") == before


def test_legacy_state_migrates_without_new_observation_or_trade(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    report = tmp_path / "latest.json"
    state.write_text(json.dumps({
        "cash": 1000.0,
        "positions": {},
        "last_prices": {"SPY": 101.0},
        "benchmark_start_price": 100.0,
        "nav_history": [{"timestamp": "legacy", "nav": 1000.0, "cash": 1000.0}],
        "trades": [],
    }), encoding="utf-8")
    report.write_text(json.dumps({
        "mode": "paper", "nav": 1000.0, "cash": 1000.0, "positions": {},
    }), encoding="utf-8")
    monkeypatch.setattr(run, "load_daily_history", lambda symbol, lookback: _history("2026-10-12"))
    monkeypatch.setattr(run, "is_completed_market_session", lambda session: True)
    monkeypatch.setattr(sys, "argv", ["autotrader", "--state", str(state), "--report", str(report), "--learning", str(tmp_path / "learning.json")])

    assert run.main() == 0
    migrated_state = json.loads(state.read_text(encoding="utf-8"))
    migrated_report = json.loads(report.read_text(encoding="utf-8"))
    assert migrated_state["last_market_date"] == "2026-10-12"
    assert len(migrated_state["nav_history"]) == 1
    assert migrated_state["trades"] == []
    assert migrated_report["market_session"] == "2026-10-12"
    assert migrated_report["execution_model"] == "legacy_same_close_before_v2_migration"
