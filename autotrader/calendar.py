from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    DateOffset,
    GoodFriday,
    Holiday,
    MO,
    nearest_workday,
)
from pandas.tseries.offsets import CustomBusinessDay


class NyseRegularHolidayCalendar(AbstractHolidayCalendar):
    """Regular full-day NYSE holidays; actual downloaded sessions remain authoritative."""

    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
        Holiday("Martin Luther King Jr. Day", month=1, day=1, offset=DateOffset(weekday=MO(3)), start_date="1998-01-01"),
        Holiday("Washington's Birthday", month=2, day=1, offset=DateOffset(weekday=MO(3))),
        GoodFriday,
        Holiday("Memorial Day", month=5, day=31, offset=DateOffset(weekday=MO(-1))),
        Holiday("Juneteenth", month=6, day=19, observance=nearest_workday, start_date="2022-01-01"),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        Holiday("Labor Day", month=9, day=1, offset=DateOffset(weekday=MO(1))),
        Holiday("Thanksgiving", month=11, day=1, offset=DateOffset(weekday=pd.offsets.WeekOfMonth(week=3, weekday=3))),
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


NYSE_BUSINESS_DAY = CustomBusinessDay(calendar=NyseRegularHolidayCalendar())
NEW_YORK = ZoneInfo("America/New_York")


def next_nyse_sessions(last_session: pd.Timestamp, periods: int) -> pd.DatetimeIndex:
    if periods <= 0:
        return pd.DatetimeIndex([])
    start = pd.Timestamp(last_session).normalize() + NYSE_BUSINESS_DAY
    return pd.date_range(start=start, periods=periods, freq=NYSE_BUSINESS_DAY)


def is_completed_market_session(
    session: str,
    *,
    now: datetime | None = None,
    completion_buffer_minutes: int = 15,
) -> bool:
    """Reject a same-day daily candle until safely after the regular NYSE close."""
    current = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    session_date = pd.Timestamp(session).date()
    if session_date < current.date():
        return True
    if session_date > current.date():
        return False
    minutes = 16 * 60 + completion_buffer_minutes
    threshold = time(hour=minutes // 60, minute=minutes % 60)
    return current.time().replace(tzinfo=None) >= threshold
