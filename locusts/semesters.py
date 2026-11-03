"""Semesters: back-to-back stretches of the game that every player starts fresh.

Semester 1 starts at night_end on Config.semester_epoch, and each one after (or before) it follows a whole
number of weeks later, so every timestamp falls in exactly one. A semester's play ends semester_break_days
before the next one starts. From then on its pets are frozen: they're only ever looked at as they were at
`ends_at`, nothing can be done with them, and the final ranking is drawn from them.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from locusts import campus_time
from locusts.config import DEFAULT_CONFIG, Config


@dataclass(frozen=True)
class Semester:
    number: int
    starts_at: float
    ends_at: float
    # When the next semester starts; from ends_at until then is the break.
    next_starts_at: float

    def over(self, t: float) -> bool:
        return t >= self.ends_at


def semester(number: int, config: Config = DEFAULT_CONFIG) -> Semester:
    period = config.semester_weeks * 7
    first = config.semester_epoch + timedelta(days=(number - 1) * period)

    def at(day) -> float:
        return datetime.combine(day, config.night_end, tzinfo=config.timezone).timestamp()

    return Semester(number, at(first), at(first + timedelta(days=period - config.semester_break_days)),
                    at(first + timedelta(days=period)))


def number_at(t: float, config: Config = DEFAULT_CONFIG) -> int:
    """The semester `t` falls in, counting its break."""
    day = datetime.fromtimestamp(campus_time.current_day_start(t, config), config.timezone).date()
    return (day - config.semester_epoch).days // (config.semester_weeks * 7) + 1


def semester_at(t: float, config: Config = DEFAULT_CONFIG) -> Semester:
    return semester(number_at(t, config), config)


def pet_clock(number: int | None, updated_at: float, t: float, config: Config = DEFAULT_CONFIG) -> float:
    """How far to bring a pet of semester `number` last updated at `updated_at` when it's looked at at `t`:
    `t`, or its semester's end once that's passed. Pets without a semester never freeze."""
    if number is None:
        return t
    return max(updated_at, min(t, semester(number, config).ends_at))


def frozen(number: int | None, t: float, config: Config = DEFAULT_CONFIG) -> bool:
    return number is not None and semester(number, config).over(t)
