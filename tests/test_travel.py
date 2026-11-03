"""Pets walk between buildings at a fixed speed and only start working or sleeping when they get there."""

import dataclasses
import math

import pytest
from helpers import HOUR, et
from test_pet import _assert_close

from locusts import actions
from locusts.actions import ActionError
from locusts.config import DEFAULT_CONFIG as C
from locusts.pet import Activity, Pet, advance, new_pet, position, spot

pytestmark = pytest.mark.travel

MON_9AM = et(2026, 10, 5, 9)


def walk(a: str | None, b: str | None) -> float:
    return math.dist(spot(a), spot(b)) / C.travel_px_per_second


def hatch(career: str = "tech", **fields) -> Pet:
    return dataclasses.replace(new_pet("larva", MON_9AM, career=career), **fields)


def along(a: str | None, b: str | None, s: float) -> list[float]:
    (x0, y0), (x1, y1) = spot(a), spot(b)
    return [x0 + (x1 - x0) * s, y0 + (y1 - y0) * s]


def test_farther_buildings_take_longer_to_reach():
    near, far = walk(None, "huntsman"), walk(None, "skirkanich")
    assert 1 < near < far
    pet = actions.task(hatch(), MON_9AM, "skirkanich")
    assert pet.activity is Activity.TASK and pet.building == "skirkanich"
    assert pet.trip == {"from": list(spot(None)), "to": list(spot("skirkanich")), "start": MON_9AM,
                        "arrive": pytest.approx(MON_9AM + far)}


def test_training_counts_from_arrival():
    pet = actions.train(hatch(), MON_9AM, "towne", "grind")
    arrive = MON_9AM + walk(None, "towne")
    assert pet.work_started_at is None

    halfway = advance(pet, (MON_9AM + arrive) / 2)[0]
    assert halfway.xp == {} and halfway.energy == 100
    assert position(halfway, halfway.updated_at) == pytest.approx(along(None, "towne", 0.5))

    pet = advance(pet, arrive + HOUR)[0]
    assert pet.trip is None
    assert pet.work_started_at == pytest.approx(arrive)
    assert pet.xp["tech"]["grind"] == pytest.approx(C.xp_per_hour)
    assert pet.energy == pytest.approx(100 - C.work_energy_drain_per_hour)


def test_task_pay_counts_from_arrival():
    pet = actions.task(hatch(), MON_9AM, "huntsman")
    pet = actions.recall(pet, MON_9AM + walk(None, "huntsman") + 2 * HOUR)
    assert pet.money == pytest.approx(C.starting_money + 2 * C.task_pay_per_hour)


def test_pulling_out_mid_walk_turns_the_pet_around_where_it_is():
    far = walk(None, "skirkanich")
    t = MON_9AM + far / 4
    pet = actions.recall(actions.task(hatch(), MON_9AM, "skirkanich"), t)
    assert pet.activity is Activity.HOME and pet.building is None
    assert pet.trip["from"] == pytest.approx(along(None, "skirkanich", 0.25))
    assert pet.trip["arrive"] == pytest.approx(t + far / 4)
    assert pet.money == C.starting_money


def test_a_pet_walking_home_can_be_sent_somewhere_new_from_where_it_is():
    left = MON_9AM + walk(None, "huntsman") + HOUR
    pet = actions.recall(actions.task(hatch(), MON_9AM, "huntsman"), left)
    t = left + walk(None, "huntsman") / 2
    mid = position(pet, t)
    pet = actions.task(pet, t, "towne")
    assert pet.trip["from"] == pytest.approx(list(mid))
    assert pet.trip["arrive"] == pytest.approx(t + math.dist(mid, spot("towne")) / C.travel_px_per_second)


def test_a_pet_walking_home_at_night_falls_asleep_when_it_gets_there():
    late = et(2026, 10, 5, 23, 30)
    pet = actions.recall(actions.task(hatch(), et(2026, 10, 5, 20), "skirkanich"), late)
    assert pet.activity is Activity.HOME
    with pytest.raises(ActionError, match="still walking home"):
        actions.sleep(pet, late)
    assert advance(pet, late + walk(None, "skirkanich"))[0].activity is Activity.SLEEPING


def test_an_exhausted_pet_walks_home():
    pet = actions.train(hatch(), MON_9AM, "towne", "grind")
    out = MON_9AM + walk(None, "towne") + 8 * HOUR
    pet, events = advance(pet, out + 1)
    assert "exhausted" in [e.kind for e in events]
    assert pet.activity is Activity.HOME
    assert pet.trip["from"] == list(spot("towne"))
    assert pet.trip["start"] == pytest.approx(out, abs=1e-3)


def test_wet_lab_experiments_start_when_the_pet_gets_to_the_lab():
    wetlab = hatch("premed", upgrades={"premed": {"wetlab": 1}})
    pet = actions.train(wetlab, MON_9AM, "chem", "wetlab")
    assert pet.experiments == []
    arrive = MON_9AM + walk(None, "chem")
    assert advance(pet, arrive)[0].experiments == [pytest.approx(arrive + C.experiment_hours * HOUR)]

    turned_back = actions.recall(pet, MON_9AM + 1)
    assert advance(turned_back, MON_9AM + HOUR)[0].experiments == []


def test_peeking_mid_walk_changes_nothing():
    pet = actions.task(hatch(), MON_9AM, "skirkanich")
    end = MON_9AM + 3 * HOUR
    once = advance(pet, end)[0]
    often = pet
    for k in range(1, 40):
        often = advance(often, MON_9AM + k * 0.37)[0]
    often = advance(often, end)[0]
    for f in dataclasses.fields(Pet):
        _assert_close(getattr(once, f.name), getattr(often, f.name), f.name)
