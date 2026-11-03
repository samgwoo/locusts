"""Player actions. Each one brings the pet up to `now`, validates the request, and applies it.

Actions never trust client state: they take a stored pet and a server timestamp and return the new pet.
"""

import math

from locusts import campus_time, shop
from locusts import events as event_book
from locusts.config import DEFAULT_CONFIG, MAX_STAT, Config
from locusts.pet import (
    FITNESS,
    WORKING,
    Activity,
    Pet,
    advance,
    at_work,
    fitness_level,
    in_transit,
    max_energy,
    meal_price,
    perks_of,
    send_home,
    start_trip,
    task_pay,
)
from locusts.upgrades import UpgradePath, careers_for, upgrade_blocker


class ActionError(Exception):
    pass


def feed(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _at(pet, now, config)
    if pet.fullness >= MAX_STAT:
        raise ActionError("pet is already full")
    if pet.swipes > 0:
        pet.swipes -= 1
    else:
        _spend(pet, meal_price(perks_of(pet, config), config, pet, now))
    pet.fullness = min(MAX_STAT, pet.fullness + config.meal_fullness)
    return _at(pet, now, config)


def sleep(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _at(pet, now, config)
    if pet.activity is Activity.SLEEPING:
        raise ActionError("pet is already asleep")
    if pet.activity in WORKING:
        raise ActionError("pull the pet out of its building first")
    if in_transit(pet, now):
        raise ActionError("pet is still walking home")
    if pet.energy >= max_energy(pet, config):
        raise ActionError("pet is not tired")
    pet.activity = Activity.SLEEPING
    return _at(pet, now, config)


def wake(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _at(pet, now, config)
    if pet.activity is not Activity.SLEEPING:
        raise ActionError("pet is not asleep")
    if campus_time.is_night(now, config):
        raise ActionError("pets sleep at night; send it to a building to keep it up")
    pet.activity = Activity.HOME
    return _at(pet, now, config)


def declare_career(pet: Pet, now: float, career: str, config: Config = DEFAULT_CONFIG) -> Pet:
    """Give a pet from before careers were picked at hatching its career. Careers can't be changed."""
    careers = careers_for(config)
    if career not in careers:
        raise ActionError(f"unknown career {career}")
    pet = _at(pet, now, config)
    if pet.career is not None:
        raise ActionError(f"{pet.name} is already on the {careers[pet.career].name} track")
    pet.career = career
    return pet


def train(pet: Pet, now: float, building: str, path: str, config: Config = DEFAULT_CONFIG) -> Pet:
    """Train a path of the pet's career at one of its buildings. Its XP banks toward the next tier."""
    upgrade_path = _path(pet, path, config)
    if building not in upgrade_path.buildings:
        raise ActionError(f"{upgrade_path.name} doesn't train at {building}")
    pet = _start_work(pet, now, building, config)
    blocker = _blocker(pet, path, config)
    if blocker:
        raise ActionError(f"can't train {upgrade_path.name}: {blocker}")
    pet.activity = Activity.TRAINING
    pet.path = path
    return _at(pet, now, config)


def task(pet: Pet, now: float, building: str, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _start_work(pet, now, building, config)
    pet.activity = Activity.TASK
    pet.path = None
    return _at(pet, now, config)


def work_out(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> Pet:
    """Train fitness at the gym, which any career can do. It costs time and energy, not money."""
    if fitness_level(pet, config) >= config.fitness_max_levels:
        raise ActionError(f"{pet.name} is as fit as it gets")
    pet = _start_work(pet, now, config.gym, config)
    pet.activity = Activity.TRAINING
    pet.path = FITNESS
    return _at(pet, now, config)


def buy(pet: Pet, now: float, item_id: str, config: Config = DEFAULT_CONFIG) -> Pet:
    """Buy a shop item for good, or start (or un-cancel) a subscription."""
    item = shop.catalog().items.get(item_id)
    if item is None:
        raise ActionError(f"unknown item {item_id}")
    pet = _at(pet, now, config)
    if item.weekly:
        sub = pet.subscriptions.get(item_id)
        if sub and sub["renew"]:
            raise ActionError(f"already subscribed to {item.name}")
        if sub:
            sub["renew"] = True
            return _at(pet, now, config)
        _spend(pet, item.price)
        pet.subscriptions[item_id] = {"until": now + config.subscription_days * 86400, "renew": True}
        if item.swipes:
            pet.swipes = item.swipes
        return _at(pet, now, config)
    if item_id in pet.items:
        raise ActionError(f"{pet.name} already has a {item.name}")
    upgrade = shop.upgrade_of(item_id)
    if upgrade and upgrade.id in pet.items:
        raise ActionError(f"{pet.name} already has a {upgrade.name}")
    if item.replaces and item.replaces not in pet.items:
        raise ActionError(f"{item.name} needs a {shop.catalog().items[item.replaces].name} to trade in")
    _spend(pet, item.price)
    pet.items = [i for i in pet.items if i != item.replaces] + [item_id]
    return _at(pet, now, config)


def cancel(pet: Pet, now: float, item_id: str, config: Config = DEFAULT_CONFIG) -> Pet:
    """Stop a subscription from renewing. It keeps working until the time already paid for runs out."""
    pet = _at(pet, now, config)
    sub = pet.subscriptions.get(item_id)
    if sub is None or not sub["renew"]:
        raise ActionError("not subscribed")
    sub["renew"] = False
    return _at(pet, now, config)


def buy_tier(pet: Pet, now: float, path: str, config: Config = DEFAULT_CONFIG) -> Pet:
    """Spend a path's banked XP on its next tier, following the crosspath rule."""
    upgrade_path = _path(pet, path, config)
    career = pet.career
    pet = _at(pet, now, config)
    blocker = _blocker(pet, path, config)
    if blocker:
        raise ActionError(blocker)
    owned = pet.upgrades.get(career, {}).get(path, 0)
    tier = upgrade_path.tiers[owned]
    banked = pet.xp.get(career, {}).get(path, 0.0)
    if banked < tier.cost:
        raise ActionError(f"{tier.name} costs {tier.cost:g} XP; {upgrade_path.name} has {banked:.0f}")

    pet.xp[career][path] = banked - tier.cost
    pet.upgrades.setdefault(career, {})[path] = owned + 1
    if pet.activity is Activity.TRAINING and pet.path != FITNESS and _blocker(pet, pet.path, config):
        send_home(pet, now, config)
    return _at(pet, now, config)


def recall(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> Pet:
    """Walk the pet home from its building, or turn it around if it hasn't got there yet."""
    pet = _at(pet, now, config)
    if pet.activity not in WORKING:
        raise ActionError("pet is not at a building")
    perks = perks_of(pet, config)
    if (perks.billed_hours and pet.activity is Activity.TASK and at_work(pet, now)
            and pet.work_started_at is not None and pet.billed_day != pet.day_started_at):
        worked = (now - pet.work_started_at) / 3600
        pet.money += (math.ceil(worked) - worked) * task_pay(pet, now, config, perks)
        pet.billed_day = pet.day_started_at
    send_home(pet, now, config)
    return _at(pet, now, config)


def drink_coffee(pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _at(pet, now, config)
    _spend(pet, config.coffee_price)
    pet.coffee_until = max(pet.coffee_until or now, now) + config.coffee_hours * 3600
    return _at(pet, now, config)


def invest(pet: Pet, now: float, amount: float, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _at(pet, now, config)
    limit = max_investment(pet, config)
    if amount <= 0:
        raise ActionError("invest a positive amount")
    if amount > pet.money:
        raise ActionError("not enough money")
    if amount > limit + 0.005:
        raise ActionError(f"you can invest at most ${limit:.2f} more")
    pet.money -= amount
    pet.invested += amount
    return _at(pet, now, config)


def withdraw(pet: Pet, now: float, amount: float, config: Config = DEFAULT_CONFIG) -> Pet:
    pet = _at(pet, now, config)
    if amount <= 0:
        raise ActionError("withdraw a positive amount")
    if amount > pet.invested + 0.005:
        raise ActionError(f"only ${pet.invested:.2f} is invested")
    amount = min(amount, pet.invested)
    pet.invested -= amount
    pet.money += amount
    return _at(pet, now, config)


def answer_event(pet: Pet, now: float, option: int, config: Config = DEFAULT_CONFIG) -> tuple[Pet, dict]:
    """Take option `option` of the pet's waiting event, returning the pet and the effects as they landed."""
    pet = _at(pet, now, config)
    if pet.event is None:
        raise ActionError("there's no event waiting, or it already passed")
    event = event_book.templates()[pet.event["id"]]
    if not 0 <= option < len(event.options):
        raise ActionError("that isn't one of the options")
    effects = event.options[option].effects
    cost = -effects.get("money", 0)
    if cost > 0 and pet.money < cost:
        raise ActionError("not enough money")
    if effects.get("energy", 0) < 0 and pet.energy + effects["energy"] <= 0:
        raise ActionError(f"{pet.name} is too tired for that")
    landed = event_book.apply(pet, pet.event, option, perks_of(pet, config), config)
    pet.event = None
    return _at(pet, now, config), landed


def max_investment(pet: Pet, config: Config = DEFAULT_CONFIG) -> float:
    """How much more of its cash the pet may put in the market right now."""
    market = perks_of(pet, config).market
    if market is None:
        raise ActionError(f"{pet.name} can't invest yet")
    room = market.max_share * (pet.money + pet.invested) - pet.invested
    return max(0.0, min(pet.money, room))


def _start_work(pet: Pet, now: float, building: str, config: Config) -> Pet:
    if building not in config.buildings:
        raise ActionError(f"unknown building {building}")
    pet = _at(pet, now, config)
    if pet.activity in WORKING:
        raise ActionError("pet is already at a building")
    if pet.energy < config.min_energy_to_work:
        raise ActionError("pet is too tired to work")
    start_trip(pet, now, building, config)
    return pet


def _path(pet: Pet, path: str, config: Config) -> UpgradePath:
    careers = careers_for(config)
    if pet.career is None:
        raise ActionError(f"{pet.name} needs to pick a career first")
    if path not in careers[pet.career].paths:
        raise ActionError(f"{careers[pet.career].name} has no path {path}")
    return careers[pet.career].paths[path]


def _blocker(pet: Pet, path: str, config: Config) -> str | None:
    career = careers_for(config)[pet.career]
    return upgrade_blocker(career, pet.upgrades.get(career.id, {}), path, config)


def _spend(pet: Pet, amount: float) -> None:
    if amount > 0 and pet.money < amount:
        raise ActionError("not enough money")
    pet.money -= amount


def _at(pet: Pet, now: float, config: Config) -> Pet:
    """Bring the pet to `now`. Called again after a change so its knock-on transitions apply too,
    like a recalled pet falling asleep at night."""
    return advance(pet, now, config)[0]
