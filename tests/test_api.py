from dataclasses import replace

import pytest
from helpers import HOUR, Outbox, et, player

from locusts.app import create_app
from locusts.config import DEFAULT_CONFIG
from locusts.db import PetStore
from locusts.pet import new_pet


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return FakeClock(et(2026, 10, 5, 9))


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


def owned(db: str, name: str = "gregor") -> int:
    """The user id of the player signed up as `name`."""
    return PetStore(db).user_by_email(f"{name}@seas.upenn.edu").id


def test_full_check_in_loop(client, clock):
    pet = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()
    pet_id = pet["id"]
    assert pet["activity"] == "home"

    r = client.post(
        f"/api/pets/{pet_id}/train", json={"building": "levine", "path": "research"}
    )
    assert r.status_code == 200
    assert r.json()["activity"] == "training"

    clock.now += 4 * HOUR
    pet = client.get(f"/api/pets/{pet_id}").json()
    assert pet["energy"] == 50
    assert pet["upgrades"]["research"]["xp"] == 40

    pet = client.post(f"/api/pets/{pet_id}/recall").json()
    assert pet["activity"] == "home"
    pet = client.post(f"/api/pets/{pet_id}/feed").json()
    assert pet["fullness"] == 100


def test_buying_a_tier(client, clock):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    client.post(f"/api/pets/{pet_id}/train", json={"building": "towne", "path": "grind"})
    clock.now += 5 * HOUR

    grind = client.get(f"/api/pets/{pet_id}").json()["upgrades"]["grind"]
    assert grind == {
        "tier": 0,
        "xp": 50,
        "next": {"name": "CIS 1200: OCaml Trauma", "cost": 45},
        "blocked": None,
        "can_buy": True,
    }

    pet = client.post(f"/api/pets/{pet_id}/upgrade", json={"path": "grind"}).json()
    assert pet["upgrades"]["grind"]["tier"] == 1
    assert pet["upgrades"]["grind"]["next"]["name"] == "CIS 1210: Big-O Nightmares"

    r = client.post(f"/api/pets/{pet_id}/upgrade", json={"path": "grind"})
    assert r.status_code == 400
    assert "costs 90 XP" in r.json()["detail"]


def test_reads_do_not_change_stored_state(client, clock):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    client.post(f"/api/pets/{pet_id}/task", json={"building": "huntsman"})
    for _ in range(10):
        clock.now += 0.5 * HOUR
        client.get(f"/api/pets/{pet_id}")
    assert client.post(f"/api/pets/{pet_id}/recall").json()["money"] == pytest.approx(50 + 5 * 3)


@pytest.mark.travel
def test_a_walking_pet_reports_its_trip_until_it_arrives(client, clock):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    pet = client.post(f"/api/pets/{pet_id}/task", json={"building": "towne"}).json()
    trip = pet["trip"]
    assert trip["start"] == clock.now < trip["arrive"]
    assert pet["building"] == "towne" and pet["activity"] == "task"

    clock.now = (trip["start"] + trip["arrive"]) / 2
    assert client.get(f"/api/pets/{pet_id}").json()["trip"] == trip
    clock.now = trip["arrive"]
    assert client.get(f"/api/pets/{pet_id}").json()["trip"] is None


def test_errors(client):
    assert client.get("/api/pets/999").status_code == 404
    assert client.post("/api/pets/999/feed").status_code == 404

    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    r = client.post(f"/api/pets/{pet_id}/feed")
    assert r.status_code == 400
    assert r.json()["detail"] == "pet is already full"


def test_a_pet_shows_what_it_wears(client):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    assert client.get(f"/api/pets/{pet_id}").json()["wearing"] == []
    assert client.post(f"/api/pets/{pet_id}/buy", json={"item": "cap"}).json()["wearing"] == ["cap"]
    # Anyone can see it on the map.
    assert client.get("/api/campus").json()["pets"][0]["wearing"] == ["cap"]


def test_shop_and_gym(client, db, clock):
    config = client.get("/api/config").json()
    assert config["gym"]["building"] == "pottruck"
    assert "pottruck" in config["buildings"]
    items = {i["id"]: i for i in config["shop"]["items"]}
    assert items["ai"]["weekly"] and items["macbook"]["replaces"] == "thinkpad"

    pet_id = PetStore(db).create(replace(new_pet("gregor", clock(), career="tech"), money=300.0), owned(db)).id
    pet = client.post(f"/api/pets/{pet_id}/buy", json={"item": "thinkpad"}).json()
    assert (pet["items"], pet["swag"], pet["money"]) == (["thinkpad"], 20, 240)
    assert pet["perks"]["training_xp"] == pytest.approx(0.1)

    pet = client.post(f"/api/pets/{pet_id}/buy", json={"item": "meal_plan"}).json()
    assert pet["swipes"] == 14
    assert pet["subscriptions"]["meal_plan"]["renew"] is True
    pet = client.post(f"/api/pets/{pet_id}/cancel", json={"item": "meal_plan"}).json()
    assert pet["subscriptions"]["meal_plan"]["renew"] is False
    assert client.post(f"/api/pets/{pet_id}/cancel", json={"item": "meal_plan"}).status_code == 400

    pet = client.post(f"/api/pets/{pet_id}/workout").json()
    assert (pet["activity"], pet["building"], pet["path"]) == ("training", "pottruck", "fitness")
    clock.now += DEFAULT_CONFIG.fitness_hours_per_level * HOUR
    pet = client.get(f"/api/pets/{pet_id}").json()
    assert pet["fitness"]["level"] == 1
    assert pet["max_energy"] == 100 + DEFAULT_CONFIG.fitness_energy_per_level


def test_careers_list_pitches_without_upgrade_tiers(client):
    careers = {c["id"]: c for c in client.get("/api/careers").json()}
    assert set(careers) == {"tech", "premed", "consulting", "finance", "prelaw"}
    finance = careers["finance"]
    assert finance["name"] == "Finance"
    assert finance["summary"]
    assert [p["name"] for p in finance["paths"]] == ["Investment Banking", "The Markets", "Private Equity"]
    assert "tiers" not in finance["paths"][0]


def test_a_pet_only_sees_its_own_career(client):
    pet = client.post("/api/pets", json={"name": "gregor", "career": "prelaw"}).json()
    assert pet["career"] == "prelaw"
    assert pet["career_tree"]["id"] == "prelaw"
    assert set(pet["upgrades"]) == set(pet["career_tree"]["paths"]) == {
        "mock_trial", "biglaw", "public_interest"
    }
    assert len(pet["career_tree"]["paths"]["biglaw"]["tiers"]) == 5

    config = client.get("/api/config").json()
    assert "careers" not in config
    assert all("paths" not in b for b in config["buildings"].values())


def test_perks_show_up_in_the_pet_view_and_investing_works(client, clock):
    pet = client.post("/api/pets", json={"name": "gordon", "career": "finance"}).json()
    pet_id = pet["id"]
    assert pet["perks"]["market"] is None
    assert pet["perks"]["meal_price"] == 5
    markets = pet["career_tree"]["paths"]["markets"]["tiers"]
    assert markets[0]["perks"] and not markets[0]["later"]
    lbo = pet["career_tree"]["paths"]["private_equity"]["tiers"][2]
    assert lbo["later"] and not lbo["perks"]

    r = client.post(f"/api/pets/{pet_id}/invest", json={"amount": 5})
    assert r.json()["detail"] == "gordon can't invest yet"
    client.post(f"/api/pets/{pet_id}/train", json={"building": "mcneil", "path": "markets"})
    clock.now += 5 * HOUR
    pet = client.post(f"/api/pets/{pet_id}/upgrade", json={"path": "markets"}).json()
    market = pet["perks"]["market"]
    assert (market["max_share"], market["drift"], market["swing"]) == (0.25, 0.01, 0.05)
    assert market["max_invest"] == 12.5

    pet = client.post(f"/api/pets/{pet_id}/invest", json={"amount": 10}).json()
    assert (pet["money"], pet["invested"]) == (40, 10)
    assert pet["perks"]["market"]["max_invest"] == 2.5
    pet = client.post(f"/api/pets/{pet_id}/withdraw", json={"amount": 4}).json()
    assert (pet["money"], pet["invested"]) == (44, 6)


def test_a_pet_cannot_train_another_careers_path(client):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "prelaw"}).json()["id"]
    r = client.post(f"/api/pets/{pet_id}/train", json={"building": "towne", "path": "grind"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Pre-Law has no path grind"
    r = client.post(f"/api/pets/{pet_id}/train", json={"building": "irvine", "path": "mock_trial"})
    assert r.json()["activity"] == "training"


def test_hatching_needs_a_known_career(client):
    assert client.post("/api/pets", json={"name": "gregor"}).status_code == 422
    r = client.post("/api/pets", json={"name": "gregor", "career": "astronaut"})
    assert r.status_code == 400
    assert r.json()["detail"] == "unknown career astronaut"


def test_a_pet_from_before_careers_declares_one_once(client, db, clock):
    pet_id = PetStore(db).create(new_pet("gregor", clock()), owned(db)).id

    pet = client.get(f"/api/pets/{pet_id}").json()
    assert pet["career"] is None
    assert pet["career_tree"] is None
    assert pet["upgrades"] == {}
    r = client.post(f"/api/pets/{pet_id}/train", json={"building": "towne", "path": "grind"})
    assert r.json()["detail"] == "gregor needs to pick a career first"

    pet = client.post(f"/api/pets/{pet_id}/career", json={"career": "premed"}).json()
    assert pet["career_tree"]["name"] == "Pre-Med"
    r = client.post(f"/api/pets/{pet_id}/career", json={"career": "tech"})
    assert r.status_code == 400
    assert r.json()["detail"] == "gregor is already on the Pre-Med track"


def test_index_is_served(client):
    assert "Locusts" in client.get("/").text


def test_pages_and_static_files_are_revalidated(client):
    for path in ("/", "/walk", "/stacker", "/static/campus/locust.mjs"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    etag = client.get("/static/campus/locust.mjs").headers["etag"]
    assert client.get("/static/campus/locust.mjs", headers={"If-None-Match": etag}).status_code == 304


def test_config_places_buildings_on_the_map(client):
    config = client.get("/api/config").json()
    towne = config["buildings"]["towne"]
    assert towne["name"] == "Towne Building"
    assert 0 <= towne["x"] < config["map"]["width"]
    assert 0 <= towne["y"] < config["map"]["height"]

    image = client.get(config["map"]["image"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert len(towne["shape"]) >= 3
    harnwell = config["houses"][config["default_house"]]
    assert harnwell["building"] == "Harnwell College House"
    assert 0 <= harnwell["x"] < config["map"]["width"]
    assert config["clock"] == {"timezone": "America/New_York", "night_start": "23:00", "night_end": "09:00"}


def test_sprites_and_fonts_are_served(client):
    for path in ("/static/sprites/locust.png", "/static/sprites/icons.png",
                 "/static/fonts/silkscreen/Silkscreen-Regular.ttf", "/static/fonts/pixelify-sans/PixelifySans.ttf"):
        assert client.get(path).status_code == 200, path
    meta = client.get("/static/sprites/locust.json").json()
    assert {"idle", "hop", "train", "workout", "task", "sleep", "tired"} <= set(meta["animations"])


def test_locust_walk_practice_round_trip(client):
    practice = client.post("/api/minigames/locust-walk/practice", json={}).json()
    course, config = practice["course"], practice["config"]
    assert course["ticks"] == 60 * config["ticks_per_second"]
    assert config["pet_depth"] == config["pet_depth_px"] * config["units_per_px"]

    body = {"seed": course["seed"], "seconds": 60, "inputs": [[10, 1], [30, -1]]}
    result = client.post("/api/minigames/locust-walk/replay", json=body).json()
    assert result["ticks_played"] <= course["ticks"]
    assert result["score"] >= result["distance_px"] > 0


def test_locust_walk_longer_practice(client):
    course = client.post("/api/minigames/locust-walk/practice", json={"seconds": 66}).json()["course"]
    assert course["ticks"] == 66 * 60


@pytest.mark.parametrize("body", [
    {"seed": 1, "seconds": 60, "inputs": [[5, 2]]},
    {"seed": 1, "seconds": 60, "inputs": [["5", 1]]},
    {"seed": 1, "seconds": 60, "inputs": [[5.5, 1]]},
    {"seed": 1, "seconds": 5000, "inputs": []},
    {"seed": -1, "seconds": 60, "inputs": []},
    {"seed": 2**32, "seconds": 60, "inputs": []},
])
def test_locust_walk_rejects_bad_replays(client, body):
    assert client.post("/api/minigames/locust-walk/replay", json=body).status_code == 400


def test_a_job_fair_from_applying_to_getting_paid(client, app, outbox, clock):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    rival = player(app, outbox, "rival")
    rival_id = rival.post("/api/pets", json={"name": "rival", "career": "tech"}).json()["id"]
    fair = client.get(f"/api/pets/{pet_id}/fair").json()
    assert fair["fair"]["state"] == "upcoming"
    assert fair["fair"]["opens_at"] == et(2026, 10, 7, 9)
    assert all(r["problem"] == "the fair isn't open" for r in fair["roles"])
    assert client.post(f"/api/pets/{pet_id}/fair/runs").json()["detail"] == "no job fair is open right now"

    clock.now = et(2026, 10, 7, 12)
    fair = client.get(f"/api/pets/{pet_id}/fair").json()
    assert fair["fair"]["state"] == "open"
    assert len(fair["roles"]) == 3
    open_role = next(r for r in fair["roles"] if r["problem"] is None)
    assert open_role["pay_per_week"] == 240
    assert open_role["applicants"] is None and open_role["names"] is None
    assert fair["rules"] == {"seconds": 60, "max_hits": 3, "multiplier": 1.0, "boosts": []}

    fair = client.post(f"/api/pets/{pet_id}/fair/apply", json={"role": open_role["id"]}).json()
    assert fair["applications"] == {"limit": 2, "used": 1, "locked": False, "late_changes_left": 0,
                                    "referral": False}
    rival.post(f"/api/pets/{rival_id}/fair/apply", json={"role": open_role["id"]})
    started = client.post(f"/api/pets/{pet_id}/fair/runs").json()
    assert started["config"]["max_hits"] == 3
    assert started["course"]["ticks"] == 60 * 60
    assert started["fair"]["applications"]["locked"]
    assert started["fair"]["runs"]["pending"] == started["run"]

    clock.now += 70
    done = client.post(f"/api/pets/{pet_id}/fair/runs/{started['run']}", json={"inputs": [[20, 1]]}).json()
    assert done["run"]["score"] == done["run"]["raw_score"] > 0
    assert done["fair"]["runs"]["best"]["score"] == done["run"]["score"]
    assert done["fair"]["runs"]["pending"] is None
    r = client.post(f"/api/pets/{pet_id}/fair/runs/{started['run']}", json={"inputs": []})
    assert r.json()["detail"] == "that run was already submitted"
    assert rival.post(f"/api/pets/{rival_id}/fair/runs/{started['run']}", json={"inputs": []}).status_code == 404

    clock.now = et(2026, 10, 8, 9, 5)
    pet = client.get(f"/api/pets/{pet_id}").json()
    assert pet["internship"]["company"] == open_role["company"]
    assert pet["internship"]["pay_per_week"] == pytest.approx(240)
    assert pet["income_per_hour"] == pytest.approx(240 / 168)
    assert rival.get(f"/api/pets/{rival_id}").json()["internship"] is None

    fair = client.get(f"/api/pets/{pet_id}/fair").json()
    assert fair["fair"]["state"] == "upcoming"
    assert fair["fair"]["opens_at"] == et(2026, 10, 14, 9)
    last = fair["last"]
    assert last["won"] == {"company": open_role["company"], "title": open_role["title"]}
    assert {"company": open_role["company"], "title": open_role["title"], "winner": "gregor"} in last["placements"]
    assert len(last["placements"]) == 3
    assert rival.get(f"/api/pets/{rival_id}/fair").json()["last"]["won"] is None


def test_fair_perks_show_who_applied(app, outbox, db, clock):
    store = PetStore(db)
    clock.now = et(2026, 10, 7, 12)
    players = {}

    def hatch(name, career, upgrades):
        players[name] = player(app, outbox, name)
        pet = store.create(replace(new_pet(name, clock(), career=career), upgrades={career: upgrades}), owned(db, name))
        return players[name], pet.id

    maker, maker_id = hatch("maker", "tech", {"grind": 5})
    other, other_id = hatch("other", "tech", {})
    diligent, diligent_id = hatch("diligent", "consulting", {"case_prep": 3})
    rival, rival_id = hatch("rival", "consulting", {"staffing": 1})

    role = next(r for r in other.get(f"/api/pets/{other_id}/fair").json()["roles"] if r["problem"] is None)
    other.post(f"/api/pets/{other_id}/fair/apply", json={"role": role["id"]})
    seen = {r["id"]: r for r in maker.get(f"/api/pets/{maker_id}/fair").json()["roles"]}
    assert (seen[role["id"]]["names"], seen[role["id"]]["applicants"]) == (["other"], 1)
    assert seen["jane_street_quant"]["top"] == 4
    plain = {r["id"]: r for r in other.get(f"/api/pets/{other_id}/fair").json()["roles"]}
    assert "jane_street_quant" not in plain
    assert plain[role["id"]]["names"] is None

    for c, pid in ((diligent, diligent_id), (rival, rival_id)):
        c.post(f"/api/pets/{pid}/fair/apply", json={"role": "wharton_pro_bono"})
    pro_bono = next(r for r in diligent.get(f"/api/pets/{diligent_id}/fair").json()["roles"]
                    if r["id"] == "wharton_pro_bono")
    assert (pro_bono["applicants"], pro_bono["names"]) == (2, None)
    assert pro_bono["rivals"] == [{"name": "rival", "tiers": {"The Staffing Model": 1}}]


def test_minigame_pages_and_scripts_are_served(client):
    assert "Locust Walk" in client.get("/walk").text
    assert "Hey Day Stacker" in client.get("/stacker").text
    for script in ("walk/sim.mjs", "walk/game.mjs", "stacker/sim.mjs", "stacker/game.mjs",
                   "minigame/pixels.mjs", "minigame/shell.mjs"):
        r = client.get(f"/static/{script}")
        assert r.status_code == 200, script
        assert "javascript" in r.headers["content-type"]


def test_hey_day_stacker_practice_round_trip(client):
    practice = client.post("/api/minigames/hey-day-stacker/practice", json={"seconds": 66}).json()
    course, config = practice["course"], practice["config"]
    assert course["ticks"] == 66 * config["ticks_per_second"]
    assert config["block_px"] < config["field_px"]

    body = {"seed": course["seed"], "seconds": 66, "inputs": list(range(30, 3000, 40))}
    result = client.post("/api/minigames/hey-day-stacker/replay", json=body).json()
    assert result["score"] == result["floors"] * config["floor_points"] + result["perfects"] * config["perfect_points"]
    bad = client.post("/api/minigames/hey-day-stacker/replay", json={**body, "inputs": [[30, 1]]})
    assert bad.status_code == 400
    assert client.post("/api/minigames/quad-snake/practice", json={}).status_code == 404


def test_the_second_fair_plays_the_stacker(client, clock):
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    clock.now = et(2026, 10, 7, 12)
    assert client.get(f"/api/pets/{pet_id}/fair").json()["game"]["id"] == "locust-walk"

    clock.now = et(2026, 10, 14, 12)
    fair = client.get(f"/api/pets/{pet_id}/fair").json()
    assert fair["game"] == {"id": "hey-day-stacker", "name": "Hey Day Stacker", "page": "/stacker"}
    assert fair["last"]["game"] == "Locust Walk"
    role = next(r for r in fair["roles"] if r["problem"] is None)
    client.post(f"/api/pets/{pet_id}/fair/apply", json={"role": role["id"]})
    wrong = client.post(f"/api/pets/{pet_id}/fair/runs", json={"game": "locust-walk"})
    assert wrong.json()["detail"] == "this fair plays Hey Day Stacker"
    started = client.post(f"/api/pets/{pet_id}/fair/runs", json={"game": "hey-day-stacker"}).json()
    assert started["game"] == "hey-day-stacker"
    assert "sides" in started["course"] and started["config"]["max_hits"] == 3

    clock.now += 70
    done = client.post(f"/api/pets/{pet_id}/fair/runs/{started['run']}", json={"inputs": [30, 75, 120]}).json()
    assert set(done["run"]["stats"]) == {"floors", "perfects"}
    assert done["fair"]["runs"]["best"]["stats"] == done["run"]["stats"]
