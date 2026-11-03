import dataclasses

import pytest
from helpers import HOUR, et

from locusts import actions
from locusts.actions import ActionError
from locusts.pet import Activity, advance, new_pet
from locusts.upgrades import careers_for, deepest_builds

MON_9AM = et(2026, 10, 5, 9)
TECH = careers_for()["tech"]


def tech_pet():
    return new_pet("larva", MON_9AM, career="tech")


def pet_with(upgrades=None, xp=None):
    return dataclasses.replace(tech_pet(), upgrades={"tech": upgrades or {}}, xp={"tech": xp or {}})


def test_training_banks_xp_on_the_trained_path_only():
    pet = actions.train(tech_pet(), MON_9AM, "towne", "grind")
    pet = actions.recall(pet, MON_9AM + 4 * HOUR)
    assert pet.xp == {"tech": {"grind": pytest.approx(40)}}
    assert pet.upgrades == {}


def test_a_path_with_two_buildings_shares_one_xp_pool():
    pet = actions.train(tech_pet(), MON_9AM, "levine", "research")
    pet = actions.recall(pet, MON_9AM + HOUR)
    pet = actions.train(pet, MON_9AM + HOUR, "skirkanich", "research")
    pet = actions.recall(pet, MON_9AM + 3 * HOUR)
    assert pet.xp["tech"]["research"] == pytest.approx(30)


def test_buying_spends_xp_and_raises_the_tier():
    pet = actions.buy_tier(pet_with(xp={"grind": 50}), MON_9AM, "grind")
    assert pet.upgrades["tech"]["grind"] == 1
    assert pet.xp["tech"]["grind"] == pytest.approx(5)


def test_buying_needs_enough_xp():
    with pytest.raises(ActionError, match="CIS 1200: OCaml Trauma costs 45 XP; The Grind has 44"):
        actions.buy_tier(pet_with(xp={"grind": 44}), MON_9AM, "grind")


@pytest.mark.parametrize(
    ("upgrades", "path", "reason"),
    [
        ({"grind": 5, "industry": 2}, "grind", "The Grind is maxed out"),
        ({"grind": 5, "industry": 2}, "industry", "Big Tech is capped at tier 2 because The Grind is past it"),
        ({"grind": 5, "industry": 2}, "research", "only 2 Tech paths can be upgraded"),
        ({"grind": 3, "industry": 2}, "industry", "Big Tech is capped at tier 2"),
        ({"grind": 1, "industry": 1}, "research", "only 2 Tech paths can be upgraded"),
    ],
)
def test_crosspath_rule_blocks_purchases(upgrades, path, reason):
    pet = pet_with(upgrades, xp={p: 10_000 for p in TECH.paths})
    with pytest.raises(ActionError, match=reason):
        actions.buy_tier(pet, MON_9AM, path)


def test_two_paths_can_both_reach_tier_two_before_either_commits():
    pet = pet_with({"grind": 2, "industry": 2}, xp={p: 10_000 for p in TECH.paths})
    pet = actions.buy_tier(pet, MON_9AM, "industry")
    assert pet.upgrades["tech"] == {"grind": 2, "industry": 3}


def test_buying_a_full_5_2_0_costs_exactly_the_build_price():
    cost = deepest_builds(TECH)[("research", "grind")]
    pet = pet_with(xp={"research": 45 + 90 + 135 + 180 + 270, "grind": 45 + 90})
    for path, tiers in (("grind", 2), ("research", 5)):
        for _ in range(tiers):
            pet = actions.buy_tier(pet, MON_9AM, path)
    assert pet.upgrades["tech"] == {"grind": 2, "research": 5}
    assert sum(pet.xp["tech"].values()) == pytest.approx(0)
    assert cost == 855


def test_locked_and_maxed_paths_cannot_be_trained_but_still_host_tasks():
    pet = pet_with({"grind": 5, "industry": 2})
    with pytest.raises(ActionError, match="can't train The Lab: only 2 Tech paths"):
        actions.train(pet, MON_9AM, "skirkanich", "research")
    with pytest.raises(ActionError, match="can't train The Grind: The Grind is maxed out"):
        actions.train(pet, MON_9AM, "towne", "grind")
    assert actions.task(pet, MON_9AM, "skirkanich").activity is Activity.TASK


def test_purchase_that_locks_the_training_path_sends_the_pet_home():
    pet = pet_with({"grind": 2, "industry": 2}, xp={"industry": 140})
    pet = actions.train(pet, MON_9AM, "towne", "grind")
    pet = actions.buy_tier(pet, MON_9AM + HOUR, "industry")
    assert pet.activity is Activity.HOME
    assert pet.building is None
    assert pet.xp["tech"]["grind"] == pytest.approx(10)


def test_purchase_on_the_training_path_keeps_the_pet_training():
    pet = actions.train(pet_with(xp={"grind": 45}), MON_9AM, "towne", "grind")
    pet = actions.buy_tier(pet, MON_9AM, "grind")
    assert pet.activity is Activity.TRAINING
    assert pet.upgrades["tech"]["grind"] == 1


def test_a_pet_can_only_train_its_own_career():
    with pytest.raises(ActionError, match="Tech has no path banking"):
        actions.train(tech_pet(), MON_9AM, "huntsman", "banking")
    with pytest.raises(ActionError, match="Tech has no path banking"):
        actions.buy_tier(tech_pet(), MON_9AM, "banking")
    with pytest.raises(ActionError, match="The Grind doesn't train at huntsman"):
        actions.train(tech_pet(), MON_9AM, "huntsman", "grind")
    assert actions.task(tech_pet(), MON_9AM, "huntsman").activity is Activity.TASK


def test_a_pet_without_a_career_cannot_train():
    with pytest.raises(ActionError, match="larva needs to pick a career first"):
        actions.train(new_pet("larva", MON_9AM), MON_9AM, "towne", "grind")


def test_declaring_a_career_is_permanent():
    pet = actions.declare_career(new_pet("larva", MON_9AM), MON_9AM, "finance")
    assert pet.career == "finance"
    pet = actions.train(pet, MON_9AM, "huntsman", "banking")
    with pytest.raises(ActionError, match="larva is already on the Finance track"):
        actions.declare_career(pet, MON_9AM, "tech")
    with pytest.raises(ActionError, match="unknown career astronaut"):
        actions.declare_career(new_pet("larva", MON_9AM), MON_9AM, "astronaut")


def test_a_career_survives_exhaustion():
    pet = actions.train(tech_pet(), MON_9AM, "towne", "grind")
    pet = advance(pet, MON_9AM + 12 * HOUR)[0]
    assert pet.penalty_start is not None
    assert pet.activity is Activity.HOME
    assert pet.career == "tech"
