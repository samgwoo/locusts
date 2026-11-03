import dataclasses
import json
import math
from pathlib import Path

import pytest
from helpers import HOUR, et

from locusts import actions, shop
from locusts.actions import ActionError
from locusts.config import DEFAULT_CONFIG as C
from locusts.pet import Activity, advance, max_energy, new_pet, perks_of, spot
from locusts.shop import ShopError, load_catalog

MON_9AM = et(2026, 10, 5, 9)
MON_NOON = et(2026, 10, 5, 12)
WEEK = C.subscription_days * 24 * HOUR


def hatch(**fields):
    return dataclasses.replace(new_pet("larva", MON_9AM, career="tech"), **fields)


def rich(**fields):
    return hatch(money=1000.0, **fields)


def grind_xp(pet) -> float:
    return pet.xp["tech"]["grind"]


# --- Catalog ---


def test_the_catalog_loads_and_every_item_is_buyable():
    items = shop.catalog().items
    assert {"thinkpad", "macbook", "headphones", "ai", "topper", "curtains", "fridge", "meal_plan",
            "indego"} <= set(items)
    for item in items.values():
        assert item.category in shop.catalog().categories
        assert item.weekly or item.swag < item.price


def test_every_item_has_a_sprite():
    sprites = json.loads((Path(shop.__file__).parent / "static/sprites/items.json").read_text())
    assert set(shop.catalog().items) <= set(sprites["names"])


def test_every_wearable_is_drawn_on_the_pet():
    """tools/draw_sprites.py draws each into wear.png; one it doesn't know would be bought and never seen."""
    meta = json.loads((Path(shop.__file__).parent / "static/sprites/wear.json").read_text())
    assert {i.id for i in shop.catalog().items.values() if i.wear} == set(meta["names"])


def test_a_pet_wears_its_best_item_in_each_slot():
    assert shop.wearing([]) == []
    assert shop.wearing(["thinkpad", "vest", "hoodie"]) == ["hoodie", "vest"]
    # The parka beats the vest for the outer layer, and slots come in drawing order whatever the order owned.
    assert shop.wearing(["sunglasses", "vest", "parka", "cap", "headphones"]) == ["parka", "cap", "headphones", "sunglasses"]


@pytest.mark.parametrize(("body", "error"), [
    ('category = "gear"\nprice = 10\neffects = { work_energy = 0.1 }', "can't have"),
    ('category = "gear"\nprice = 10\nswag = 10\neffects = {}', "swag must be less"),
    ('category = "gear"\nprice = 10\nreplaces = "nope"\neffects = {}', "replaces unknown"),
    ('category = "nope"\nprice = 10\neffects = {}', "unknown category"),
    ('category = "gear"\nprice = 10\nswipes = 3\neffects = {}', "only subscriptions"),
    ('category = "gear"\nprice = 10\neffects = { training_xp = -1 }', "non-negative"),
])
def test_bad_items_are_rejected(tmp_path, body, error):
    file = tmp_path / "items.toml"
    file.write_text(f'[categories]\ngear = "Gear"\n\n[[item]]\nid = "x"\nname = "X"\nblurb = ""\n{body}\n')
    with pytest.raises(ShopError, match=error):
        load_catalog(file)


# --- Buying ---


def test_buying_an_item_costs_its_price_and_adds_swag():
    pet = actions.buy(hatch(money=100.0), MON_9AM, "thinkpad")
    assert pet.items == ["thinkpad"]
    assert pet.money == 100 - 60
    assert shop.swag(pet.items) == 20
    with pytest.raises(ActionError, match="already has"):
        actions.buy(rich(items=["thinkpad"]), MON_9AM, "thinkpad")
    with pytest.raises(ActionError, match="money"):
        actions.buy(hatch(money=10.0), MON_9AM, "thinkpad")
    with pytest.raises(ActionError, match="unknown item"):
        actions.buy(hatch(), MON_9AM, "yacht")


def test_the_macbook_trades_in_the_thinkpad():
    with pytest.raises(ActionError, match="needs a Used ThinkPad"):
        actions.buy(rich(), MON_9AM, "macbook")
    pet = actions.buy(actions.buy(rich(), MON_9AM, "thinkpad"), MON_9AM, "macbook")
    assert pet.items == ["macbook"]
    assert pet.money == 1000 - 60 - 180
    with pytest.raises(ActionError, match="already has a MacBook Pro"):
        actions.buy(pet, MON_9AM, "thinkpad")


def test_owned_items_keep_their_value_but_subscriptions_dont():
    pet = actions.buy(actions.buy(rich(), MON_9AM, "thinkpad"), MON_9AM, "macbook")
    pet = actions.buy(actions.buy(pet, MON_9AM, "fridge"), MON_9AM, "meal_plan")
    # The MacBook is worth all that went into it, ThinkPad included; the meal plan is spent.
    assert shop.value(pet.items) == 60 + 180 + shop.catalog().items["fridge"].price
    assert pet.money + shop.value(pet.items) == 1000 - shop.catalog().items["meal_plan"].price


def test_items_work_for_a_pet_without_a_career():
    pet = dataclasses.replace(new_pet("larva", MON_9AM), items=["thinkpad", "indego"])
    perks = perks_of(pet)
    assert (perks.training_xp, perks.travel_speed) == (0.10, 0.5)


# --- Effects ---


@pytest.mark.parametrize(("items", "subs", "boost"), [
    ([], {}, 1.0),
    (["thinkpad"], {}, 1.10),
    (["macbook"], {}, 1.20),
    (["macbook"], {"ai": {"until": MON_9AM + WEEK, "renew": True}}, 1.35),
])
def test_laptops_and_ai_speed_up_training(items, subs, boost):
    pet = actions.train(rich(items=items, subscriptions=subs), MON_9AM, "towne", "grind")
    pet = advance(pet, MON_9AM + 2 * HOUR)[0]
    assert grind_xp(pet) == pytest.approx(2 * C.xp_per_hour * boost)


def test_headphones_raise_task_pay_and_save_energy():
    pet = actions.recall(actions.task(hatch(items=["headphones"]), MON_9AM, "towne"), MON_9AM + 2 * HOUR)
    assert pet.money == pytest.approx(C.starting_money + 2 * C.task_pay_per_hour * 1.1)
    assert pet.energy == pytest.approx(100 - 2 * C.work_energy_drain_per_hour * 0.9)


def test_the_topper_speeds_up_sleep():
    pet = actions.recall(actions.task(hatch(items=["topper"]), MON_9AM, "towne"), MON_9AM + 4 * HOUR)
    pet = actions.sleep(pet, pet.updated_at)
    pet = advance(pet, pet.updated_at + 2 * HOUR)[0]
    assert pet.energy == pytest.approx(50 + 2 * C.sleep_energy_regen_per_hour * 1.2)


@pytest.mark.parametrize(("items", "rest"), [([], 0.9), (["curtains"], 0.94)])
def test_curtains_make_a_short_night_count_for_more(items, rest):
    """Up past midnight at a task, the pet only sleeps 8 hours."""
    pet = actions.task(hatch(items=items), et(2026, 10, 5, 22), "towne")
    pet = actions.recall(pet, et(2026, 10, 6, 1))
    pet = advance(pet, et(2026, 10, 6, 9))[0]
    assert pet.rest_multiplier == pytest.approx(rest)


def test_the_fridge_feeds_a_hungry_pet_at_home_but_not_at_work():
    pet = advance(hatch(fullness=1.0, items=["fridge"]), MON_9AM + HOUR)[0]
    assert pet.fullness > 90
    assert pet.money == C.starting_money - C.meal_price

    without = advance(hatch(fullness=1.0), MON_9AM + HOUR)[0]
    assert (without.doordashes, without.money) == (1, C.starting_money - C.doordash_price)

    working = actions.task(hatch(fullness=1.0, items=["fridge"]), MON_9AM, "towne")
    assert advance(working, MON_9AM + HOUR)[0].doordashes == 1


@pytest.mark.travel
def test_indego_walks_faster():
    pet = actions.task(hatch(items=["indego"]), MON_9AM, "towne")
    walk = math.dist(spot(None), spot("towne")) / C.travel_px_per_second
    assert pet.trip["arrive"] - MON_9AM == pytest.approx(walk / 1.5)


# --- Subscriptions ---


def test_a_meal_plan_gives_swipes_that_feed_for_free():
    pet = actions.buy(rich(), MON_9AM, "meal_plan")
    assert (pet.swipes, pet.money) == (14, 1000 - 55)
    assert pet.subscriptions["meal_plan"] == {"until": MON_9AM + WEEK, "renew": True}

    pet = actions.feed(dataclasses.replace(pet, fullness=50.0), MON_NOON)
    assert (pet.swipes, pet.money, pet.fullness) == (13, 1000 - 55, 100)


def test_a_starving_pet_swipes_in_wherever_it_is():
    pet = actions.buy(rich(fullness=1.0), MON_9AM, "meal_plan")
    pet = actions.task(pet, MON_9AM, "towne")
    pet, events = advance(pet, MON_9AM + HOUR)
    assert pet.swipes == 13
    assert pet.fullness > 90
    assert "meal_swipe" in [e.kind for e in events]


def test_subscriptions_renew_weekly_and_reset_swipes():
    pet = actions.buy(hatch(money=200.0), MON_9AM, "meal_plan")
    pet = actions.feed(dataclasses.replace(pet, fullness=50.0), MON_NOON)
    pet, events = advance(pet, MON_9AM + WEEK)
    assert pet.money == pytest.approx(200 - 2 * 55)
    assert pet.swipes == 14
    assert pet.subscriptions["meal_plan"]["until"] == MON_9AM + 2 * WEEK
    assert "subscription_renewed" in [e.kind for e in events]


def test_a_subscription_lapses_when_the_pet_cant_pay():
    pet = actions.buy(hatch(), MON_9AM, "ai")
    assert perks_of(pet).training_xp == 0.15
    pet, events = advance(dataclasses.replace(pet, money=5.0), MON_9AM + WEEK + HOUR)
    assert pet.subscriptions == {}
    assert perks_of(pet).training_xp == 0
    assert pet.money + pet.doordashes * C.doordash_price == 5
    assert ("subscription_ended", {"item": "ai", "cancelled": False}) in [(e.kind, e.data) for e in events]


def test_a_lapse_mid_shift_stops_the_boost_from_then_on():
    pet = dataclasses.replace(rich(), money=0.0, subscriptions={"ai": {"until": MON_9AM + HOUR, "renew": True}})
    pet = actions.train(pet, MON_9AM, "towne", "grind")
    pet = advance(pet, MON_9AM + 2 * HOUR)[0]
    assert grind_xp(pet) == pytest.approx(C.xp_per_hour * 1.15 + C.xp_per_hour)


def test_cancelling_keeps_the_subscription_until_its_paid_time_runs_out():
    pet = actions.cancel(actions.buy(rich(), MON_9AM, "meal_plan"), MON_NOON, "meal_plan")
    assert pet.subscriptions["meal_plan"]["renew"] is False
    with pytest.raises(ActionError, match="not subscribed"):
        actions.cancel(pet, MON_NOON, "meal_plan")

    ended = advance(pet, MON_9AM + WEEK)[0]
    assert (ended.subscriptions, ended.swipes, ended.money) == ({}, 0, 1000 - 55)

    resumed = actions.buy(pet, MON_NOON, "meal_plan")
    assert resumed.subscriptions["meal_plan"]["renew"] is True
    assert resumed.money == 1000 - 55
    with pytest.raises(ActionError, match="already subscribed"):
        actions.buy(resumed, MON_NOON, "meal_plan")


# --- Pottruck ---

LEVEL_HOURS = C.fitness_hours_per_level
LEVEL_ENERGY = C.fitness_energy_per_level
FIT_HOURS = C.fitness_max_levels * LEVEL_HOURS
FIT_ENERGY = 100 + C.fitness_max_levels * LEVEL_ENERGY


def test_working_out_raises_max_energy_instead_of_xp():
    pet = actions.work_out(hatch(), MON_9AM)
    assert (pet.activity, pet.building, pet.path) == (Activity.TRAINING, "pottruck", "fitness")
    pet = advance(pet, MON_9AM + LEVEL_HOURS * HOUR)[0]
    assert pet.fitness == pytest.approx(LEVEL_HOURS)
    assert max_energy(pet) == 100 + LEVEL_ENERGY
    assert pet.energy == pytest.approx(100 - LEVEL_HOURS * C.work_energy_drain_per_hour)
    assert pet.xp == {}
    assert pet.train_streak == 0


def test_sleep_refills_a_fit_pet_in_the_same_time():
    pet = actions.sleep(hatch(fitness=FIT_HOURS, energy=0.0), MON_NOON)
    assert max_energy(pet) == FIT_ENERGY
    pet = advance(pet, MON_NOON + 10 * HOUR)[0]
    assert pet.energy == pytest.approx(FIT_ENERGY)
    assert pet.activity is Activity.HOME


def test_fitness_caps_out():
    pet = actions.work_out(hatch(fitness=FIT_HOURS - 1), MON_9AM)
    pet = advance(pet, MON_9AM + 3 * HOUR)[0]
    assert pet.fitness == FIT_HOURS
    with pytest.raises(ActionError, match="as fit as it gets"):
        actions.work_out(actions.recall(pet, pet.updated_at), pet.updated_at)


def test_a_full_fit_pet_isnt_tired():
    level = hatch(fitness=LEVEL_HOURS, energy=100 + LEVEL_ENERGY)
    with pytest.raises(ActionError, match="not tired"):
        actions.sleep(level, MON_NOON)
    pet = actions.sleep(dataclasses.replace(level, energy=100.0), MON_NOON)
    assert pet.activity is Activity.SLEEPING


def test_buying_a_tier_mid_workout_keeps_working_out():
    pet = actions.work_out(hatch(xp={"tech": {"grind": 1000.0}}), MON_9AM)
    pet = actions.buy_tier(pet, MON_9AM + HOUR, "grind")
    assert (pet.activity, pet.path) == (Activity.TRAINING, "fitness")
