"""What a pet's owned upgrade tiers and shop items actually do.

Each tier in a career file can carry an `effects` table. A pet's perks are the effects of every tier it
owns in its career, combined in path and tier order, then those of its items (see locusts/shop.py):
numbers add up, flags stay on once any tier sets them, and tables are merged key by key with higher
tiers overriding lower ones.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from locusts.config import Config

if TYPE_CHECKING:
    from locusts.upgrades import Career


@dataclass(frozen=True)
class TaskSite:
    """A building where the pet's campus tasks become a better kind of task."""

    building: str
    name: str
    # Extra fraction of task pay, and XP per hour as a fraction of xp_per_hour, banked on `path`.
    pay: float
    xp: float
    path: str


@dataclass(frozen=True)
class Market:
    # Most of its total money (cash plus invested) a pet may have invested, and the daily move:
    # drift + swing * z for a server-wide z in [-1, 1], never worse than -loss_cap.
    max_share: float
    drift: float
    swing: float
    loss_cap: float | None


@dataclass(frozen=True)
class Perks:
    # Extra fraction of task pay everywhere.
    task_pay: float = 0.0
    # Extra fraction of energy burned per hour at a building.
    work_energy: float = 0.0
    # Pay per hour while asleep, and while home and awake, as fractions of task_pay_per_hour.
    sleep_pay: float = 0.0
    idle_pay: float = 0.0
    meal_discount: float = 0.0
    # On a penalty day, training gets this much faster instead of slower.
    tired_training: float = 0.0
    # A pet at a building that runs out of food buys a meal on the spot.
    delivered_meals: bool = False
    # A pet at a building that runs out of energy keeps working, but going hungry sends it home.
    burnout_immunity: bool = False
    # Once a campus day, pulling a pet out of a task bills the rest of the hour.
    billed_hours: bool = False
    # Experiments start when the pet goes to work at experiment_buildings, run experiment_hours (in
    # Config), and each running one speeds up all training by experiment_bonus.
    experiments: int = 0
    experiment_bonus: float = 0.0
    experiment_buildings: tuple[str, ...] = ()
    task_sites: dict[str, TaskSite] = field(default_factory=dict)
    market: Market | None = None

    # Job fairs. Extra fraction of Locust Walk time, extra scored runs per fair, and score bonuses: per
    # consecutive training day (up to config.streak_max_days), and at the fair after one where the pet
    # applied and won nothing.
    fair_time: float = 0.0
    fair_retries: int = 0
    streak_score: float = 0.0
    rejection_score: float = 0.0
    # Extra applications at each of the pet's first extra_application_fairs fairs that use them.
    extra_applications: int = 0
    extra_application_fairs: int = 0
    # Applications per fair that may go to a role the pet is one tier short of.
    referrals: int = 0
    # Roles with a top-N cutoff take this many more places.
    top_cutoff: int = 0
    # Applications that may be swapped after they lock, per fair.
    late_changes: int = 0
    # What the pet can see while a fair is open: how many applied to each role, who did, and the tiers of
    # everyone applying to the same roles as the pet.
    see_counts: bool = False
    see_applicants: bool = False
    see_rivals: bool = False
    # Role unlock keys the pet can apply to, and the ones it wins without playing whenever it qualifies.
    unlocks: frozenset[str] = frozenset()
    wins_outright: frozenset[str] = frozenset()
    # A pet that wins nothing at a fair gets its career's reserved role.
    reserved_roles: bool = False

    # Internships. Extra fraction of pay, plus loyalty_pay for each fair in a row the pet keeps the same
    # role. Meals are free while interning with intern_free_food.
    internship_pay: float = 0.0
    loyalty_pay: float = 0.0
    intern_free_food: bool = False
    # When an internship ends without being renewed, this many weeks of its pay as a lump sum.
    exit_bonus_weeks: float = 0.0
    # Share of the best internship pay the pet has ever had, paid whenever it isn't interning.
    vesting: float = 0.0
    # Paid all the time, as a fraction of a normal internship.
    startup_pay: float = 0.0
    # Paid until the next fair, as a share of every internship on the server.
    partner_share: float = 0.0

    # Mostly from shop items. Extra fraction of training XP, of sleep's energy regen, and of walking
    # speed; work_energy_saving takes a fraction off work's energy drain (after work_energy adds to it).
    training_xp: float = 0.0
    sleep_regen: float = 0.0
    travel_speed: float = 0.0
    work_energy_saving: float = 0.0
    # Each hour of sleep counts this much extra toward the next day's rest multiplier.
    rest_bonus: float = 0.0
    # A pet at home (not walking) that runs out of food buys a meal on the spot.
    home_meals: bool = False

    # GPA. A fraction off what events cost in GPA, and extra skill gain while GPA is at least
    # Config.gpa_honors.
    gpa_loss_saving: float = 0.0
    gpa_xp_bonus: float = 0.0
    # GPA recovered each campus week (Monday to Monday) the pet trains at weekly_gpa_buildings (the buildings
    # of the path that grants it) on at least weekly_gpa_days days, up to Config.gpa_max.
    weekly_gpa: float = 0.0
    weekly_gpa_days: int = 0
    weekly_gpa_buildings: tuple[str, ...] = ()
    # While GPA is at least this, the pet may apply to any role it's one tier short of (0 for never).
    referral_gpa: float = 0.0
    # At fair results, applicants with a lower GPA are cut from every role the pet applied to.
    curve_wrecker: bool = False

    # Moves on other pets at a fair. A short seller bets once per fair that a pet wins no role. Backing a
    # pet (once per fair, before the backer's first scored run) adds amicus_score to the backer's runs and
    # amicus_backed_score to the backed pet's runs.
    short_seller: bool = False
    amicus_score: float = 0.0
    amicus_backed_score: float = 0.0


NO_PERKS = Perks()

_NUMBERS = {"task_pay", "work_energy", "sleep_pay", "idle_pay", "meal_discount", "tired_training",
            "experiment_bonus", "fair_time", "streak_score", "rejection_score", "internship_pay",
            "loyalty_pay", "exit_bonus_weeks", "vesting", "startup_pay", "partner_share", "training_xp",
            "sleep_regen", "travel_speed", "work_energy_saving", "rest_bonus", "gpa_loss_saving", "gpa_xp_bonus",
            "weekly_gpa", "referral_gpa", "amicus_score", "amicus_backed_score"}
_COUNTS = {"experiments", "fair_retries", "extra_applications", "extra_application_fairs", "referrals",
           "top_cutoff", "late_changes", "weekly_gpa_days"}
_FLAGS = {"delivered_meals", "burnout_immunity", "billed_hours", "see_counts", "see_applicants",
          "see_rivals", "reserved_roles", "intern_free_food", "home_meals", "curve_wrecker", "short_seller"}
_LISTS = {"unlocks", "wins_outright"}
_TABLES = {
    "task_site": {"building": str, "name": str, "pay": float, "xp": float},
    "market": {"max_share": float, "drift": float, "swing": float, "loss_cap": float},
}


def effect_errors(effects: dict, buildings: dict) -> list[str]:
    """Problems with one tier's effects table, as messages."""
    errors = []
    for key, value in effects.items():
        if key in _NUMBERS:
            if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
                errors.append(f"{key} must be a non-negative number")
        elif key in _COUNTS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"{key} must be a non-negative whole number")
        elif key in _FLAGS:
            if value is not True:
                errors.append(f"{key} can only be true")
        elif key in _LISTS:
            if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
                errors.append(f"{key} must be a list of names")
        elif key in _TABLES:
            if not isinstance(value, dict):
                errors.append(f"{key} must be a table")
                continue
            for sub, sub_value in value.items():
                kind = _TABLES[key].get(sub)
                if kind is None:
                    errors.append(f"{key} has no field {sub}")
                elif kind is float and (isinstance(sub_value, bool) or not isinstance(sub_value, int | float)):
                    errors.append(f"{key}.{sub} must be a number")
                elif kind is str and not isinstance(sub_value, str):
                    errors.append(f"{key}.{sub} must be a string")
            if key == "task_site" and value.get("building") not in buildings:
                errors.append(f"task_site is at unknown building {value.get('building')}")
        else:
            errors.append(f"unknown effect {key}")
    return errors


def perks_for(career: "Career | None", build: dict[str, int], extra: Sequence[dict] = ()) -> Perks:
    """Perks from the tiers of `career` in `build`, plus `extra` effects tables (from items, which can
    only use numbers and flags), applied after every tier."""
    numbers = dict.fromkeys(_NUMBERS, 0.0)
    counts = dict.fromkeys(_COUNTS, 0)
    flags = dict.fromkeys(_FLAGS, False)
    lists: dict[str, set[str]] = {key: set() for key in _LISTS}
    sites: dict[str, TaskSite] = {}
    market: dict = {}
    experiment_buildings: tuple[str, ...] = ()
    gpa_buildings: tuple[str, ...] = ()
    sources = [(path, tier.effects) for path in (career.paths.values() if career else ())
               for tier in path.tiers[: build.get(path.id, 0)]]
    for path, effects in [*sources, *((None, e) for e in extra)]:
        for key, value in effects.items():
            if key in _NUMBERS:
                numbers[key] += value
            elif key in _COUNTS:
                counts[key] += value
            elif key in _FLAGS:
                flags[key] = True
            elif key in _LISTS:
                lists[key].update(value)
            elif key == "market":
                market.update(value)
            elif key == "task_site":
                old = sites.get(value["building"])
                sites[value["building"]] = TaskSite(
                    building=value["building"],
                    name=value.get("name", old.name if old else "Campus task"),
                    pay=value.get("pay", old.pay if old else 0.0),
                    xp=value.get("xp", old.xp if old else 0.0),
                    path=path.id,
                )
            if key == "experiments":
                experiment_buildings = path.buildings
            if key == "weekly_gpa":
                gpa_buildings = path.buildings
    return Perks(
        **numbers,
        **counts,
        **flags,
        **{key: frozenset(names) for key, names in lists.items()},
        experiment_buildings=experiment_buildings,
        weekly_gpa_buildings=gpa_buildings,
        task_sites=sites,
        market=Market(
            max_share=market.get("max_share", 0.0),
            drift=market.get("drift", 0.0),
            swing=market.get("swing", 0.0),
            loss_cap=market.get("loss_cap"),
        ) if market else None,
    )


def market_move(day_start: float, market: Market, config: Config) -> float:
    """How much an invested pet's portfolio changes at `day_start`, as a fraction. Every pet on the
    server sees the same day's z, so a bad day is bad for everyone."""
    digest = hashlib.sha256(f"{config.market_seed}:{int(day_start)}".encode()).digest()
    z = int.from_bytes(digest[:8]) / 2**64 * 2 - 1
    move = market.drift + market.swing * z
    if market.loss_cap is not None:
        move = max(move, -market.loss_cap)
    return move
