"""Every tunable number in the game. Balancing should only ever touch this file."""

from dataclasses import dataclass, field
from datetime import date, time
from zoneinfo import ZoneInfo

MAX_STAT = 100.0


@dataclass(frozen=True)
class Config:
    # Campus clock. America/New_York follows EST/EDT so "night" stays at the same wall-clock hours
    # year-round. The night window doubles as the day boundary: a pet's day starts at night_end.
    timezone: ZoneInfo = ZoneInfo("America/New_York")
    night_start: time = time(23, 0)
    night_end: time = time(9, 0)
    # College Hall. The sky reads Philadelphia's weather and today's sunrise and sunset from here.
    weather_latitude: float = 39.9522
    weather_longitude: float = -75.1932

    # Semesters run back to back from semester_epoch (a Monday), each starting at night_end and lasting
    # semester_weeks weeks. The last semester_break_days of each are a break: the semester is over, its pets
    # stay frozen as they were at the end, and the final ranking is up until the next one starts. Every
    # player hatches a new pet each semester.
    semester_epoch: date = date(2026, 10, 26)
    semester_weeks: int = 3
    semester_break_days: int = 1

    # Fullness: 100 is fed, 0 is starving. An awake pet empties in 16 hours and a sleeping pet at
    # half that rate, so a 10-hour night plus 14 awake hours burns ~119 per day: one meal a day
    # starves, two keeps the pet fed.
    fullness_drain_per_hour: float = MAX_STAT / 16
    sleeping_fullness_multiplier: float = 0.5
    meal_fullness: float = MAX_STAT
    meal_price: float = 5.0
    # A starving pet with no cheaper meal on hand orders DoorDash wherever it is, right away: pricey, not much
    # food, and it orders even if that puts its money below zero.
    doordash_price: float = 10.0
    doordash_fullness: float = 30.0

    # Energy: drained by time at a building, restored by sleep. A full pet can work 8 hours before it
    # is exhausted, and 10 hours of sleep refills it from empty.
    work_energy_drain_per_hour: float = MAX_STAT / 8
    idle_energy_drain_per_hour: float = 0.0
    sleep_energy_regen_per_hour: float = MAX_STAT / 10
    min_energy_to_work: float = 10.0

    # Rest: how well a pet works today, from total hours slept in the 24 hours ending at night_end.
    # Piecewise-linear through these (hours, multiplier) points, clamped at both ends.
    rest_curve: tuple[tuple[float, float], ...] = ((0.0, 0.5), (8.0, 0.9), (10.0, 1.0))

    # Exhaustion: running out of energy at a building sends the pet home and penalizes the whole
    # next day. Coffee closes coffee_mitigation of the gap back to normal while it lasts, and on any day
    # makes the pet work coffee_work_boost faster.
    exhausted_energy_multiplier: float = 2.0
    exhausted_work_multiplier: float = 0.5
    coffee_mitigation: float = 0.5
    coffee_work_boost: float = 0.2
    coffee_hours: float = 4.0
    coffee_price: float = 4.0

    # Work output per hour at full effectiveness.
    xp_per_hour: float = 10.0
    task_pay_per_hour: float = 3.0

    starting_money: float = 50.0

    # Events (locusts/events.toml): one comes up after every event_hours[0] to event_hours[1] hours a pet
    # spends at work, and waits event_window_minutes for its player to answer before the pet lets it go.
    event_hours: tuple[float, float] = (2.0, 3.0)
    event_window_minutes: float = 45.0
    # GPA, which every pet has and events move: it starts at starting_gpa and stays within 0 to gpa_max.
    # Pre-Med's Summa Track pays off while it's at least gpa_honors.
    starting_gpa: float = 3.5
    gpa_max: float = 4.0
    gpa_honors: float = 3.9

    # Pets walk between buildings at this many campus-map pixels per second (the map is 5 px per meter),
    # and only start working or sleeping once they arrive. Harnwell to the farthest building, about
    # 4,200 px, takes about 3.5 minutes (about 2.3 with an Indego membership).
    travel_px_per_second: float = 30.0

    # Career upgrades work like Bloons TD 6: each career has upgrade_paths paths of upgrade_tiers tiers.
    # A pet may upgrade at most max_upgraded_paths paths, and only one of them past crosspath_max_tier,
    # so the deepest legal build is 5-2-0.
    upgrade_paths: int = 3
    upgrade_tiers: int = 5
    max_upgraded_paths: int = 2
    crosspath_max_tier: int = 2

    # Tier n of every path costs tier_sessions[n - 1] training sessions of xp_per_session XP each (about
    # a dedicated day of training), so a focused pet reaches tier 5 around the second job fair and its
    # capstone matters for the last two. Balance math in career_skill_trees.xlsx assumes these costs.
    xp_per_session: float = 45.0
    tier_sessions: tuple[int, ...] = (1, 2, 3, 4, 6)

    # Perk tuning that isn't a per-tier number in the career files. Wet Lab experiments run this long
    # after they start. The market moves once a day at night_end, by the same draw from market_seed for
    # every pet on the server.
    experiment_hours: float = 12.0
    market_seed: str = "locusts"
    # LeetCode Streak's bonus stops growing after this many consecutive training days.
    streak_max_days: int = 10

    # Job fairs: one a week during a semester, open from night_end on fair_weekday (0 is Monday) for
    # fair_hours. A week whose fair wouldn't have its results out before the semester ends has none. Results
    # come out fair_grace_minutes after it closes, so a run started just before closing still counts, and
    # a scored run has to be submitted within fair_submit_seconds of the run ending.
    fair_weekday: int = 2
    fair_hours: float = 24.0
    fair_grace_minutes: float = 5.0
    fair_submit_seconds: float = 120.0
    fair_applications: int = 2
    # Each fair posts this many of each career's regular roles, always including one with no
    # requirements, plus every role a perk unlocks. Picks come from fair_seed and the opening time.
    fair_roles_per_career: int = 3
    fair_seed: str = "locusts"
    # A normal internship pays this much a week, by the hour and whatever the pet is doing, from one
    # fair's results to the next. It's about three weeks of campus tasks. Roles pay multiples of it.
    internship_pay_per_week: float = 300.0
    # In fair minigames a pet can take one extra hit for every this many career tiers it owns.
    tiers_per_extra_hit: int = 2

    # Buildings a pet can be sent to. Career paths say which ones they train at; any of them hosts tasks.
    buildings: dict[str, "Building"] = field(
        default_factory=lambda: {
            # Tech
            "towne": Building("Towne Building"),
            "moore": Building("Moore School Building"),
            "levine": Building("Levine Hall"),
            "skirkanich": Building("Skirkanich Hall"),
            "amy_gutmann": Building("Amy Gutmann Hall"),
            # Pre-Med
            "van_pelt": Building("Van Pelt Library"),
            "goddard_richards": Building("Goddard & Richards Laboratories"),
            "chem": Building("Chemistry Laboratories 1973 Wing"),
            "wistar": Building("Wistar Institute"),
            # Consulting
            "steinberg_dietrich": Building("Steinberg-Dietrich Hall"),
            "vance": Building("Vance Hall"),
            "houston": Building("Houston Hall"),
            # Finance
            "huntsman": Building("John M. Huntsman Hall"),
            "mcneil": Building("McNeil Building"),
            "lauder_fischer": Building("Lauder Fischer Hall"),
            # Pre-Law
            "irvine": Building("Irvine Auditorium"),
            "silverman": Building("Silverman Hall"),
            "gittis": Building("Gittis Hall"),
            # Every career
            "pottruck": Building("Pottruck Health and Fitness Center"),
        }
    )
    # Any pet can work out at the gym instead of training a career path. Every fitness_hours_per_level
    # hours there (at full work speed) adds fitness_energy_per_level to its max energy, for good, up to
    # fitness_max_levels levels. Sleep refills a fitter pet proportionally faster, so a full night
    # still tops it up.
    gym: str = "pottruck"
    fitness_hours_per_level: float = 4.0
    fitness_energy_per_level: float = 10.0
    fitness_max_levels: int = 6

    # Shop subscriptions (see locusts/items.toml) bill this often from the pet's money, and lapse when
    # it can't pay.
    subscription_days: float = 7.0

    # Pets live in the college houses in locusts/houses.toml. Pets from before houses live here.
    default_house: str = "harnwell"

    # Other pets on the campus map are refetched this often by each client.
    campus_poll_seconds: float = 5.0


@dataclass(frozen=True)
class Building:
    # Must match a building name in locusts/static/map/buildings.json.
    map_name: str


DEFAULT_CONFIG = Config()
