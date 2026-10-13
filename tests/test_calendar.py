from datetime import datetime, timezone

import pandas as pd

from autotrader.calendar import is_completed_market_session, next_nyse_sessions


def test_nyse_calendar_skips_good_friday_and_weekend():
    sessions = next_nyse_sessions(pd.Timestamp("2026-04-02"), 2)
    assert [value.date().isoformat() for value in sessions] == ["2026-04-06", "2026-04-07"]


def test_nyse_calendar_does_not_treat_columbus_day_as_closed():
    sessions = next_nyse_sessions(pd.Timestamp("2026-10-09"), 1)
    assert sessions[0].date().isoformat() == "2026-10-12"


def test_same_day_candle_is_rejected_until_close_buffer():
    before_close = datetime(2026, 10, 13, 19, 59, tzinfo=timezone.utc)  # 15:59 New York
    after_buffer = datetime(2026, 10, 13, 20, 15, tzinfo=timezone.utc)  # 16:15 New York
    assert not is_completed_market_session("2026-10-13", now=before_close)
    assert is_completed_market_session("2026-10-13", now=after_buffer)
    assert is_completed_market_session("2026-10-12", now=before_close)
