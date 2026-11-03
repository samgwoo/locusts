"""Wall-clock helpers in campus time. All timestamps are UTC epoch seconds."""

from datetime import datetime, time, timedelta

from locusts.config import Config


def _boundaries(ts: float, at: time, config: Config) -> list[float]:
    """Timestamps of `at` (local wall-clock) on the days around `ts`, in increasing order."""
    tz = config.timezone
    day = datetime.fromtimestamp(ts, tz).date()
    return [
        datetime.combine(day + timedelta(days=offset), at, tzinfo=tz).timestamp()
        for offset in range(-2, 3)
    ]


def next_occurrence(ts: float, at: time, config: Config) -> float:
    """First time strictly after `ts` that the campus clock reads `at`."""
    return next(b for b in _boundaries(ts, at, config) if b > ts)


def last_occurrence(ts: float, at: time, config: Config) -> float:
    """Last time at or before `ts` that the campus clock reads `at`."""
    return max(b for b in _boundaries(ts, at, config) if b <= ts)


def is_night(ts: float, config: Config) -> bool:
    local = datetime.fromtimestamp(ts, config.timezone).time()
    start, end = config.night_start, config.night_end
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def next_day_start(ts: float, config: Config) -> float:
    return next_occurrence(ts, config.night_end, config)


def current_day_start(ts: float, config: Config) -> float:
    return last_occurrence(ts, config.night_end, config)


def week_start(ts: float, config: Config) -> float:
    """The start of the campus week `ts` falls in: Monday's day start."""
    day = datetime.fromtimestamp(current_day_start(ts, config), config.timezone).date()
    monday = day - timedelta(days=day.weekday())
    return datetime.combine(monday, config.night_end, tzinfo=config.timezone).timestamp()
