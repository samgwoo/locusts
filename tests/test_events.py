"""Events while a pet works: when they come up, what answering does, what not answering does, and GPA."""

import dataclasses

import pytest
from helpers import HOUR, Outbox, et, player

from locusts import actions, events
from locusts.app import create_app
from locusts.config import DEFAULT_CONFIG as C
from locusts.events import EventError, load_events
from locusts.pet import Activity, advance, new_pet, perks_of

MON_9AM = et(2026, 10, 5, 9)
WINDOW = C.event_window_minutes * 60


def hatch(career="tech", **fields):
    return dataclasses.replace(new_pet("larva", MON_9AM, career=career), id=7, **fields)


def at(pet, t):
    return advance(pet, t)[0]


def waiting(pet, event_id, path=None):
    """The pet with event `event_id` waiting for an answer, as if it had just come up."""
    return dataclasses.replace(pet, event={"id": event_id, "at": pet.updated_at, "expires": pet.updated_at + WINDOW,
                                           "path": path or pet.path, "building": pet.building})


# --- The catalog ---

def test_the_events_load_and_fit_the_rules():
    catalog = events.templates()
    assert len(catalog) >= 20
    assert {e.when for e in catalog.values()} == {"training", "task", "work"}
    assert any(len(e.options) == 1 for e in catalog.values()) and any(len(e.options) == 2 for e in catalog.values())


@pytest.mark.parametrize(("body", "error"), [
    ('when = "work"\noptions = [{ label = "a", effects = { fullness = 5, energy = -1 } }]', "all upside"),
    ('when = "work"\noptions = [{ label = "a", effects = { fullness = 5 } }, { label = "b", effects = { energy = 5 } }]',
     "gains one thing and gives up another"),
    ('when = "task"\noptions = [{ label = "a", effects = { xp = 1 } }]', "only training events"),
    ('when = "work"\noptions = [{ label = "a", effects = { swag = 1 } }]', "can't have"),
    ('when = "sometimes"\noptions = [{ label = "a", effects = { money = 1 } }]', "when must be"),
    ('when = "work"\nbuildings = ["the_moon"]\noptions = [{ label = "a", effects = { money = 1 } }]', "unknown buildings"),
])
def test_bad_events_are_rejected(tmp_path, body, error):
    file = tmp_path / "events.toml"
    file.write_text(f'[[event]]\nid = "x"\ntitle = "X"\ntext = "x"\n{body}\n')
    with pytest.raises(EventError, match=error):
        load_events(file)


def test_a_pet_in_debt_can_still_take_money():
    pet = waiting(actions.task(hatch(money=-20.0), MON_9AM, "towne"), "tip")
    assert actions.answer_event(pet, MON_9AM, 0)[0].money == pytest.approx(-16)


# --- When they come up ---

def test_an_event_comes_up_after_two_to_three_hours_of_work():
    # By three hours it has come up, and its answer window hasn't closed yet.
    pet = at(actions.train(hatch(), MON_9AM, "towne", "grind"), MON_9AM + 3 * HOUR)
    offered = pet.event or {}
    assert pet.events_seen == 1
    assert MON_9AM + 2 * HOUR <= offered.get("at", 0) <= MON_9AM + 3 * HOUR
    assert events.templates()[offered["id"]].fits("training", "tech", "towne")


def test_events_come_up_the_same_however_often_the_pet_is_looked_at():
    start = actions.train(hatch(), MON_9AM, "towne", "grind")
    once = at(start, MON_9AM + 4 * HOUR)
    often = start
    for minutes in range(10, 4 * 60 + 1, 10):
        often = at(often, MON_9AM + minutes * 60)
    assert (once.event, once.events_seen, once.next_event_at) == (often.event, often.events_seen, often.next_event_at)


def test_an_unanswered_event_goes_away_and_changes_nothing():
    pet, seen = actions.train(hatch(), MON_9AM, "towne", "grind"), []
    pet, log = advance(pet, MON_9AM + 7 * HOUR)
    kinds = [e.kind for e in log]
    assert "event_offered" in kinds and "event_missed" in kinds
    # Seven hours of training, all of it XP: nothing an event offered got applied.
    assert pet.xp["tech"]["grind"] == pytest.approx(70)
    assert pet.gpa == C.starting_gpa


def test_working_out_brings_no_events():
    pet = at(actions.work_out(hatch(), MON_9AM), MON_9AM + 6 * HOUR)
    assert pet.events_seen == 0 and pet.event is None


def test_tasks_get_task_events_and_training_gets_training_events():
    for start, kind in ((actions.task(hatch(), MON_9AM, "huntsman"), "task"),
                        (actions.train(hatch(), MON_9AM, "towne", "grind"), "training")):
        pet = start
        for hours in range(3, 40, 3):    # several events, answered or not
            pet = at(pet, MON_9AM + hours * 3600 / 12)
            if pet.event:
                assert events.templates()[pet.event["id"]].when in (kind, "work")


# --- Answering ---

def test_answering_applies_the_option():
    pet = waiting(actions.train(hatch(), MON_9AM, "towne", "grind"), "office_hours")
    pet, landed = actions.answer_event(pet, MON_9AM, 0)
    assert landed == {"gpa": 0.06, "energy": -10}
    assert pet.gpa == pytest.approx(C.starting_gpa + 0.06) and pet.energy == 90
    assert pet.event is None
    with pytest.raises(actions.ActionError, match="no event waiting"):
        actions.answer_event(pet, MON_9AM, 0)


def test_the_other_side_of_a_tradeoff_banks_xp_in_the_path_being_trained():
    pet = waiting(actions.train(hatch(), MON_9AM, "towne", "grind"), "office_hours")
    pet, landed = actions.answer_event(pet, MON_9AM, 1)
    assert landed == {"xp": 12.0, "gpa": -0.02}
    assert pet.xp["tech"]["grind"] == pytest.approx(12)


def test_an_answer_too_late_is_refused():
    pet = waiting(actions.train(hatch(), MON_9AM, "towne", "grind"), "office_hours")
    with pytest.raises(actions.ActionError, match="no event waiting"):
        actions.answer_event(pet, MON_9AM + WINDOW, 0)


def test_options_the_pet_cant_afford_are_refused():
    broke = waiting(actions.train(hatch(money=1.0), MON_9AM, "towne", "grind"), "wawa")
    with pytest.raises(actions.ActionError, match="not enough money"):
        actions.answer_event(broke, MON_9AM, 0)
    tired = waiting(actions.train(hatch(energy=12.0), MON_9AM, "towne", "grind"), "pset")
    with pytest.raises(actions.ActionError, match="too tired"):
        actions.answer_event(tired, MON_9AM, 0)     # -12 energy would leave it with none
    with pytest.raises(actions.ActionError, match="isn't one of the options"):
        actions.answer_event(tired, MON_9AM, 2)


def test_gpa_stays_between_zero_and_four():
    top = waiting(actions.train(hatch(gpa=3.99), MON_9AM, "towne", "grind"), "office_hours")
    assert actions.answer_event(top, MON_9AM, 0)[0].gpa == C.gpa_max


# --- GPA perks and roles ---

def gunner(tier: int, **fields):
    return hatch("premed", upgrades={"premed": {"gunner": tier}}, **fields)


def test_front_row_seat_halves_what_events_cost_in_gpa():
    pet = waiting(actions.train(gunner(1), MON_9AM, "van_pelt", "gunner"), "pset")
    pet, landed = actions.answer_event(pet, MON_9AM, 1)
    assert landed["gpa"] == pytest.approx(-0.015)


def test_summa_track_speeds_up_training_on_a_high_gpa():
    def xp_after_an_hour(gpa):
        pet = at(actions.train(gunner(4, gpa=gpa), MON_9AM, "van_pelt", "gunner"), MON_9AM + HOUR)
        return pet.xp["premed"]["gunner"]
    assert xp_after_an_hour(3.9) == pytest.approx(1.5 * xp_after_an_hour(3.8))
    assert perks_of(gunner(4)).gpa_xp_bonus == 0.5


def test_top_pre_med_roles_need_a_gpa(tmp_path):
    from locusts import fairs
    from locusts.db import PetStore

    store = PetStore(str(tmp_path / "gpa.db"))
    now = et(2026, 10, 7, 12)
    pet = store.create(dataclasses.replace(new_pet("larva", now, career="premed"), upgrades={"premed": {"gunner": 2}},
                                           gpa=3.5))
    fairs.settle(store, now)
    with store.transaction() as tx:
        view = fairs.fair_view(tx, pet, now)
    surp = next(r for r in view["roles"] if r["id"] == "perelman_surp")
    assert surp["gpa"] == {"needs": 3.6, "have": 3.5}
    assert surp["problem"] == "needs a 3.6 GPA"


# --- Over the API ---

def test_events_over_the_api(tmp_path):
    class Clock:
        now = MON_9AM

        def __call__(self):
            return self.now

    clock, outbox = Clock(), Outbox()
    client = player(create_app(str(tmp_path / "api.db"), clock=clock, mailer=outbox), outbox)
    pet = client.post("/api/pets", json={"name": "larva", "career": "tech"}).json()
    assert (pet["gpa"], pet["event"]) == (C.starting_gpa, None)
    assert client.post(f"/api/pets/{pet['id']}/event", json={"option": 0}).status_code == 400

    client.post(f"/api/pets/{pet['id']}/train", json={"building": "towne", "path": "grind"})
    clock.now += 3 * HOUR
    event = client.get(f"/api/pets/{pet['id']}").json()["event"]
    assert event["title"] and event["options"] and event["expires_at"] > clock.now
    answered = client.post(f"/api/pets/{pet['id']}/event", json={"option": 0}).json()
    assert answered["event"] is None and answered["landed"] == {
        k: round(v * (C.xp_per_hour if k == "xp" else 1), 3) for k, v in
        events.templates()[event["id"]].options[0].effects.items()}
    assert answered["activity"] == Activity.TRAINING.value
