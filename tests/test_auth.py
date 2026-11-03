"""Accounts and the protections around them: sign-up with emailed confirmation, passwords, sessions,
resets, one pet per player, nobody else's pet, and forged or cross-site requests turned away."""

import sqlite3
from contextlib import closing

import pytest
from helpers import PASSWORD, Outbox, browser, et, player

from locusts.app import create_app

EMAIL = "gregor@seas.upenn.edu"


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
    return str(tmp_path / "auth.db")


@pytest.fixture
def outbox():
    return Outbox()


@pytest.fixture
def app(db, clock, outbox):
    return create_app(db, clock=clock, mailer=outbox)


def register(client, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def login(client, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- Signing up ---

@pytest.mark.parametrize("email", ["gregor", "@gmail.com", "gregor@gmail.com@evil.com",
                                   "gregor@gmail", "gregor@", "gregor@.com",
                                   "gregor@gmail.com\r\nBcc:x@evil.com",
                                   "robert'); DROP TABLE users;--@gmail.com"])
def test_malformed_emails_cant_sign_up(app, email):
    r = register(browser(app), email)
    assert r.status_code == 400
    assert "email" in r.json()["detail"]


@pytest.mark.parametrize("email", ["gregor@upenn.edu", "Gregor@SEAS.upenn.edu", "g.r@wharton.upenn.edu",
                                   "gregor@gmail.com", "gregor@notupenn.edu"])
def test_any_email_address_can_sign_up(app, email):
    assert register(browser(app), email).status_code == 202


@pytest.mark.parametrize(("password", "problem"), [("short", "at least 10"), ("x" * 129, "at most 128"),
                                                   ("gregor@seas.upenn.edu", "can't be your email")])
def test_weak_passwords_are_refused(app, password, problem):
    r = register(browser(app), EMAIL, password)
    assert r.status_code == 400 and problem in r.json()["detail"]


def test_sign_up_confirms_the_email_then_signs_in(app, outbox):
    client = browser(app)
    assert client.get("/api/me").status_code == 401
    assert register(client).json()["detail"].startswith("Check your email")
    assert login(client).status_code == 403      # not confirmed yet
    assert len(outbox.to(EMAIL)) == 2           # the sign-up link, and a new one from that login
    r = client.get(outbox.link(EMAIL), follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?auth=verified")
    me = client.get("/api/me").json()
    assert (me["email"], me["pet_id"], me["previous_pet_id"]) == (EMAIL, None, None)
    # The link works once.
    r = browser(app).get(outbox.link(EMAIL), follow_redirects=False)
    assert r.headers["location"] == "/?auth=expired"


def test_cookies_are_httponly_and_sessions_and_passwords_are_stored_hashed(app, outbox, db):
    client = browser(app)
    register(client)
    r = client.get(outbox.link(EMAIL), follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = client.cookies["locusts_session"]
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT count(*) FROM sessions WHERE token_hash = ?", (token,)).fetchone()[0] == 0
        assert conn.execute("SELECT password_hash FROM users").fetchone()[0].startswith("scrypt$")


def test_someone_who_registers_your_email_first_cant_set_your_password(app, outbox):
    """Each registration's link sets the password chosen with it, so clicking your own link keeps yours."""
    attacker, you = browser(app), browser(app)
    register(attacker, EMAIL, "attacker password")
    register(you, EMAIL, PASSWORD)
    assert len(outbox.to(EMAIL)) == 2
    you.get(outbox.link(EMAIL))
    assert login(browser(app), EMAIL, "attacker password").status_code == 401
    assert login(browser(app)).status_code == 200


def test_registering_a_taken_email_changes_nothing(app, outbox):
    player(app, outbox)
    r = register(browser(app), EMAIL, "another password")
    assert r.status_code == 202                 # the same answer as for a new email
    assert "already has an account" in outbox.to(EMAIL)[-1]["body"]
    assert login(browser(app), EMAIL, "another password").status_code == 401
    assert login(browser(app)).status_code == 200


# --- Signing in and out ---

def test_wrong_email_or_password_get_the_same_answer(app, outbox):
    player(app, outbox)
    wrong_password, no_account = login(browser(app), EMAIL, "not the password"), login(browser(app), "x@upenn.edu")
    assert wrong_password.status_code == no_account.status_code == 401
    assert wrong_password.json() == no_account.json() == {"detail": "wrong email or password"}


def test_signing_in_and_out(app, outbox):
    player(app, outbox)
    client = browser(app)
    assert login(client).json()["email"] == EMAIL
    assert client.get("/api/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_guessing_passwords_is_rate_limited(app, outbox):
    player(app, outbox)
    client = browser(app)
    codes = [login(client, EMAIL, f"guess number {i}").status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    assert login(client).status_code == 429     # even the right password, for a while


def test_sessions_expire(app, outbox, clock):
    client = player(app, outbox)
    clock.now += 31 * 86400
    assert client.get("/api/me").status_code == 401


# --- Resetting a password ---

def test_a_reset_link_sets_a_new_password_and_signs_out_everywhere_else(app, outbox):
    old_session = player(app, outbox)
    stranger = browser(app)
    assert stranger.post("/api/auth/forgot", json={"email": "nobody@upenn.edu"}).status_code == 202
    assert stranger.post("/api/auth/forgot", json={"email": EMAIL}).status_code == 202
    token = outbox.link(EMAIL).split("reset=")[1]

    client = browser(app)
    r = client.post("/api/auth/reset", json={"token": token, "password": "a brand new password"})
    assert r.json()["email"] == EMAIL
    assert client.get("/api/me").status_code == 200
    assert old_session.get("/api/me").status_code == 401
    assert login(browser(app)).status_code == 401
    assert login(browser(app), EMAIL, "a brand new password").status_code == 200
    again = browser(app).post("/api/auth/reset", json={"token": token, "password": "yet another password"})
    assert again.status_code == 400


# --- Pets ---

def test_pets_need_a_signed_in_owner(app, outbox):
    gregor, gary = player(app, outbox), player(app, outbox, "gary")
    mine = gregor.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    assert gregor.get("/api/me").json()["pet_id"] == mine
    # One each.
    assert gregor.post("/api/pets", json={"name": "again", "career": "tech"}).status_code == 409
    # Nobody else can see or touch it, and it looks like it doesn't exist.
    for method, path in (("get", ""), ("post", "/feed"), ("post", "/buy"), ("get", "/fair"), ("post", "/fair/runs")):
        assert getattr(gary, method)(f"/api/pets/{mine}{path}").status_code == 404, path
    signed_out = browser(app)
    assert signed_out.get(f"/api/pets/{mine}").status_code == 401
    assert signed_out.post("/api/pets", json={"name": "x", "career": "tech"}).status_code == 401
    # But the map is public.
    assert [p["name"] for p in signed_out.get("/api/campus").json()["pets"]] == ["gregor"]


@pytest.mark.parametrize(("name", "problem"), [("   ", "required"), ("x" * 21, "at most 20"),
                                               ("bad\u0000name", "control characters")])
def test_pet_names_are_checked(app, outbox, name, problem):
    r = player(app, outbox).post("/api/pets", json={"name": name, "career": "tech"})
    assert r.status_code == 400 and problem in r.json()["detail"]


# --- Forged requests ---

def test_requests_without_the_header_or_from_another_site_are_refused(app, outbox):
    client = player(app, outbox)
    pet = client.post("/api/pets", json={"name": "gregor", "career": "tech"}).json()["id"]
    bare = client.post(f"/api/pets/{pet}/coffee", headers={"X-Locusts": ""})
    assert (bare.status_code, bare.json()["detail"]) == (403, "missing X-Locusts header")
    cross = client.post(f"/api/pets/{pet}/coffee", headers={"Origin": "https://evil.example"})
    assert (cross.status_code, cross.json()["detail"]) == (403, "cross-site request")
    assert client.post(f"/api/pets/{pet}/coffee", headers={"Origin": "http://testserver"}).status_code == 200


def test_huge_requests_are_refused(app, outbox):
    client = player(app, outbox)
    r = client.post("/api/minigames/locust-walk/replay", content=b"x" * 300_000,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
    too_many = client.post("/api/minigames/locust-walk/replay", json={"seed": 1, "seconds": 60,
                                                                       "inputs": [[1, 1]] * 20_001})
    assert too_many.status_code == 422


def test_every_response_has_security_headers(app):
    for path in ("/", "/api/config", "/static/campus/main.mjs"):
        headers = browser(app).get(path).headers
        assert "frame-ancestors 'none'" in headers["content-security-policy"], path
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
