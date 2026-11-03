import dataclasses

import pytest
from helpers import HOUR, et

from locusts import actions
from locusts.actions import ActionError
from locusts.config import DEFAULT_CONFIG as C
from locusts.pet import Activity, new_pet

MON_9AM = et(2026, 10, 5, 9)
MON_NOON = et(2026, 10, 5, 12)
MON_MIDNIGHT = et(2026, 10, 6, 0)


def hatch():
    return new_pet("larva", MON_9AM, career="tech")


def test_feed_costs_a_meal_and_caps_at_full():
    pet = actions.feed(hatch(), MON_NOON)
    assert pet.fullness == 100
    assert pet.money == C.starting_money - C.meal_price


def test_feed_rejects_a_full_pet_and_a_broke_one():
    with pytest.raises(ActionError, match="full"):
        actions.feed(hatch(), MON_9AM)

    broke = dataclasses.replace(hatch(), money=1.0)
    with pytest.raises(ActionError, match="money"):
        actions.feed(broke, MON_NOON)


def test_train_validates_building_path_and_energy():
    with pytest.raises(ActionError, match="unknown building"):
        actions.task(hatch(), MON_9AM, "the_quad")
    with pytest.raises(ActionError, match="Tech has no path networking"):
        actions.train(hatch(), MON_9AM, "towne", "networking")
    with pytest.raises(ActionError, match="The Grind doesn't train at huntsman"):
        actions.train(hatch(), MON_9AM, "huntsman", "grind")

    tired = actions.train(hatch(), MON_9AM, "towne", "grind")
    tired = actions.recall(tired, MON_9AM + 7.5 * HOUR)
    with pytest.raises(ActionError, match="too tired"):
        actions.task(tired, MON_9AM + 7.5 * HOUR, "towne")


def test_cannot_double_book_a_pet():
    pet = actions.task(hatch(), MON_9AM, "towne")
    with pytest.raises(ActionError, match="already at a building"):
        actions.train(pet, MON_NOON, "towne", "grind")
    with pytest.raises(ActionError, match="pull the pet out"):
        actions.sleep(pet, MON_NOON)


def test_task_pays_by_the_hour():
    pet = actions.recall(actions.task(hatch(), MON_9AM, "huntsman"), MON_9AM + 2 * HOUR)
    assert pet.money == pytest.approx(C.starting_money + 2 * C.task_pay_per_hour)
    assert pet.activity is Activity.HOME


def test_recall_at_night_puts_the_pet_to_bed():
    pet = actions.recall(actions.task(hatch(), et(2026, 10, 5, 20), "towne"), MON_MIDNIGHT)
    assert pet.activity is Activity.SLEEPING


def test_sleep_and_wake():
    with pytest.raises(ActionError, match="not tired"):
        actions.sleep(hatch(), MON_9AM)

    pet = actions.recall(actions.task(hatch(), MON_9AM, "towne"), MON_NOON)
    pet = actions.sleep(pet, MON_NOON)
    assert pet.activity is Activity.SLEEPING
    pet = actions.wake(pet, MON_NOON + HOUR)
    assert pet.activity is Activity.HOME


def test_cannot_wake_a_pet_at_night():
    pet = new_pet("larva", MON_MIDNIGHT)
    with pytest.raises(ActionError, match="night"):
        actions.wake(pet, MON_MIDNIGHT)
    with pytest.raises(ActionError, match="already asleep"):
        actions.sleep(pet, MON_MIDNIGHT)


def test_coffee_stacks_duration():
    pet = actions.drink_coffee(hatch(), MON_9AM)
    pet = actions.drink_coffee(pet, MON_9AM + HOUR)
    assert pet.coffee_until == MON_9AM + 2 * C.coffee_hours * HOUR
    assert pet.money == C.starting_money - 2 * C.coffee_price
