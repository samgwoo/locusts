from helpers import HOUR, et

from locusts import campus_time
from locusts.config import DEFAULT_CONFIG as C


def test_night_window_edges():
    assert not campus_time.is_night(et(2026, 10, 5, 22, 59), C)
    assert campus_time.is_night(et(2026, 10, 5, 23, 0), C)
    assert campus_time.is_night(et(2026, 10, 6, 8, 59), C)
    assert not campus_time.is_night(et(2026, 10, 6, 9, 0), C)


def test_day_boundaries_are_strictly_after_and_at_or_before():
    nine = et(2026, 10, 6, 9)
    assert campus_time.next_day_start(nine, C) == et(2026, 10, 7, 9)
    assert campus_time.current_day_start(nine, C) == nine
    assert campus_time.current_day_start(nine - 1, C) == et(2026, 10, 5, 9)


def test_fall_back_night_is_eleven_hours():
    start = campus_time.next_occurrence(et(2026, 10, 31, 12), C.night_start, C)
    end = campus_time.next_day_start(start, C)
    assert (end - start) / HOUR == 11


def test_spring_forward_night_is_nine_hours():
    start = campus_time.next_occurrence(et(2026, 3, 7, 12), C.night_start, C)
    end = campus_time.next_day_start(start, C)
    assert (end - start) / HOUR == 9
