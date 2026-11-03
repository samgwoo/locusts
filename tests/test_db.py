import sqlite3
from contextlib import closing

from helpers import et

from locusts.db import PetStore

OLD_SCHEMA = """
CREATE TABLE pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, fullness REAL NOT NULL,
    energy REAL NOT NULL, money REAL NOT NULL, activity TEXT NOT NULL, building TEXT, career TEXT,
    path TEXT, xp TEXT NOT NULL, upgrades TEXT NOT NULL, rest_multiplier REAL NOT NULL,
    sleep_hours REAL NOT NULL, day_started_at REAL NOT NULL, penalty_start REAL, penalty_end REAL,
    coffee_until REAL, updated_at REAL NOT NULL
)
"""


def test_a_database_from_before_perks_gets_the_new_columns(tmp_path):
    path = str(tmp_path / "old.db")
    now = et(2026, 10, 5, 9)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(OLD_SCHEMA)
        conn.execute("PRAGMA user_version = 1")   # past the accounts migration, which clears old pets
        conn.execute(
            "INSERT INTO pets (name, fullness, energy, money, activity, career, xp, upgrades, "
            "rest_multiplier, sleep_hours, day_started_at, updated_at) "
            "VALUES ('gregor', 100, 100, 50, 'home', 'tech', '{}', '{}', 1, 0, ?, ?)",
            (now, now),
        )
        conn.commit()

    store = PetStore(path)
    pet = store.get(1)
    assert (pet.invested, pet.experiments, pet.work_started_at, pet.billed_day) == (0, [], None, None)
    assert (pet.internship, pet.best_internship_pay, pet.bonuses) == (None, 0, [])
    assert (pet.train_day, pet.train_streak, pet.trip) == (None, 0, None)
    assert (pet.items, pet.subscriptions, pet.swipes, pet.fitness) == ([], {}, 0, 0)
    assert pet.house == "harnwell"
    assert store.read(lambda tx: tx.latest_fair()) is None

    trip = {"from": [1.5, 2.0], "to": [300, 400], "start": now, "arrive": now + 1.5}

    subs = {"meal_plan": {"until": now + 600, "renew": False}}

    def change(p):
        p.experiments = [now + 60]
        p.trip = trip
        p.items = ["thinkpad"]
        p.subscriptions = subs
        p.swipes = 3
        p.fitness = 4.5
        return p

    store.update(1, change)
    saved = PetStore(path).get(1)
    assert (saved.experiments, saved.trip) == ([now + 60], trip)
    assert (saved.items, saved.subscriptions, saved.swipes, saved.fitness) == (["thinkpad"], subs, 3, 4.5)


def test_pets_from_before_accounts_are_cleared(tmp_path):
    """Nobody owned them, so anyone could have played them; the game starts over with accounts."""
    path = str(tmp_path / "old.db")
    now = et(2026, 10, 5, 9)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO pets (name, fullness, energy, money, activity, career, xp, upgrades, "
            "rest_multiplier, sleep_hours, day_started_at, updated_at) "
            "VALUES ('gregor', 100, 100, 50, 'home', 'tech', '{}', '{}', 1, 0, ?, ?)",
            (now, now),
        )
        conn.commit()
    store = PetStore(path)
    assert store.get(1) is None
    # Only once: pets made after the migration stay.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "INSERT INTO pets (name, fullness, energy, money, activity, career, xp, upgrades, "
            "rest_multiplier, sleep_hours, day_started_at, updated_at) "
            "VALUES ('gary', 100, 100, 50, 'home', 'tech', '{}', '{}', 1, 0, ?, ?)",
            (now, now),
        )
        conn.commit()
    assert PetStore(path).get(2).name == "gary"


OLD_FAIRS = """
CREATE TABLE fairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, opens_at REAL NOT NULL UNIQUE, closes_at REAL NOT NULL,
    results_at REAL NOT NULL, roles TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE fair_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, fair_id INTEGER NOT NULL, pet_id INTEGER NOT NULL,
    seed INTEGER NOT NULL, seconds INTEGER NOT NULL, max_hits INTEGER NOT NULL, multiplier REAL NOT NULL,
    started_at REAL NOT NULL, submitted_at REAL, raw_score INTEGER, score REAL, hits INTEGER,
    distance INTEGER, swag INTEGER
);
"""


def test_fairs_from_before_the_stacker_were_locust_walk_fairs(tmp_path):
    path = str(tmp_path / "old.db")
    now = et(2026, 10, 7, 9)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(OLD_FAIRS)
        conn.execute("PRAGMA user_version = 1")   # past the accounts migration, which clears old runs
        conn.execute("INSERT INTO fairs (opens_at, closes_at, results_at, roles) VALUES (?, ?, ?, '[]')",
                     (now, now + 1, now + 2))
        conn.execute("INSERT INTO fair_attempts (fair_id, pet_id, seed, seconds, max_hits, multiplier, started_at, "
                     "submitted_at, raw_score, score, hits, distance, swag) VALUES (1, 1, 5, 60, 3, 1, ?, ?, 900, 900, "
                     "1, 800, 2)", (now, now))
        conn.execute("INSERT INTO fair_attempts (fair_id, pet_id, seed, seconds, max_hits, multiplier, started_at) "
                     "VALUES (1, 1, 6, 60, 3, 1, ?)", (now,))
        conn.commit()

    store = PetStore(path)
    assert store.read(lambda tx: tx.fair(1)).game == "locust-walk"
    played, pending = store.read(lambda tx: tx.attempts(1))
    assert (played.raw_score, played.stats, pending.stats) == (900, {"distance": 800, "swag": 2}, {})
    with store.transaction() as tx:
        stacker = tx.add_fair(now + 7 * 86400, now + 7 * 86400 + 1, now + 7 * 86400 + 2, [], "hey-day-stacker")
        pending.submitted_at, pending.raw_score, pending.stats = now, 50, {"floors": 4, "perfects": 1}
        tx.save_attempt(pending)
    assert PetStore(path).read(lambda tx: (tx.fair(stacker.id).game, tx.attempt(2).stats)) == (
        "hey-day-stacker", {"floors": 4, "perfects": 1})
