from __future__ import annotations

import pandas as pd
import pytest

from scripts.validate_eulerpool import compare, normalize_eulerpool, report_markdown


def test_normalize_official_documented_shape():
    payload = {
        "ticker": "AAPL",
        "data": [
            {"date": "2026-10-22", "open": 250, "high": 253, "low": 249,
             "close": 252, "volume": 1000, "adjustedClose": 250.5},
            {"date": "2026-10-23", "open": 251, "high": 254, "low": 250,
             "close": 253, "volume": 1100, "adjustedClose": 251.5},
        ],
    }
    df = normalize_eulerpool(payload, "2026-10-22")
    assert list(df.index) == ["2026-10-22"]
    assert df.loc["2026-10-22", "adj_close"] == 250.5
    assert df.loc["2026-10-22", "close"] == 252


def test_candidate_needs_adjusted_close_validation():
    index = ["2026-10-20", "2026-10-21", "2026-10-22"]
    common = dict(open=[100, 101, 102], high=[103, 104, 105],
                  low=[99, 100, 101], close=[102, 103, 104], volume=[100, 110, 120])
    yahoo = pd.DataFrame({**common, "adj_close": [100, 101, 102]}, index=index)
    euler = pd.DataFrame({**common, "adj_close": [None, None, None]}, index=index)
    result = compare("AAPL", yahoo, euler, 3, "https://api.eulerpool.com/v1/equities/AAPL/history")
    assert result["adj_close_available"] is False
    assert result["status"] != "CANDIDATE"
    assert result["overlap"] == 3


def test_summary_does_not_show_secret():
    result = [{"symbol": "SPY", "status": "ERROR", "error": "bad response"}]
    summary = report_markdown(result, "2026-10-23")
    assert "SPY" in summary
    assert "bad response" not in summary
