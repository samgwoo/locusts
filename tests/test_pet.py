"""Simulation tests. Unless noted, pets hatch Monday Oct 5 2026 at 9 AM, the start of a campus day."""

import copy
import dataclasses
import random

import pytest
from helpers import HOUR, et

from locusts import actions
from locusts.config import DEFAULT_CONFIG as C
from locusts.pet import Activity, Pet, advance, new_pet, penalty_strength, rest_multiplier_for, work_speed

RICH = dataclasses.replace(C, starting_money=10_000)
MON_9AM = et(2026, 10, 5, 9)


def at(pet: Pet, ts: float, config=C) -> Pet:
    return advance(pet, ts, config)[0]


def hatch(ts: float = MON_9AM, config=C) -> Pet:
    return new_pet("larva", ts, config, career="tech")


def fed_at(pet: Pet, ts: float) -> Pet:
    """Advance to `ts` and feed, so starvation doesn't muddy work-speed assertions."""
    return actions.feed(pet, ts)


# Fullness


def test_awake_pet_loses_a_sixteenth_of_fullness_per_hour():
    assert at(hatch(), et(2026, 10, 5, 21)).fullness == pytest.approx(100 - 12 * 100 / 16)


def test_sleeping_halves_fullness_drain():
    pet = at(hatch(), et(2026, 10, 6, 1))
    # 14 awake hours, then 2 asleep at half rate.
    assert pet.fullness == pytest.approx(100 - 14 * 6.25 - 2 * 3.125)


def test_one_meal_a_day_starves_the_pet_overnight():
    pet, events = advance(hatch(), et(2026, 10, 6, 9), C)
    # 87.5 burned by 11 PM, the last 12.5 at 3.125/hr runs out at 3 AM, and it orders DoorDash right away.
    assert [e.at for e in events if e.kind == "starving"] == [et(2026, 10, 6, 3)]
    assert [e.at for e in events if e.kind == "doordash"] == [et(2026, 10, 6, 3)]
    assert pet.fullness == pytest.approx(C.doordash_fullness - 6 * 3.125)
    assert (pet.money, pet.doordashes) == (C.starting_money - C.doordash_price, 1)


def test_doordash_keeps_coming_while_nobody_feeds_the_pet():
    pet = advance(dataclasses.replace(hatch(), fullness=1.0), MON_9AM + 24 * HOUR, C)[0]
    # 30 fullness lasts 4.8 awake hours: orders at 9:10 AM, 1:58 PM, 6:46 PM, and just after midnight (it
    # slows down at 11 PM), and that last 30 at the sleeping rate lasts past 9 AM.
    assert pet.doordashes == 4
    assert pet.money == C.starting_money - 4 * C.doordash_price


def test_a_broke_pet_orders_doordash_into_debt():
    pet, events = advance(dataclasses.replace(hatch(), fullness=1.0, money=4.0), MON_9AM + HOUR, C)
    assert (pet.money, pet.doordashes) == (4.0 - C.doordash_price, 1)
    assert pet.fullness > 0
    assert "doordash" in [e.kind for e in events]


def test_two_meals_a_day_keeps_the_pet_fed_all_week():
    pet = hatch(config=RICH)
    for day in range(5, 12):
        for hour in (9, 21):
            pet, events = advance(pet, et(2026, 10, day, hour), RICH)
            assert not [e for e in events if e.kind == "starving"]
            assert pet.fullness > 0
            if pet.fullness < 100:
                pet = actions.feed(pet, et(2026, 10, day, hour), RICH)


# Sleep and rest


def test_home_pet_sleeps_through_the_night_window_and_wakes_rested():
    pet = hatch()
    assert at(pet, et(2026, 10, 5, 22, 59)).activity is Activity.HOME
    assert at(pet, et(2026, 10, 5, 23)).activity is Activity.SLEEPING

    pet, events = advance(pet, et(2026, 10, 6, 9), C)
    assert pet.activity is Activity.HOME
    assert pet.rest_multiplier == 1.0
    assert pet.sleep_hours == 0
    assert [e.data["sleep_hours"] for e in events if e.kind == "new_day"] == [pytest.approx(10)]


@pytest.mark.parametrize(
    ("hours", "multiplier"),
    [(0, 0.5), (4, 0.7), (8, 0.9), (9, 0.95), (10, 1.0), (14, 1.0)],
)
def test_rest_curve(hours, multiplier):
    assert rest_multiplier_for(hours) == pytest.approx(multiplier)


def test_eight_hours_of_sleep_means_ninety_percent_effectiveness_the_next_day():
    pet = actions.train(hatch(), et(2026, 10, 5, 19), "towne", "grind")
    pet = actions.recall(pet, et(2026, 10, 6, 1))
    assert pet.activity is Activity.SLEEPING
    assert pet.xp["tech"]["grind"] == pytest.approx(60)

    pet = fed_at(pet, et(2026, 10, 6, 9))
    assert pet.rest_multiplier == pytest.approx(0.9)
    assert pet.energy == 100

    pet = actions.train(pet, et(2026, 10, 6, 9), "towne", "grind")
    assert at(pet, et(2026, 10, 6, 13)).xp["tech"]["grind"] == pytest.approx(60 + 4 * 10 * 0.9)


def test_daytime_nap_wakes_when_energy_is_full_and_counts_toward_tomorrow():
    pet = actions.train(hatch(), MON_9AM, "towne", "grind")
    pet = actions.recall(pet, et(2026, 10, 5, 13))
    pet = actions.sleep(pet, et(2026, 10, 5, 13))

    assert at(pet, et(2026, 10, 5, 17, 59)).activity is Activity.SLEEPING
    pet = at(pet, et(2026, 10, 5, 18))
    assert pet.activity is Activity.HOME
    assert pet.energy == 100
    assert pet.sleep_hours == pytest.approx(5)


def test_sleeping_pet_keeps_banking_sleep_hours_with_full_energy():
    pet = at(hatch(), et(2026, 10, 6, 8, 59))
    assert pet.energy == 100
    assert pet.sleep_hours == pytest.approx(9 + 59 / 60)


def test_pet_hatched_mid_night_is_credited_for_the_night_so_far():
    pet = hatch(et(2026, 10, 6, 2))
    assert pet.activity is Activity.SLEEPING
    assert pet.sleep_hours == pytest.approx(3)
    assert at(pet, et(2026, 10, 6, 9)).rest_multiplier == 1.0


def test_daylight_saving_nights():
    fall_back = at(hatch(et(2026, 10, 31, 12)), et(2026, 11, 1, 8, 59))
    assert fall_back.sleep_hours == pytest.approx(10 + 59 / 60)

    spring_forward = at(hatch(et(2026, 3, 7, 12)), et(2026, 3, 8, 9))
    assert spring_forward.rest_multiplier == pytest.approx(0.95)


# Energy and exhaustion


def test_full_pet_is_exhausted_after_eight_hours_of_work_and_sent_home():
    pet = actions.train(hatch(), MON_9AM, "towne", "grind")
    pet, events = advance(pet, et(2026, 10, 5, 18), C)

    assert [e.at for e in events if e.kind == "exhausted"] == [et(2026, 10, 5, 17)]
    assert pet.activity is Activity.HOME
    assert pet.building is None
    assert pet.energy == 0
    assert pet.xp["tech"]["grind"] == pytest.approx(80)


def test_exhaustion_penalizes_exactly_the_next_campus_day():
    pet = at(actions.train(hatch(), MON_9AM, "towne", "grind"), et(2026, 10, 5, 18))
    assert pet.penalty_start == et(2026, 10, 6, 9)
    assert pet.penalty_end == et(2026, 10, 7, 9)

    assert penalty_strength(pet, et(2026, 10, 6, 8, 59)) == 0
    assert penalty_strength(pet, et(2026, 10, 6, 9)) == 1
    assert penalty_strength(pet, et(2026, 10, 7, 8, 59)) == 1
    assert penalty_strength(pet, et(2026, 10, 7, 9)) == 0


def test_penalty_day_doubles_energy_cost_and_halves_work_speed():
    pet = fed_at(actions.train(hatch(), MON_9AM, "towne", "grind"), et(2026, 10, 6, 9))
    assert pet.energy == 100
    assert work_speed(pet, et(2026, 10, 6, 9)) == pytest.approx(0.5)

    pet = actions.train(pet, et(2026, 10, 6, 9), "towne", "grind")
    pet, events = advance(pet, et(2026, 10, 6, 15), C)
    assert [e.at for e in events if e.kind == "exhausted"] == [et(2026, 10, 6, 13)]
    assert pet.xp["tech"]["grind"] == pytest.approx(80 + 4 * 10 * 0.5)


def test_exhaustion_during_a_penalty_extends_it_by_another_day():
    pet = at(actions.train(hatch(), MON_9AM, "towne", "grind"), et(2026, 10, 6, 9))
    pet = at(actions.train(pet, et(2026, 10, 6, 9), "towne", "grind"), et(2026, 10, 6, 15))
    assert pet.penalty_start == et(2026, 10, 6, 9)
    assert pet.penalty_end == et(2026, 10, 8, 9)
    assert penalty_strength(pet, et(2026, 10, 6, 20)) == 1


def test_coffee_halves_the_penalty_while_it_lasts():
    boost = 1 + C.coffee_work_boost
    pet = fed_at(actions.train(hatch(), MON_9AM, "towne", "grind"), et(2026, 10, 6, 9))
    pet = actions.drink_coffee(pet, et(2026, 10, 6, 9))
    assert work_speed(pet, et(2026, 10, 6, 9)) == pytest.approx(0.75 * boost)

    pet = actions.train(pet, et(2026, 10, 6, 9), "towne", "grind")
    pet, events = advance(pet, et(2026, 10, 6, 15), C)
    # 4 caffeinated hours at 1.5x drain use 75 energy, the last 25 go in 1 hour at 2x.
    assert [e.at for e in events if e.kind == "exhausted"] == [et(2026, 10, 6, 14)]
    assert pet.xp["tech"]["grind"] == pytest.approx(80 + 4 * 10 * 0.75 * boost + 1 * 10 * 0.5)


def test_coffee_speeds_up_work_on_any_day():
    pet = actions.drink_coffee(hatch(), MON_9AM)
    assert work_speed(pet, MON_9AM) == pytest.approx(1 + C.coffee_work_boost)
    assert work_speed(pet, MON_9AM + C.coffee_hours * HOUR) == 1.0


def test_exhaustion_after_midnight_penalizes_the_day_starting_that_morning():
    pet = actions.train(hatch(), et(2026, 10, 5, 19), "towne", "grind")
    pet, events = advance(pet, et(2026, 10, 6, 9), C)
    pet = fed_at(pet, et(2026, 10, 6, 9))

    assert [e.at for e in events if e.kind == "exhausted"] == [et(2026, 10, 6, 3)]
    assert pet.penalty_start == et(2026, 10, 6, 9)
    assert pet.rest_multiplier == pytest.approx(0.8)
    assert pet.energy == pytest.approx(60)
    assert work_speed(pet, et(2026, 10, 6, 9)) == pytest.approx(0.8 * 0.5)


def test_doordash_at_work_keeps_the_pet_working_at_full_speed():
    pet = dataclasses.replace(hatch(), fullness=25.0)
    pet = at(actions.train(pet, MON_9AM, "towne", "grind"), et(2026, 10, 5, 13))
    assert pet.xp["tech"]["grind"] == pytest.approx(4 * 10)
    assert pet.doordashes == 1


# Simulation invariants


def test_cannot_advance_backwards():
    with pytest.raises(ValueError):
        advance(hatch(), MON_9AM - 1, C)


def test_month_offline_converges():
    pet = at(actions.train(hatch(), MON_9AM, "towne", "grind"), et(2026, 11, 5, 9))
    assert pet.fullness > 0
    assert pet.money == C.starting_money - pet.doordashes * C.doordash_price < 0
    assert pet.activity is Activity.HOME


ACTIONS = [
    lambda p, t: actions.feed(p, t, RICH),
    lambda p, t: actions.sleep(p, t, RICH),
    lambda p, t: actions.wake(p, t, RICH),
    lambda p, t: actions.train(p, t, "towne", "grind", RICH),
    lambda p, t: actions.train(p, t, "skirkanich", "research", RICH),
    lambda p, t: actions.buy_tier(p, t, "grind", RICH),
    lambda p, t: actions.buy_tier(p, t, "research", RICH),
    lambda p, t: actions.task(p, t, "huntsman", RICH),
    lambda p, t: actions.recall(p, t, RICH),
    lambda p, t: actions.drink_coffee(p, t, RICH),
]


AT_THE_CROSSPATH = {
    "upgrades": {"tech": {"grind": 2, "research": 2}},
    "xp": {"tech": {"grind": 150, "research": 160}},
}


@pytest.mark.parametrize("start_state", [{}, AT_THE_CROSSPATH], ids=["fresh", "crosspath"])
@pytest.mark.parametrize("seed", range(25))
def test_result_does_not_depend_on_how_often_the_pet_is_looked_at(seed, start_state):
    rng = random.Random(seed)
    start = MON_9AM + rng.uniform(0, 24 * HOUR)
    schedule = sorted(start + rng.uniform(0, 6 * 24 * HOUR) for _ in range(30))
    end = schedule[-1] + 24 * HOUR
    choices = [rng.choice(ACTIONS) for _ in schedule]

    def run(peek_every: float | None) -> Pet:
        pet = dataclasses.replace(
            new_pet("larva", start, RICH, career="tech"), **copy.deepcopy(start_state))
        for ts, action in zip(schedule, choices):
            if peek_every:
                while pet.updated_at + peek_every < ts:
                    pet = at(pet, pet.updated_at + peek_every, RICH)
            try:
                pet = action(pet, ts)
            except actions.ActionError:
                pet = at(pet, ts, RICH)
        return at(pet, end, RICH)

    once = run(None)
    for peek in (7 * 60, 37 * 60 + 13):
        often = run(peek)
        for f in dataclasses.fields(Pet):
            _assert_close(getattr(once, f.name), getattr(often, f.name), f.name)


def _assert_close(a, b, where: str) -> None:
    if isinstance(a, dict):
        assert a.keys() == b.keys(), where
        for k in a:
            _assert_close(a[k], b[k], f"{where}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), where
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_close(x, y, f"{where}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, abs=1e-6), where
    else:
        assert a == b, where


def test_office_hours_regular_recovers_gpa_on_the_fourth_van_pelt_day_of_a_week():
    pet = dataclasses.replace(new_pet("larva", MON_9AM, career="premed"), upgrades={"premed": {"gunner": 2}},
                              gpa=3.5, swipes=99)
    for day in range(4):
        start = MON_9AM + day * 24 * HOUR
        pet = actions.train(advance(pet, start, C)[0], start, "van_pelt", "gunner")
        pet = actions.recall(advance(pet, start + 2 * HOUR, C)[0], start + 2 * HOUR)
        assert pet.gpa == pytest.approx(3.6 if day == 3 else 3.5)
    # A fifth day the same week adds nothing; the next week starts the count over.
    friday = MON_9AM + 4 * 24 * HOUR
    pet = actions.train(advance(pet, friday, C)[0], friday, "van_pelt", "gunner")
    assert advance(pet, friday + HOUR, C)[0].gpa == pytest.approx(3.6)
    assert advance(pet, friday + HOUR, C)[0].study_days == 5
