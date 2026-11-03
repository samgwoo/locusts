import dataclasses

import pytest
from fastapi.testclient import TestClient
from helpers import HOUR, Outbox, et, player

from locusts import actions
from locusts.app import create_app
from locusts.config import DEFAULT_CONFIG as C
from locusts.db import PetStore
from locusts.houses import HouseError, houses, load_houses
from locusts.pet import advance, new_pet, perks_of, spot

MON_9AM = et(2026, 10, 5, 9)


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return FakeClock(MON_9AM)


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
    """Signed in as gregor, who hasn't hatched a pet yet."""
    return player(app, outbox)


def hatch(house: str, **fields):
    return dataclasses.replace(new_pet("larva", MON_9AM, career="tech", house=house), **fields)


# --- Catalog ---


def test_houses_load_and_the_default_is_one_of_them():
    assert {"harnwell", "hill", "stouffer"} <= set(houses())
    assert C.default_house in houses()


@pytest.mark.parametrize(("body", "error"), [
    ('effects = { experiments = 1 }', "can't have"),
    ('effects = { training_xp = -0.1 }', "non-negative"),
    ('effects = {}\nperks = []', "unknown fields"),
])
def test_bad_houses_are_rejected(tmp_path, body, error):
    file = tmp_path / "houses.toml"
    file.write_text(f'[[house]]\nid = "x"\nname = "X"\nmap_name = "Hill College House"\nblurb = ""\n{body}\n')
    with pytest.raises(HouseError, match=error):
        load_houses(file)


# --- Living somewhere ---


def test_new_pets_live_in_their_house():
    assert new_pet("larva", MON_9AM).house == C.default_house
    assert spot(None, C, "hill") == spot(None, C, houses()["hill"].id)
    assert spot(None) == spot(None, C, C.default_house)


@pytest.mark.travel
def test_a_pet_walks_home_to_its_own_house():
    pet = actions.task(hatch("hill"), MON_9AM, "towne")
    pet = advance(pet, pet.trip["arrive"])[0]
    pet = actions.recall(pet, pet.updated_at)
    assert pet.trip["to"] == list(spot(None, C, "hill"))
    assert pet.trip["to"] != list(spot(None, C, "harnwell"))


@pytest.mark.houses
def test_house_perks_apply():
    assert perks_of(hatch("rodin")).training_xp == pytest.approx(0.05)
    assert perks_of(hatch("english")).meal_discount == pytest.approx(0.10)
    pet = actions.train(hatch("rodin"), MON_9AM, "towne", "grind")
    pet = advance(pet, MON_9AM + 2 * HOUR)[0]
    assert pet.xp["tech"]["grind"] == pytest.approx(2 * C.xp_per_hour * 1.05)


def test_house_perks_are_off_in_other_tests():
    assert perks_of(hatch("rodin")).training_xp == 0


# --- API ---


def test_hatching_into_a_house(client, app, outbox):
    pet = client.post("/api/pets", json={"name": "gregor", "career": "tech", "house": "lauder"}).json()
    assert pet["house"] == "lauder"
    default = player(app, outbox, "gary").post("/api/pets", json={"name": "gary", "career": "tech"}).json()
    assert default["house"] == C.default_house
    r = client.post("/api/pets", json={"name": "x", "career": "tech", "house": "the_quad"})
    assert r.status_code == 400
    assert "unknown house" in r.json()["detail"]

    houses_config = client.get("/api/config").json()["houses"]
    assert houses_config["lauder"]["building"] == "Lauder College House"
    assert houses_config["lauder"]["blurb"]


def test_campus_shows_every_pet_but_only_public_fields(client, app, outbox, clock):
    a = client.post("/api/pets", json={"name": "gregor", "career": "tech", "house": "hill"}).json()["id"]
    gary = player(app, outbox, "gary")
    b = gary.post("/api/pets", json={"name": "gary", "career": "finance", "house": "rodin"}).json()["id"]
    client.post(f"/api/pets/{a}/train", json={"building": "towne", "path": "grind"})

    pets = {p["id"]: p for p in client.get("/api/campus").json()["pets"]}
    assert set(pets) == {a, b}
    assert pets[a] == {"id": a, "name": "gregor", "house": "hill", "career": "Tech", "activity": "training",
                       "building": "towne", "trip": None, "tired": False, "wearing": []}
    assert pets[b]["activity"] == "home" and pets[b]["building"] is None
    assert not {"money", "energy", "upgrades", "xp"} & set(pets[a])

    clock.now += 7.9 * HOUR
    assert client.get("/api/campus").json()["pets"][0]["tired"] is True


@pytest.mark.travel
def test_campus_shows_a_walking_pets_trip(client):
    a = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    trip = client.post(f"/api/pets/{a}/task", json={"building": "towne"}).json()["trip"]
    assert client.get("/api/campus").json()["pets"][0]["trip"] == trip


def test_leaderboard_ranks_pets_and_averages_houses(db, clock):
    store = PetStore(db)

    def add(name, house, money, items=()):
        pet = dataclasses.replace(new_pet(name, clock(), career="tech", house=house), money=money, items=list(items))
        return store.create(pet).id

    add("rich", "hill", 500.0)
    add("poor", "hill", 100.0)
    add("solo", "rodin", 250.0, items=["thinkpad"])
    client = TestClient(create_app(db, clock=clock))

    board = client.get("/api/leaderboard").json()
    assert [p["name"] for p in board["pets"]] == ["rich", "solo", "poor"]
    # The ThinkPad counts at its $60 price, and its swag on top.
    assert board["pets"][1] == {"id": 3, "name": "solo", "house": "rodin", "career": "Tech", "net_worth": 310,
                                "swag": 20, "score": 330, "interning": False, "offers": []}
    # Which puts solo's house ahead: averages, so one well-equipped pet beats two.
    rodin, hill, *rest = board["houses"]
    assert (rodin["id"], rodin["members"], rodin["score"]) == ("rodin", 1, 330)
    assert (hill["id"], hill["members"], hill["score"]) == ("hill", 2, 300)
    assert all(h["members"] == 0 for h in rest)
    assert len(board["houses"]) == len(houses())
