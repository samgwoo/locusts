"""What a player might try: SQL in a field, a forged Host header, a path out of /static, an extra JSON field,
and a number that isn't one."""

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from helpers import PASSWORD, Outbox, browser, et, player

from locusts.app import create_app
from locusts.db import PetStore, _guard_sql

# A whole statement, short enough for a pet name. Bound as a value, it has to come back unchanged.
INJECTION = "';DROP TABLE pets;--"


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
    return str(tmp_path / "security.db")


@pytest.fixture
def outbox():
    return Outbox()


@pytest.fixture
def app(db, clock, outbox):
    return create_app(db, clock=clock, mailer=outbox)


def tables(path: str) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_sql_in_a_name_is_stored_as_a_name(app, outbox, db):
    client = player(app, outbox)
    created = client.post("/api/pets", json={"name": INJECTION, "career": "tech"})
    assert created.status_code == 200
    pet_id = created.json()["id"]
    assert client.get(f"/api/pets/{pet_id}").json()["name"] == INJECTION
    campus = client.get("/api/campus")
    assert campus.headers["content-type"].startswith("application/json")
    assert campus.json()["pets"][0]["name"] == INJECTION
    assert {"users", "pets", "sessions"} <= tables(db)


def test_sql_in_a_building_doesnt_change_the_database(app, outbox, db):
    client = player(app, outbox)
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    before = tables(db)
    r = client.post(f"/api/pets/{pet_id}/task", json={"building": "towne'; DROP TABLE pets;--"})
    assert r.status_code == 400
    assert client.get(f"/api/pets/{pet_id}").status_code == 200
    assert tables(db) == before


def test_a_query_string_isnt_sql(app, outbox):
    client = player(app, outbox)
    assert client.get("/api/leaderboard", params={"semester": "1; DROP TABLE pets"}).status_code == 422
    assert client.get("/api/pets/1 OR 1=1").status_code in (404, 422)
    assert client.get("/api/pets/" + "9" * 30).status_code == 404
    assert client.get("/api/leaderboard", params={"semester": -1}).status_code == 404


def test_extra_fields_and_non_numbers_are_refused(app, outbox):
    client = player(app, outbox)
    extra = client.post("/api/pets", json={"name": "gregor", "career": "tech", "money": 1_000_000})
    assert extra.status_code == 422
    assert client.get("/api/me").json()["pet_id"] is None
    pet_id = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    for raw in (b'{"amount": NaN}', b'{"amount": Infinity}', b'{"amount": -Infinity}'):
        r = client.post(f"/api/pets/{pet_id}/invest", content=raw, headers={"content-type": "application/json"})
        assert r.status_code == 422, raw


def test_static_files_cant_leave_their_directory(app):
    client = browser(app)
    for path in ("/static/%2e%2e/auth.py", "/static/campus/%2e%2e/%2e%2e/auth.py",
                 "/static/campus/..%2f..%2fauth.py"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert b"hash_password" not in r.content


def test_a_forged_host_isnt_written_into_an_email(app, outbox):
    evil = TestClient(app, base_url="http://evil.example", headers={"X-Locusts": "1"})
    r = evil.post("/api/auth/register", json={"email": "gregor@gmail.com", "password": PASSWORD})
    assert r.status_code == 400 and "LOCUSTS_BASE_URL" in r.json()["detail"]
    assert outbox.sent == []


def test_a_configured_address_is_what_emails_use(db, clock, outbox, monkeypatch):
    monkeypatch.setenv("LOCUSTS_BASE_URL", "https://play.locusts.example")
    app = create_app(db, clock=clock, mailer=outbox)
    evil = TestClient(app, base_url="http://evil.example", headers={"X-Locusts": "1"})
    r = evil.post("/api/auth/register", json={"email": "gregor@gmail.com", "password": PASSWORD})
    assert r.status_code == 202
    body = outbox.to("gregor@gmail.com")[-1]["body"]
    assert "https://play.locusts.example/auth/verify?token=" in body
    assert "evil.example" not in body
    cross = evil.post("/api/auth/login", json={"email": "gregor@gmail.com", "password": PASSWORD},
                      headers={"Origin": "http://evil.example"})
    assert cross.status_code == 403


def test_the_database_wont_attach_another_file(tmp_path):
    assert _guard_sql(sqlite3.SQLITE_ATTACH, "x", None, None, None) == sqlite3.SQLITE_DENY
    assert _guard_sql(sqlite3.SQLITE_FUNCTION, None, "readfile", None, None) == sqlite3.SQLITE_DENY
    assert _guard_sql(sqlite3.SQLITE_FUNCTION, None, "lower", None, None) == sqlite3.SQLITE_OK
    store = PetStore(str(tmp_path / "game.db"))
    other = tmp_path / "other.db"
    with closing(store._connect()) as conn:
        with pytest.raises(sqlite3.Error, match="authoriz"):
            conn.execute("ATTACH DATABASE ? AS other", (str(other),))
    assert not other.exists()
