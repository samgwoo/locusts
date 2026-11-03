"""Pet state and the timestamp-driven simulation.

There is no game loop. A stored pet is a snapshot at `updated_at`, and `advance` replays everything
that happened between then and now. Time is cut into segments where every rate is constant, split at
campus-clock boundaries (night start/end), penalty and coffee windows, experiment ends, internship and
bonus windows, trip arrivals, subscription bills, an event's deadline, and the exact moments a stat hits
empty or full or the pet has worked long enough for its next event.
Within a segment every stat moves linearly, so the result is exact and does not depend on how often the
pet is looked at.
"""

import copy
import math
from dataclasses import dataclass, field
from enum import StrEnum

from locusts import campus_map, campus_time, semesters, shop
from locusts import events as event_book
from locusts.config import DEFAULT_CONFIG, MAX_STAT, Config
from locusts.houses import houses
from locusts.perks import NO_PERKS, Perks, market_move, perks_for
from locusts.upgrades import careers_for

_MAX_STEPS = 10_000
# How close to full counts as full: summing a rate over many segments can leave a stat a hair short.
_FULL_EPSILON = 1e-6
WEEK_HOURS = 7 * 24
# The path of a pet TRAINING at the gym, which builds fitness instead of career XP.
FITNESS = "fitness"


class Activity(StrEnum):
    HOME = "home"
    SLEEPING = "sleeping"
    TRAINING = "training"
    TASK = "task"


WORKING = (Activity.TRAINING, Activity.TASK)


@dataclass
class Pet:
    name: str
    fullness: float
    energy: float
    money: float
    activity: Activity
    building: str | None
    # The career the pet declared when it hatched (None only for pets from before careers were picked),
    # and the path of it being trained while activity is TRAINING.
    career: str | None
    path: str | None
    # career -> path -> unspent XP, and career -> path -> tiers owned.
    xp: dict[str, dict[str, float]]
    upgrades: dict[str, dict[str, int]]
    rest_multiplier: float
    sleep_hours: float
    day_started_at: float
    penalty_start: float | None
    penalty_end: float | None
    coffee_until: float | None
    updated_at: float
    # Money in the market, which moves once a day and isn't spendable until withdrawn.
    invested: float = 0.0
    # When each running Wet Lab experiment ends.
    experiments: list[float] = field(default_factory=list)
    # When the current stint at a building began, and the campus day whose billed hour was used.
    work_started_at: float | None = None
    billed_day: float | None = None
    # The latest internship won at a job fair, kept after it ends: role, company, title, pay (per hour,
    # before perks), start, until, kept (fairs in a row it was renewed), and lost_on_exhaustion.
    internship: dict | None = None
    # The highest per-hour pay of any internship the pet has won, before perks.
    best_internship_pay: float = 0.0
    # Pay from other pets' results, each {name, per_hour, start, until}.
    bonuses: list[dict] = field(default_factory=list)
    # The last campus day the pet trained, and how many days in a row that makes.
    train_day: float | None = None
    train_streak: int = 0
    # For Perks.weekly_gpa: the start of the campus week being counted, how many days of it the pet trained at
    # the perk's buildings, and the last of those days.
    study_week: float | None = None
    study_days: int = 0
    study_day: float | None = None
    # The walk to `building` (home when None): {from, to, start, arrive}, with map points. Until it
    # arrives the pet isn't working or sleeping yet, whatever its activity says it's headed to do.
    trip: dict | None = None
    # Shop items owned for good, and running subscriptions: item -> {until (when the next bill is due),
    # renew (False once cancelled, so it ends at until)}. Swipes are meal plan meals left this period.
    items: list[str] = field(default_factory=list)
    subscriptions: dict[str, dict] = field(default_factory=dict)
    swipes: int = 0
    # Hours worked out at the gym, which raise max energy (see Config.gym).
    fitness: float = 0.0
    # The college house the pet lives in (see locusts/houses.toml): where "home" is.
    house: str = DEFAULT_CONFIG.default_house
    # GPA, which events move (0 to Config.gpa_max).
    gpa: float = DEFAULT_CONFIG.starting_gpa
    # Hours spent at work in all, the total at which the next event comes up (set once the pet is at work),
    # how many events it has been offered, and the one waiting for an answer: {id, at, expires, path,
    # building} (see locusts/events.py).
    work_hours: float = 0.0
    next_event_at: float | None = None
    events_seen: int = 0
    event: dict | None = None
    # The semester the pet was hatched in (see locusts/semesters.py), and how many times it has ordered
    # DoorDash because it was starving.
    semester: int | None = None
    doordashes: int = 0
    id: int | None = None


@dataclass(frozen=True)
class Event:
    at: float
    kind: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Rates:
    """Per-hour change of each quantity over a segment."""

    energy: float
    fullness: float
    sleep: float
    # Path -> XP per hour.
    xp: dict[str, float]
    money: float
    fitness: float = 0.0
    # Hours of work counting toward the next event (1 while at work, except working out at the gym).
    work: float = 0.0


def new_pet(name: str, now: float, config: Config = DEFAULT_CONFIG, career: str | None = None,
            house: str | None = None) -> Pet:
    sleep_hours = 0.0
    if campus_time.is_night(now, config):
        night_start = campus_time.last_occurrence(now, config.night_start, config)
        sleep_hours = (now - night_start) / 3600
    pet = Pet(
        name=name,
        fullness=MAX_STAT,
        energy=MAX_STAT,
        money=config.starting_money,
        activity=Activity.HOME,
        building=None,
        career=career,
        path=None,
        xp={},
        upgrades={},
        rest_multiplier=1.0,
        sleep_hours=sleep_hours,
        day_started_at=campus_time.current_day_start(now, config),
        penalty_start=None,
        penalty_end=None,
        coffee_until=None,
        updated_at=now,
        house=house or config.default_house,
        semester=semesters.number_at(now, config),
    )
    return advance(pet, now, config)[0]


def rest_multiplier_for(sleep_hours: float, config: Config = DEFAULT_CONFIG) -> float:
    curve = config.rest_curve
    if sleep_hours <= curve[0][0]:
        return curve[0][1]
    for (h0, m0), (h1, m1) in zip(curve, curve[1:]):
        if sleep_hours <= h1:
            return m0 + (m1 - m0) * (sleep_hours - h0) / (h1 - h0)
    return curve[-1][1]


def penalty_strength(pet: Pet, t: float, config: Config = DEFAULT_CONFIG) -> float:
    """0 when the pet is unpenalized, 1 for the full exhaustion penalty, in between with coffee."""
    if pet.penalty_start is None or not (pet.penalty_start <= t < pet.penalty_end):
        return 0.0
    if coffee_active(pet, t):
        return 1.0 - config.coffee_mitigation
    return 1.0


def coffee_active(pet: Pet, t: float) -> bool:
    return pet.coffee_until is not None and t < pet.coffee_until


def perks_of(pet: Pet, config: Config = DEFAULT_CONFIG) -> Perks:
    career = careers_for(config).get(pet.career)
    build = pet.upgrades.get(career.id, {}) if career else {}
    house = houses().get(pet.house)
    extra = [*shop.owned_effects(pet.items, pet.subscriptions), *([house.effects] if house else [])]
    return perks_for(career, build, extra)


def fitness_level(pet: Pet, config: Config = DEFAULT_CONFIG) -> int:
    return min(config.fitness_max_levels, int(pet.fitness // config.fitness_hours_per_level))


def max_energy(pet: Pet, config: Config = DEFAULT_CONFIG) -> float:
    return MAX_STAT + fitness_level(pet, config) * config.fitness_energy_per_level


def meal_price(perks: Perks, config: Config = DEFAULT_CONFIG, pet: Pet | None = None,
               t: float | None = None) -> float:
    if perks.intern_free_food and pet is not None and interning(pet, t):
        return 0.0
    return config.meal_price * (1 - perks.meal_discount)


def running_experiments(pet: Pet, t: float) -> int:
    return sum(end > t for end in pet.experiments)


def spot(building: str | None, config: Config = DEFAULT_CONFIG, house: str | None = None) -> tuple[float, float]:
    """Where a pet at `building` stands on the campus map, or at home in `house` when building is None."""
    if building:
        name = config.buildings[building].map_name
    else:
        name = houses()[house or config.default_house].map_name
    return campus_map.load().anchors[name]


def in_transit(pet: Pet, t: float) -> bool:
    return pet.trip is not None and t < pet.trip["arrive"]


def position(pet: Pet, t: float, config: Config = DEFAULT_CONFIG) -> tuple[float, float]:
    """The pet's map point at `t`, partway along its trip if it's still walking."""
    if not in_transit(pet, t):
        return spot(pet.building, config, pet.house)
    trip = pet.trip
    s = (t - trip["start"]) / (trip["arrive"] - trip["start"])
    (x0, y0), (x1, y1) = trip["from"], trip["to"]
    return (x0 + (x1 - x0) * s, y0 + (y1 - y0) * s)


def start_trip(pet: Pet, t: float, building: str | None, config: Config = DEFAULT_CONFIG) -> None:
    """Send the pet walking from wherever it is at `t` to `building` (home when None)."""
    origin = position(pet, t, config)
    dest = spot(building, config, pet.house)
    speed = config.travel_px_per_second * (1 + perks_of(pet, config).travel_speed)
    seconds = math.dist(origin, dest) / speed
    pet.trip = {"from": list(origin), "to": list(dest), "start": t, "arrive": t + seconds}
    pet.building = building


def interning(pet: Pet, t: float) -> bool:
    job = pet.internship
    return job is not None and job["start"] <= t < job["until"]


def job_pay(job: dict, perks: Perks) -> float:
    """What an internship pays per hour with the pet's perks."""
    return job["pay"] * (1 + perks.internship_pay + perks.loyalty_pay * job["kept"])


def income(pet: Pet, t: float, config: Config = DEFAULT_CONFIG, perks: Perks = NO_PERKS) -> float:
    """Money per hour that doesn't depend on what the pet is doing: its internship (or the vested share
    of its best one), a startup, and bonuses from other pets' internships."""
    vested = perks.vesting * pet.best_internship_pay * (1 + perks.internship_pay)
    rate = max(job_pay(pet.internship, perks) if interning(pet, t) else 0.0, vested)
    rate += perks.startup_pay * config.internship_pay_per_week / WEEK_HOURS
    return rate + sum(b["per_hour"] for b in pet.bonuses if b["start"] <= t < b["until"])


def task_pay(pet: Pet, t: float, config: Config = DEFAULT_CONFIG, perks: Perks = NO_PERKS) -> float:
    """Money per hour from what the pet is doing right now."""
    if in_transit(pet, t):
        return 0.0
    if pet.activity is Activity.TASK:
        site = perks.task_sites.get(pet.building)
        bonus = perks.task_pay + (site.pay if site else 0.0)
        return config.task_pay_per_hour * work_speed(pet, t, config, perks) * (1 + bonus)
    if pet.activity is Activity.SLEEPING:
        return config.task_pay_per_hour * perks.sleep_pay
    if pet.activity is Activity.HOME:
        return config.task_pay_per_hour * perks.idle_pay
    return 0.0


def training_streak(pet: Pet, t: float, config: Config = DEFAULT_CONFIG) -> int:
    """Consecutive campus days the pet has trained, counting today or ending yesterday."""
    if pet.train_day is None:
        return 0
    today = campus_time.current_day_start(t, config)
    yesterday = campus_time.current_day_start(today - 1, config)
    return pet.train_streak if pet.train_day in (today, yesterday) else 0


def work_speed(pet: Pet, t: float, config: Config = DEFAULT_CONFIG, perks: Perks = NO_PERKS) -> float:
    """Multiplier on XP and pay earned per hour at a building."""
    penalty = penalty_strength(pet, t, config)
    worst = config.exhausted_work_multiplier
    if pet.activity is Activity.TRAINING and perks.tired_training:
        worst = 1.0 + perks.tired_training
    speed = pet.rest_multiplier * _lerp(1.0, worst, penalty)
    if coffee_active(pet, t):
        speed *= 1 + config.coffee_work_boost
    return speed


def at_work(pet: Pet, t: float) -> bool:
    """Training or doing a task at its building, not still walking there."""
    return pet.activity in WORKING and not in_transit(pet, t)


def rates(pet: Pet, t: float, config: Config = DEFAULT_CONFIG, perks: Perks = NO_PERKS) -> Rates:
    sleeping = pet.activity is Activity.SLEEPING
    working = at_work(pet, t)

    top = max_energy(pet, config)
    if sleeping:
        energy = config.sleep_energy_regen_per_hour * top / MAX_STAT * (1 + perks.sleep_regen)
    else:
        base = config.work_energy_drain_per_hour if working else config.idle_energy_drain_per_hour
        if working:
            base *= (1 + perks.work_energy) * (1 - perks.work_energy_saving)
        penalty = penalty_strength(pet, t, config)
        energy = -base * _lerp(1.0, config.exhausted_energy_multiplier, penalty)
    if (energy > 0 and pet.energy >= top) or (energy < 0 and pet.energy <= 0):
        energy = 0.0

    fullness = -config.fullness_drain_per_hour
    if sleeping:
        fullness *= config.sleeping_fullness_multiplier
    if pet.fullness <= 0:
        fullness = 0.0

    speed = work_speed(pet, t, config, perks) if working else 0.0
    xp: dict[str, float] = {}
    fitness = 0.0
    if working and pet.activity is Activity.TRAINING and pet.path == FITNESS:
        fitness = speed
    elif working and pet.activity is Activity.TRAINING and pet.path:
        boost = 1 + perks.experiment_bonus * running_experiments(pet, t) + perks.training_xp
        if pet.gpa >= config.gpa_honors:
            boost += perks.gpa_xp_bonus
        xp[pet.path] = config.xp_per_hour * speed * boost
    site = perks.task_sites.get(pet.building)
    if working and pet.activity is Activity.TASK and site and site.xp:
        xp[site.path] = xp.get(site.path, 0.0) + config.xp_per_hour * speed * site.xp
    money = task_pay(pet, t, config, perks) + income(pet, t, config, perks)
    return Rates(energy=energy, fullness=fullness, sleep=1.0 if sleeping else 0.0, xp=xp, money=money,
                 fitness=fitness, work=1.0 if _counts_for_events(pet, t) else 0.0)


def _counts_for_events(pet: Pet, t: float) -> bool:
    """Whether the pet's time now counts toward its next event: at work, except working out."""
    return at_work(pet, t) and not (pet.activity is Activity.TRAINING and pet.path == FITNESS)


def advance(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> tuple[Pet, list[Event]]:
    """Return a copy of `pet` brought forward to `now`, plus what happened along the way."""
    if now < pet.updated_at:
        raise ValueError(f"cannot advance pet backwards ({now} < {pet.updated_at})")

    pet = copy.deepcopy(pet)
    perks = perks_of(pet, config)
    events: list[Event] = []
    t = pet.updated_at

    for _ in range(_MAX_STEPS):
        if _bill_subscriptions(pet, t, config, events):
            perks = perks_of(pet, config)
        _settle(pet, t, config, events, perks)
        if t >= now:
            break

        end = min(now, _next_boundary(pet, t, config))
        hours = (end - t) / 3600
        r = rates(pet, t, config, perks)

        threshold = _first_threshold(pet, r, config)
        # A stat that would empty or fill within rounding of the segment's end snaps there exactly.
        if threshold is not None and threshold[0] <= hours + 1e-9:
            hours, snap = threshold
            end = min(end, t + hours * 3600)
        else:
            snap = None

        if pet.activity is Activity.TRAINING and pet.path != FITNESS and at_work(pet, t) and end > t:
            _count_training_day(pet, config)
            if perks.weekly_gpa and pet.building in perks.weekly_gpa_buildings:
                _count_study_day(pet, t, config, events, perks)
        _apply(pet, r, hours, snap, config)
        if snap == "fullness_empty":
            events.append(Event(end, "starving"))
        elif snap == "event_due":
            pet.event = event_book.offer(pet, end, config)
            pet.events_seen += 1
            pet.next_event_at = None
            if pet.event:
                events.append(Event(end, "event_offered", {"id": pet.event["id"]}))
        t = end
    else:
        raise RuntimeError(f"pet simulation did not converge after {_MAX_STEPS} steps")

    pet.updated_at = now
    return pet, events


def _settle(pet: Pet, t: float, config: Config, events: list[Event], perks: Perks) -> None:
    """Apply every instantaneous transition that is due at time `t`. Idempotent."""
    next_day = campus_time.next_day_start(pet.day_started_at, config)
    if next_day <= t:
        pet.rest_multiplier = rest_multiplier_for(pet.sleep_hours * (1 + perks.rest_bonus), config)
        events.append(
            Event(next_day, "new_day", {"sleep_hours": pet.sleep_hours, "rest": pet.rest_multiplier})
        )
        pet.sleep_hours = 0.0
        pet.day_started_at = next_day
        if pet.activity is Activity.SLEEPING:
            pet.activity = Activity.HOME
        if pet.invested > 0 and perks.market:
            move = market_move(next_day, perks.market, config)
            pet.invested *= 1 + move
            events.append(Event(next_day, "market", {"move": move, "invested": pet.invested}))

    finished = [end for end in pet.experiments if end <= t]
    if finished:
        pet.experiments = [end for end in pet.experiments if end > t]
        events.extend(Event(end, "experiment_done") for end in finished)

    if any(b["until"] <= t for b in pet.bonuses):
        pet.bonuses = [b for b in pet.bonuses if b["until"] > t]

    if pet.trip is not None and pet.trip["arrive"] <= t:
        _arrive(pet, config, events, perks)

    # An event nobody answered lets go; the next one is drawn once the pet is at work with none waiting.
    if pet.event is not None and pet.event["expires"] <= t:
        events.append(Event(pet.event["expires"], "event_missed", {"id": pet.event["id"]}))
        pet.event = None
    if pet.event is None and pet.next_event_at is None and _counts_for_events(pet, t):
        pet.next_event_at = pet.work_hours + event_book.gap_hours(pet, config)

    working = at_work(pet, t)
    starving = pet.fullness <= 0
    if starving:
        _eat(pet, t, config, events, perks)

    if working and pet.energy <= 0 and not perks.burnout_immunity:
        _exhaust(pet, t, config)
        events.append(Event(t, "exhausted", {"penalty_start": pet.penalty_start}))
        if interning(pet, t) and pet.internship["lost_on_exhaustion"]:
            end_internship(pet, t, config, perks)
            events.append(Event(t, "internship_lost", {"role": pet.internship["role"]}))
    elif working and starving and perks.burnout_immunity:
        send_home(pet, t, config)
        events.append(Event(t, "went_home_hungry"))

    night = campus_time.is_night(t, config)
    if night and pet.activity is Activity.HOME and not in_transit(pet, t):
        pet.activity = Activity.SLEEPING
    elif not night and pet.activity is Activity.SLEEPING and pet.energy >= max_energy(pet, config) - _FULL_EPSILON:
        pet.activity = Activity.HOME


def _eat(pet: Pet, t: float, config: Config, events: list[Event], perks: Perks) -> None:
    """A starving pet swipes in anywhere while it has meal plan swipes, then buys a meal if a perk brings
    one to it and it can pay (delivered at a building, or from its fridge at home), and otherwise orders
    DoorDash, going into debt if it has to."""
    home = pet.activity in (Activity.HOME, Activity.SLEEPING) and not in_transit(pet, t)
    price = meal_price(perks, config, pet, t)
    affords = price <= 0 or pet.money >= price
    if pet.swipes > 0:
        pet.swipes -= 1
        kind, food = "meal_swipe", config.meal_fullness
    elif at_work(pet, t) and perks.delivered_meals and affords:
        pet.money -= price
        kind, food = "meal_delivered", config.meal_fullness
    elif home and perks.home_meals and affords:
        pet.money -= price
        kind, food = "meal_at_home", config.meal_fullness
    else:
        pet.money -= config.doordash_price
        pet.doordashes += 1
        kind, food = "doordash", config.doordash_fullness
    pet.fullness = min(MAX_STAT, pet.fullness + food)
    events.append(Event(t, kind))


def _bill_subscriptions(pet: Pet, t: float, config: Config, events: list[Event]) -> bool:
    """Renew every subscription due by `t`, or end it if it was cancelled or the pet can't pay.
    Returns whether any ended, since that changes the pet's perks."""
    ended = False
    for item_id, sub in list(pet.subscriptions.items()):
        if sub["until"] > t:
            continue
        due = sub["until"]
        item = shop.catalog().items.get(item_id)
        if item and sub["renew"] and pet.money >= item.price:
            pet.money -= item.price
            sub["until"] = due + config.subscription_days * 86400
            if item.swipes:
                pet.swipes = item.swipes
            events.append(Event(due, "subscription_renewed", {"item": item_id}))
            continue
        del pet.subscriptions[item_id]
        if item and item.swipes:
            pet.swipes = 0
        ended = True
        events.append(Event(due, "subscription_ended", {"item": item_id, "cancelled": not sub["renew"]}))
    return ended


def _arrive(pet: Pet, config: Config, events: list[Event], perks: Perks) -> None:
    """End the pet's trip. Work at a building counts from the moment it gets there."""
    t = pet.trip["arrive"]
    pet.trip = None
    if pet.activity not in WORKING:
        return
    pet.work_started_at = t
    if pet.building in perks.experiment_buildings:
        free = perks.experiments - running_experiments(pet, t)
        pet.experiments += [t + config.experiment_hours * 3600] * free
    events.append(Event(t, "arrived", {"building": pet.building}))


def _exhaust(pet: Pet, t: float, config: Config) -> None:
    """Send the pet home and penalize the whole next day, extending any penalty already running."""
    send_home(pet, t, config)
    start = campus_time.next_day_start(t, config)
    end = campus_time.next_day_start(start, config)
    if pet.penalty_end is None or pet.penalty_end <= t:
        pet.penalty_start = start
    pet.penalty_end = end


def send_home(pet: Pet, t: float, config: Config = DEFAULT_CONFIG) -> None:
    pet.activity = Activity.HOME
    pet.path = None
    pet.work_started_at = None
    start_trip(pet, t, None, config)


def end_internship(pet: Pet, t: float, config: Config = DEFAULT_CONFIG, perks: Perks = NO_PERKS) -> None:
    """Cut the pet's internship short at `t`, paying any exit bonus its perks give."""
    pet.money += exit_bonus(pet.internship, perks)
    pet.internship = {**pet.internship, "until": t}


def exit_bonus(job: dict, perks: Perks) -> float:
    return perks.exit_bonus_weeks * WEEK_HOURS * job_pay(job, perks)


def _count_training_day(pet: Pet, config: Config) -> None:
    day = pet.day_started_at
    if pet.train_day == day:
        return
    yesterday = campus_time.current_day_start(day - 1, config)
    pet.train_streak = pet.train_streak + 1 if pet.train_day == yesterday else 1
    pet.train_day = day


def _count_study_day(pet: Pet, t: float, config: Config, events: list[Event], perks: Perks) -> None:
    """Count today toward this week's weekly_gpa, which lands the day the count is reached."""
    if pet.study_day == pet.day_started_at:
        return
    week = campus_time.week_start(pet.day_started_at, config)
    if pet.study_week != week:
        pet.study_week, pet.study_days = week, 0
    pet.study_day = pet.day_started_at
    pet.study_days += 1
    if pet.study_days == perks.weekly_gpa_days:
        gained = min(config.gpa_max, pet.gpa + perks.weekly_gpa) - pet.gpa
        pet.gpa += gained
        events.append(Event(t, "gpa_recovered", {"gpa": gained}))


def _next_boundary(pet: Pet, t: float, config: Config) -> float:
    candidates = [
        campus_time.next_occurrence(t, config.night_start, config),
        campus_time.next_occurrence(t, config.night_end, config),
    ]
    windows = [(b["start"], b["until"]) for b in pet.bonuses]
    if pet.internship:
        windows.append((pet.internship["start"], pet.internship["until"]))
    arrive = pet.trip["arrive"] if pet.trip else None
    bills = (s["until"] for s in pet.subscriptions.values())
    expires = pet.event["expires"] if pet.event else None
    for ts in (pet.penalty_start, pet.penalty_end, pet.coffee_until, arrive, expires, *pet.experiments, *bills,
               *(ts for window in windows for ts in window)):
        if ts is not None and ts > t:
            candidates.append(ts)
    return min(candidates)


def _first_threshold(pet: Pet, r: Rates, config: Config) -> tuple[float, str] | None:
    """Hours until the first stat reaches empty or full under rates `r`, and which one."""
    hits: list[tuple[float, str]] = []
    top = max_energy(pet, config)
    if r.energy < 0 and pet.energy > 0:
        hits.append((pet.energy / -r.energy, "energy_empty"))
    if r.energy > 0 and pet.energy < top:
        hits.append(((top - pet.energy) / r.energy, "energy_full"))
    if r.fullness < 0 and pet.fullness > 0:
        hits.append((pet.fullness / -r.fullness, "fullness_empty"))
    if r.work and pet.event is None and pet.next_event_at is not None:
        hits.append((max(0.0, pet.next_event_at - pet.work_hours), "event_due"))
    return min(hits) if hits else None


def _apply(pet: Pet, r: Rates, hours: float, snap: str | None, config: Config) -> None:
    top = max_energy(pet, config)
    pet.energy = _clamp(pet.energy + r.energy * hours, top)
    pet.fullness = _clamp(pet.fullness + r.fullness * hours)
    pet.sleep_hours += r.sleep * hours
    pet.work_hours += r.work * hours
    pet.money += r.money * hours
    pet.fitness = min(pet.fitness + r.fitness * hours,
                      max(pet.fitness, config.fitness_max_levels * config.fitness_hours_per_level))
    if r.xp and pet.career:
        bank = pet.xp.setdefault(pet.career, {})
        for path, rate in r.xp.items():
            bank[path] = bank.get(path, 0.0) + rate * hours

    if snap == "energy_empty":
        pet.energy = 0.0
    elif snap == "energy_full":
        pet.energy = top
    elif snap == "fullness_empty":
        pet.fullness = 0.0


def _clamp(value: float, top: float = MAX_STAT) -> float:
    return max(0.0, min(top, value))


def _lerp(a: float, b: float, s: float) -> float:
    return a + (b - a) * s
