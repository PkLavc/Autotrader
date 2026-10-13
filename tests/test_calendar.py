import pandas as pd

from autotrader.calendar import next_nyse_sessions


def test_nyse_calendar_skips_good_friday_and_weekend():
    sessions = next_nyse_sessions(pd.Timestamp("2026-04-02"), 2)
    assert [value.date().isoformat() for value in sessions] == ["2026-04-06", "2026-04-07"]


def test_nyse_calendar_does_not_treat_columbus_day_as_closed():
    sessions = next_nyse_sessions(pd.Timestamp("2026-10-09"), 1)
    assert sessions[0].date().isoformat() == "2026-10-12"
