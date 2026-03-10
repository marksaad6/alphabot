"""
src/utils/market_hours.py
--------------------------
Utilities for checking US stock market hours.
NYSE/NASDAQ: Monday–Friday, 9:30 AM – 4:00 PM Eastern, excluding holidays.
"""

from datetime import datetime, time, timedelta
import pytz

EASTERN = pytz.timezone("US/Eastern")

# US market holidays 2025 (add more years as needed)
MARKET_HOLIDAYS_2025 = {
    datetime(2025, 1, 1).date(),   # New Year's Day
    datetime(2025, 1, 20).date(),  # MLK Day
    datetime(2025, 2, 17).date(),  # Presidents Day
    datetime(2025, 4, 18).date(),  # Good Friday
    datetime(2025, 5, 26).date(),  # Memorial Day
    datetime(2025, 6, 19).date(),  # Juneteenth
    datetime(2025, 7, 4).date(),   # Independence Day
    datetime(2025, 9, 1).date(),   # Labor Day
    datetime(2025, 11, 27).date(), # Thanksgiving
    datetime(2025, 12, 25).date(), # Christmas
}

MARKET_OPEN  = time(9, 30, 0)
MARKET_CLOSE = time(16, 0, 0)


def is_market_open() -> bool:
    """Returns True if the US stock market is currently open."""
    now_et = datetime.now(EASTERN)
    today = now_et.date()

    # Weekend
    if now_et.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False

    # Holiday
    if today in MARKET_HOLIDAYS_2025:
        return False

    # Hours check
    current_time = now_et.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE


def next_market_open() -> datetime:
    """Returns the next market open datetime in Eastern time."""
    now_et = datetime.now(EASTERN)
    check = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

    # If before today's open, return today's open
    if now_et.time() < MARKET_OPEN and now_et.weekday() < 5:
        if check.date() not in MARKET_HOLIDAYS_2025:
            return check

    # Otherwise advance to next weekday
    check += timedelta(days=1)
    while check.weekday() >= 5 or check.date() in MARKET_HOLIDAYS_2025:
        check += timedelta(days=1)

    return check.replace(hour=9, minute=30, second=0, microsecond=0)
