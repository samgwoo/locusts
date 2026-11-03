"""What owned upgrade tiers do to the simulation. Pets start Monday Oct 5 2026 at 9 AM."""

import copy
import dataclasses
import random

import pytest
from helpers import HOUR, et
from test_pet import _assert_close

from locusts import actions
from locusts.actions import ActionError
from locusts.config import DEFAULT_CONFIG as C
from locusts.perks import Market, market_move, perks_for
from locusts.pet import Activity, Pet, advance, new_pet, perks_of
from locusts.upgrades import careers_for

MON_9AM = et(2026, 10, 5, 9)
TUE_9AM = et(2026, 10, 6, 9)
CAREERS = careers_for()


def pet(career, upgrades=None, at=MON_9AM, **fields):
    hatched = new_pet("larva", at, career=career)
    return dataclasses.replace(hatched, upgrades={career: upgrades or {}}, **fields)


def at(p, ts):
    return advance(p, ts)[0]


def test_perks_combine_in_tier_order():
    perks = perks_for(CAREERS["finance"], {"banking": 5, "markets": 2})
    assert perks.delivered_meals and perks.burnout_immunity
    assert perks.tired_training == 0.25
    assert perks.task_pay == pytest.approx(0.333)
    assert perks.market == Market(max_share=0.75, drift=0.01, swing=0.05, loss_cap=0.02)

    markets = perks_for(CAREERS["finance"], {"markets": 4, "banking": 2})
    assert markets.market == Market(max_share=1.0, drift=0.025, swing=0.10, loss_cap=0.02)
    assert perks_for(CAREERS["finance"], {}) == perks_of(pet("finance"))


def test_a_pet_without_a_career_has_no_perks():
    assert perks_of(new_pet("larva", MON_9AM)).market is None


# --- Pay ---


def test_task_pay_bonus():
    p = actions.task(pet("tech", {"industry": 1}), MON_9AM, "towne")
    assert at(p, MON_9AM + 4 * HOUR).money == pytest.approx(50 + 4 * 3 * 1.1)


def test_task_sites_pay_more_or_bank_xp_on_their_path():
    p = actions.task(pet("tech", {"research": 1}), MON_9AM, "levine")
    p = at(p, MON_9AM + 2 * HOUR)
    assert p.money == pytest.approx(56)
    assert p.xp == {"tech": {"research": pytest.approx(2)}}
    assert at(actions.task(pet("tech", {"research": 1}), MON_9AM, "towne"), MON_9AM + 2 * HOUR).xp == {}

    p = actions.task(pet("finance", {"private_equity": 1}), MON_9AM, "lauder_fischer")
    assert at(p, MON_9AM + 2 * HOUR).money == pytest.approx(50 + 2 * 3 * 1.1)


def test_medical_scribe_earns_while_asleep():
    p = pet("premed", {"clinical": 2}, at=et(2026, 10, 5, 23))
    assert p.activity is Activity.SLEEPING
    assert at(p, TUE_9AM).money == pytest.approx(50 + 10 * 3 * 0.3)


def test_document_review_earns_while_idle_at_home():
    assert at(pet("prelaw", {"biglaw": 3}), MON_9AM + 4 * HOUR).money == pytest.approx(50 + 4 * 3 * 0.7)
    assert at(pet("prelaw", {"biglaw": 2}), MON_9AM + 4 * HOUR).money == 50


def test_night_shift_pays_more_and_burns_more_energy():
    p = at(actions.task(pet("premed", {"clinical": 3}), MON_9AM, "towne"), MON_9AM + 2 * HOUR)
    assert p.money == pytest.approx(50 + 2 * 3 * 1.4)
    assert p.energy == pytest.approx(100 - 2 * 12.5 * 1.25)


def test_summer_associate_halves_meal_prices():
    p = at(pet("prelaw", {"biglaw": 2}), MON_9AM + 8 * HOUR)
    assert actions.feed(p, MON_9AM + 8 * HOUR).money == pytest.approx(47.5)


def test_billed_hours_round_one_task_a_day_up_to_the_hour():
    p = pet("prelaw", {"biglaw": 1})
    p = actions.recall(actions.task(p, MON_9AM, "towne"), MON_9AM + HOUR / 3)
    assert p.money == pytest.approx(53)
    p = actions.recall(actions.task(p, MON_9AM + HOUR, "towne"), MON_9AM + HOUR * 4 / 3)
    assert p.money == pytest.approx(54)
    p = actions.feed(p, TUE_9AM)
    p = actions.recall(actions.task(p, TUE_9AM, "towne"), TUE_9AM + HOUR / 3)
    # It went hungry overnight and ordered DoorDash.
    assert p.money == pytest.approx(54 - C.doordash_price - 5 + 3)


def test_billed_hours_dont_apply_to_training_or_without_the_perk():
    p = actions.recall(actions.task(pet("prelaw"), MON_9AM, "towne"), MON_9AM + HOUR / 3)
    assert p.money == pytest.approx(51)
    p = actions.recall(actions.train(pet("prelaw", {"biglaw": 1}), MON_9AM, "silverman", "biglaw"),
                       MON_9AM + HOUR / 3)
    assert p.money == pytest.approx(50)


# --- Investment Banking ---


def test_delivered_meals_feed_a_working_pet_that_runs_out_of_food():
    p = actions.train(pet("finance", {"banking": 1}, fullness=5.0), MON_9AM, "huntsman", "banking")
    p = at(p, MON_9AM + 2 * HOUR)
    assert p.money == pytest.approx(45)
    assert p.fullness == pytest.approx(100 - 1.2 * 6.25)
    assert p.xp["finance"]["banking"] == pytest.approx(20)


def test_without_money_for_a_delivered_meal_the_pet_orders_doordash():
    p = actions.train(pet("finance", {"banking": 1}, fullness=5.0, money=1.0), MON_9AM, "huntsman", "banking")
    p = at(p, MON_9AM + 2 * HOUR)
    assert (p.doordashes, p.money) == (1, 1.0 - C.doordash_price)


def test_100_hour_week_turns_the_penalty_into_a_bonus_for_training():
    penalized = {"penalty_start": MON_9AM, "penalty_end": TUE_9AM}
    p = actions.train(pet("finance", {"banking": 2}, **penalized), MON_9AM, "huntsman", "banking")
    p = at(p, MON_9AM + 2 * HOUR)
    assert p.xp["finance"]["banking"] == pytest.approx(25)
    assert p.energy == pytest.approx(100 - 2 * 25)

    p = actions.task(pet("finance", {"banking": 2}, **penalized), MON_9AM, "huntsman")
    assert at(p, MON_9AM + 2 * HOUR).money == pytest.approx(53)


def test_burnout_immunity_keeps_an_empty_pet_working():
    p = actions.train(pet("finance", {"banking": 5}), MON_9AM, "mcneil", "markets")
    p = at(p, MON_9AM + 10 * HOUR)
    assert p.activity is Activity.TRAINING
    assert p.energy == 0
    assert p.penalty_start is None
    assert p.xp["finance"]["markets"] == pytest.approx(100)


def test_burnout_immunity_still_sends_a_hungry_pet_home():
    p = actions.train(pet("finance", {"banking": 5}, money=0.0, fullness=20.0), MON_9AM, "mcneil", "markets")
    p, events = advance(p, MON_9AM + 4 * HOUR)
    assert p.activity is Activity.HOME
    kinds = [e for e in events if not e.kind.startswith("event_")]
    assert [e.kind for e in kinds] == ["starving", "doordash", "went_home_hungry"]
    assert kinds[2].at == pytest.approx(MON_9AM + 3.2 * HOUR)
    assert (p.money, p.penalty_start) == (-C.doordash_price, None)


# --- Wet Lab experiments ---


def test_experiments_start_at_the_wet_lab_and_speed_up_all_training():
    p = actions.train(pet("premed", {"wetlab": 1, "clinical": 1}), MON_9AM, "chem", "wetlab")
    assert p.experiments == [MON_9AM + 12 * HOUR]
    p = actions.recall(p, MON_9AM + 2 * HOUR)
    p = actions.train(p, MON_9AM + 2 * HOUR, "goddard_richards", "clinical")
    p = actions.recall(p, MON_9AM + 4 * HOUR)
    assert p.xp["premed"] == {"wetlab": pytest.approx(22), "clinical": pytest.approx(22)}


def test_experiments_end_after_twelve_hours_and_dont_stack_past_the_limit():
    p = actions.train(pet("premed", {"wetlab": 1}), MON_9AM, "chem", "wetlab")
    p = actions.recall(p, MON_9AM + HOUR)
    p = actions.task(p, MON_9AM + HOUR, "wistar")
    assert p.experiments == [MON_9AM + 12 * HOUR]
    p, events = advance(actions.recall(p, MON_9AM + 2 * HOUR), MON_9AM + 13 * HOUR)
    assert p.experiments == []
    assert "experiment_done" in [e.kind for e in events]


def test_wistar_badge_runs_two_experiments():
    p = actions.train(pet("premed", {"wetlab": 4}), MON_9AM, "wistar", "wetlab")
    assert len(p.experiments) == 2
    assert at(p, MON_9AM + HOUR).xp["premed"]["wetlab"] == pytest.approx(12)


def test_no_experiments_without_the_perk():
    assert actions.train(pet("premed"), MON_9AM, "chem", "wetlab").experiments == []


# --- The Markets ---


def test_investing_is_capped_by_tier():
    p = pet("finance", {"markets": 1})
    with pytest.raises(ActionError, match=r"at most \$12.50 more"):
        actions.invest(p, MON_9AM, 13)
    p = actions.invest(p, MON_9AM, 12.5)
    assert (p.money, p.invested) == (37.5, 12.5)
    with pytest.raises(ActionError, match="larva can't invest yet"):
        actions.invest(pet("finance", {"banking": 1}), MON_9AM, 1)
    p = actions.withdraw(p, MON_9AM, 2.5)
    assert (p.money, p.invested) == (40, 10)
    with pytest.raises(ActionError, match=r"only \$10.00 is invested"):
        actions.withdraw(p, MON_9AM, 11)


def test_the_market_moves_every_morning():
    p = actions.invest(pet("finance", {"markets": 1}), MON_9AM, 10)
    market = perks_of(p).market
    p, events = advance(p, et(2026, 10, 7, 12))
    moves = [market_move(day, market, C) for day in (TUE_9AM, et(2026, 10, 7, 9))]
    assert p.invested == pytest.approx(10 * (1 + moves[0]) * (1 + moves[1]))
    assert [e.data["move"] for e in events if e.kind == "market"] == moves


def test_market_moves_are_shared_and_bounded():
    days = [TUE_9AM + n * 24 * HOUR for n in range(200)]
    wild = Market(max_share=1.0, drift=0.015, swing=0.10, loss_cap=None)
    capped = dataclasses.replace(wild, loss_cap=0.02)
    moves = [market_move(d, wild, C) for d in days]
    assert min(moves) < -0.05 and max(moves) > 0.08
    assert all(-0.085 <= m <= 0.115 for m in moves)
    assert min(market_move(d, capped, C) for d in days) == pytest.approx(-0.02)
    assert sum(moves) / len(moves) == pytest.approx(0.015, abs=0.01)


# --- Look-frequency independence ---

FINANCE = {"career": "finance", "upgrades": {"finance": {"banking": 5, "markets": 2}}, "invested": 20.0,
           "fullness": 30.0}
PREMED = {"career": "premed", "upgrades": {"premed": {"wetlab": 4, "clinical": 2}}}
PRELAW = {"career": "prelaw", "upgrades": {"prelaw": {"biglaw": 3, "public_interest": 1}}}

ACTIONS = [
    lambda p, t: actions.feed(p, t),
    lambda p, t: actions.sleep(p, t),
    lambda p, t: actions.wake(p, t),
    lambda p, t: actions.recall(p, t),
    lambda p, t: actions.train(p, t, "mcneil", "markets"),
    lambda p, t: actions.task(p, t, "mcneil"),
    lambda p, t: actions.train(p, t, "chem", "wetlab"),
    lambda p, t: actions.train(p, t, "goddard_richards", "clinical"),
    lambda p, t: actions.task(p, t, "gittis"),
    lambda p, t: actions.task(p, t, "silverman"),
    lambda p, t: actions.invest(p, t, 5),
    lambda p, t: actions.withdraw(p, t, 3),
]


@pytest.mark.parametrize("start_state", [FINANCE, PREMED, PRELAW], ids=["finance", "premed", "prelaw"])
@pytest.mark.parametrize("seed", range(10))
def test_perks_do_not_depend_on_how_often_the_pet_is_looked_at(seed, start_state):
    rng = random.Random(seed)
    start = MON_9AM + rng.uniform(0, 24 * HOUR)
    schedule = sorted(start + rng.uniform(0, 5 * 24 * HOUR) for _ in range(30))
    end = schedule[-1] + 24 * HOUR
    choices = [rng.choice(ACTIONS) for _ in schedule]

    def run(peek_every: float | None) -> Pet:
        p = dataclasses.replace(new_pet("larva", start), money=200.0, **copy.deepcopy(start_state))
        for ts, action in zip(schedule, choices):
            if peek_every:
                while p.updated_at + peek_every < ts:
                    p = at(p, p.updated_at + peek_every)
            try:
                p = action(p, ts)
            except ActionError:
                p = at(p, ts)
        return at(p, end)

    once = run(None)
    for peek in (7 * 60, 37 * 60 + 13):
        often = run(peek)
        for f in dataclasses.fields(Pet):
            _assert_close(getattr(once, f.name), getattr(often, f.name), f.name)
