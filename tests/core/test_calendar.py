from datetime import date

import pytest

from trading_core.market.calendar import MarketCalendar
from trading_core.market.options import atm_strike, nearest_listed


def test_normal_week_window_is_fri_mon_tue():
    cal = MarketCalendar([])
    expiry = date(2026, 11, 3)                          # Tuesday
    dte = {d: cal.trading_days_until(d, expiry) for d in
           [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30), date(2026, 11, 2), expiry]}
    assert dte == {date(2026, 10, 28): 4,               # Wed after previous expiry: no trade
                   date(2026, 10, 29): 3,               # Thu: no trade
                   date(2026, 10, 30): 2,               # Fri
                   date(2026, 11, 2): 1,                # Mon
                   expiry: 0}                           # Tue (expiry)
    assert cal.window_before(expiry, 3) == [date(2026, 10, 30), date(2026, 11, 2), expiry]


def test_holiday_tuesday_moves_window_to_thu_fri_mon():
    cal = MarketCalendar([date(2026, 11, 10)])          # Tuesday holiday -> expiry Monday
    expiry = date(2026, 11, 9)
    assert cal.window_before(expiry, 3) == [date(2026, 11, 5), date(2026, 11, 6), expiry]
    assert cal.trading_days_until(date(2026, 11, 4), expiry) == 3     # Wed after prior expiry: no trade
    assert cal.trading_days_until(date(2026, 11, 5), expiry) == 2


def test_weekends_and_holidays_are_not_trading_days():
    cal = MarketCalendar([date(2026, 12, 25)])
    assert not cal.is_trading_day(date(2026, 12, 25))
    assert not cal.is_trading_day(date(2026, 12, 26))   # Saturday
    assert cal.next_trading_day(date(2026, 12, 24)) == date(2026, 12, 28)


def test_expiry_in_past_raises():
    with pytest.raises(ValueError):
        MarketCalendar([]).trading_days_until(date(2026, 11, 4), date(2026, 11, 3))


def test_atm_rounding_half_up():
    assert atm_strike(25024.9, 50) == 25000
    assert atm_strike(25025, 50) == 25050
    assert atm_strike(24975, 50) == 25000


def test_nearest_listed_tie_goes_otm():
    strikes = [24650, 24700, 24750, 24800]
    assert nearest_listed(24725, strikes, tie="down") == 24700
    assert nearest_listed(24725, strikes, tie="up") == 24750
    assert nearest_listed(24740, strikes) == 24750
    assert nearest_listed(1, []) is None
