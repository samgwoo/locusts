"""Semesters: three weeks each from Monday Oct 26 2026 at 9 AM, the last day a break. Semester 1 ends Sunday
Nov 15 at 9 AM and semester 2 starts Monday Nov 16."""

import dataclasses

import pytest
from helpers import HOUR, Outbox, et, player

from locusts import fairs, semesters
from locusts.app import create_app
from locusts.config import DEFAULT_CONFIG as C
from locusts.db import PetStore
from locusts.fairs import first_window, in_semester, next_window
from locusts.pet import new_pet

START = et(2026, 10, 26, 9)
END = et(2026, 11, 15, 9)
NEXT = et(2026, 11, 16, 9)


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return FakeClock(et(2026, 11, 2, 9))


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def outbox():
    return Outbox()


@pytest.fixture
def app(db, clock, outbox):
    return create_app(db, clock=clock, mailer=outbox)


@pytest.fixture
def client(app, outbox):
    return player(app, outbox)


# --- The calendar ---

def test_semesters_are_three_weeks_with_a_day_of_break():
    term = semesters.semester(1)
    assert (term.starts_at, term.ends_at, term.next_starts_at) == (START, END, NEXT)
    assert semesters.semester(2).starts_at == NEXT


def test_every_moment_falls_in_one_semester_counting_its_break():
    assert semesters.number_at(START - 1) == 0
    assert semesters.number_at(START) == 1
    assert semesters.number_at(END + 12 * HOUR) == 1
    assert semesters.number_at(NEXT - 1) == 1
    assert semesters.number_at(NEXT) == 2


def test_the_semester_runs_through_the_clock_change():
    # Daylight saving ends Nov 1, so the semester is an hour longer than 20 days of 24 hours.
    term = semesters.semester(1)
    assert term.ends_at - term.starts_at == 20 * 24 * HOUR + HOUR


def test_a_semester_has_a_fair_each_week():
    window = first_window(START)
    opens = []
    while semesters.number_at(window.opens_at) == 1:
        opens.append(window.opens_at)
        window = next_window(window)
    assert opens == [et(2026, 10, 28, 9), et(2026, 11, 4, 9), et(2026, 11, 11, 9)]
    assert window.opens_at == et(2026, 11, 18, 9)


def test_a_fair_whose_results_would_land_in_the_break_is_skipped():
    # A four-day break ends semester 1 on Thursday Nov 12 at 9 AM, before the third fair's results.
    config = dataclasses.replace(C, semester_break_days=4)
    third = fairs.window_opening(et(2026, 11, 11, 9), config)
    assert not in_semester(third, config)
    assert next_window(fairs.window_opening(et(2026, 11, 4, 9), config), config).opens_at == et(2026, 11, 18, 9)


def test_a_pet_freezes_at_the_end_of_its_semester():
    assert semesters.pet_clock(1, START, END + 5 * HOUR) == END
    assert semesters.pet_clock(1, START, END - HOUR) == END - HOUR
    assert semesters.frozen(1, END) and not semesters.frozen(1, END - 1)
    assert semesters.pet_clock(None, START, END + HOUR) == END + HOUR


def test_new_pets_join_the_semester_they_hatch_in():
    assert new_pet("larva", et(2026, 11, 2, 9)).semester == 1
    assert new_pet("larva", NEXT).semester == 2


# --- Playing through the end ---

def hatch(client, name="gregor"):
    r = client.post("/api/pets", json={"name": name, "career": "tech"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_the_semester_ends_freezes_ranks_and_starts_over(client, clock):
    pet_id = hatch(client)
    assert client.post(f"/api/pets/{pet_id}/feed").status_code == 400  # still full
    me = client.get("/api/me").json()
    assert me["semester"]["number"] == 1 and me["semester"]["over"] is False

    # The break: the pet stays as it was at the end and can't do anything.
    clock.now = END + 5 * HOUR
    pet = client.get(f"/api/pets/{pet_id}").json()
    assert pet["frozen"] is True and pet["now"] == END
    assert client.post(f"/api/pets/{pet_id}/feed").status_code == 409
    assert client.get(f"/api/pets/{pet_id}/fair").json() == {"fair": None}
    assert client.post("/api/pets", json={"name": "again", "career": "tech"}).status_code == 409
    final = client.get("/api/leaderboard").json()
    assert final["semester"]["over"] is True
    assert [p["id"] for p in final["pets"]] == [pet_id]
    assert final["pets"][0]["offers"] == []
    # Still the same pet later in the break: it doesn't starve or move.
    clock.now = END + 20 * HOUR
    assert client.get(f"/api/pets/{pet_id}").json()["fullness"] == pet["fullness"]

    # The next semester: everyone hatches again, and the old pet is history.
    clock.now = NEXT + HOUR
    me = client.get("/api/me").json()
    assert (me["pet_id"], me["previous_pet_id"], me["semester"]["number"]) == (None, pet_id, 2)
    assert client.get("/api/leaderboard").json()["pets"] == []
    assert [p["id"] for p in client.get("/api/leaderboard?semester=1").json()["pets"]] == [pet_id]
    assert client.get("/api/leaderboard?semester=3").status_code == 404
    assert client.get(f"/api/pets/{pet_id}").json()["frozen"] is True

    new_id = hatch(client, "gregor ii")
    assert client.get("/api/me").json()["pet_id"] == new_id
    assert [p["id"] for p in client.get("/api/campus").json()["pets"]] == [new_id]
    assert client.post("/api/pets", json={"name": "third", "career": "tech"}).status_code == 409


def test_fair_results_only_go_to_that_semesters_pets(db, clock):
    store = PetStore(db)
    old = store.create(new_pet("old", et(2026, 10, 20, 9)))
    clock.now = et(2026, 10, 28, 9)
    fairs.settle(store, clock.now)
    fair = store.read(lambda tx: tx.latest_fair())
    assert fair.opens_at == et(2026, 10, 28, 9)
    fairs.settle(store, fair.results_at)
    # Semester 0's pet was left as it was at the end of its semester.
    assert store.get(old.id).updated_at == old.updated_at


def test_pets_from_before_semesters_join_the_current_one(db, clock):
    store = PetStore(db)
    pet = store.create(dataclasses.replace(new_pet("old", et(2026, 10, 1, 9)), semester=None))
    create_app(db, clock=clock, mailer=Outbox())
    assert store.get(pet.id).semester == 1
