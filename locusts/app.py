import logging
import os
import re
import sqlite3
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from locusts import actions, auth, campus_map, campus_time, fairs, semesters, shop
from locusts import events as event_book
from locusts.config import DEFAULT_CONFIG, Config
from locusts.db import PetStore, Tx
from locusts.houses import houses
from locusts.minigames.games import GAMES, Game
from locusts.minigames.rng import MASK32, new_seed
from locusts.perks import Perks, market_move
from locusts.pet import (
    WEEK_HOURS,
    Pet,
    advance,
    coffee_active,
    fitness_level,
    in_transit,
    income,
    interning,
    job_pay,
    max_energy,
    meal_price,
    new_pet,
    penalty_strength,
    perks_of,
    work_speed,
)
from locusts.upgrades import Career, careers_for, upgrade_blocker
from locusts.weather import Weather

_STATIC = Path(__file__).parent / "static"
_PRACTICE_SECONDS = range(10, 121)
# Browsers otherwise reuse cached modules without asking, so an edited .mjs wouldn't load until the
# cache expired. no-cache still lets them reuse a copy once the server confirms it's unchanged.
_REVALIDATE = {"Cache-Control": "no-cache"}


class _RevalidatedFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(_REVALIDATE)
        return response


def _mean(rows: list[dict], key: str) -> float:
    return round(sum(r[key] for r in rows) / len(rows), 2) if rows else 0.0


log = logging.getLogger("locusts")

# Cookie holding the session token. HttpOnly, so page scripts never see it.
_COOKIE = "locusts_session"
# Unsafe /api requests must carry this header: a page on another site can't add a custom header to a request
# here without a CORS preflight, which this server never approves, so it can't forge requests with a
# player's cookie.
_CSRF_HEADER = "x-locusts"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_MAX_BODY = 256 * 1024
_PET_PATH = re.compile(r"^/api/pets/(\d+)(?:/|$)")
# Bigger than this doesn't fit in a SQLite integer, and sqlite3 would error or coerce it instead of missing.
_MAX_ID = 10**18
_NAME_MAX = 20
# Career, building, item, role, and game ids are all short. Anything longer is not one of them.
_LABEL_MAX = 64
# Any game's inputs for a two-minute run fit well under this.
_MAX_INPUTS = 20_000
# Sent with every response. Scripts, styles, images, and requests only from this server; no framing (so no
# clickjacking); no Referer, so the token in a password-reset link never leaks to another site.
_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
                               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class _Body(BaseModel):
    """A request body. Fields the route doesn't ask for are rejected, so a player can't slip in money, a
    password hash, or anything else by adding it to the JSON."""

    model_config = ConfigDict(extra="forbid")


class Credentials(_Body):
    email: str = Field(max_length=auth.EMAIL_MAX)
    password: str = Field(max_length=1024)


class EmailRequest(_Body):
    email: str = Field(max_length=auth.EMAIL_MAX)


class ResetRequest(_Body):
    token: str = Field(max_length=128)
    password: str = Field(max_length=1024)


class NewPet(_Body):
    name: str = Field(max_length=200)
    career: str = Field(max_length=_LABEL_MAX)
    house: str | None = Field(default=None, max_length=_LABEL_MAX)


class CareerRequest(_Body):
    career: str = Field(max_length=_LABEL_MAX)


class WorkRequest(_Body):
    building: str = Field(max_length=_LABEL_MAX)
    path: str | None = Field(default=None, max_length=_LABEL_MAX)


class UpgradeRequest(_Body):
    path: str = Field(max_length=_LABEL_MAX)


class MoneyRequest(_Body):
    amount: float = Field(gt=0, le=1e9, allow_inf_nan=False)


class ItemRequest(_Body):
    item: str = Field(max_length=_LABEL_MAX)


class EventAnswer(_Body):
    option: int


class PracticeRequest(_Body):
    seconds: int | None = None


class ReplayRequest(_Body):
    seed: int
    seconds: int
    # Left untyped so each game's replay() rejects inputs that aren't exactly its format.
    inputs: list = Field(max_length=_MAX_INPUTS)


class ApplyRequest(_Body):
    role: str = Field(max_length=_LABEL_MAX)
    instead_of: str | None = Field(default=None, max_length=_LABEL_MAX)


class BackRequest(_Body):
    pet: int = Field(ge=1, le=_MAX_ID)


class ShortRequest(_Body):
    pet: int = Field(ge=1, le=_MAX_ID)
    amount: float = Field(gt=0, le=1e9, allow_inf_nan=False)


class WithdrawRequest(_Body):
    role: str = Field(max_length=_LABEL_MAX)


class StartRunRequest(_Body):
    # The game the client is about to play, so a page for the wrong game can't start a run.
    game: str | None = Field(default=None, max_length=_LABEL_MAX)


class RunRequest(_Body):
    inputs: list = Field(max_length=_MAX_INPUTS)


def _pet_name(raw: str) -> str:
    """A pet's name as entered, trimmed, or a 400 if it's empty, too long, or has control characters."""
    name = unicodedata.normalize("NFC", raw).strip()
    if not name:
        raise HTTPException(400, "name is required")
    if len(name) > _NAME_MAX:
        raise HTTPException(400, f"names can be at most {_NAME_MAX} characters")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise HTTPException(400, "names can't have control characters")
    return name


def create_app(
    db_path: str | None = None,
    config: Config = DEFAULT_CONFIG,
    clock: Callable[[], float] = time.time,
    mailer: auth.Mailer | None = None,
    weather: Weather | None = None,
) -> FastAPI:
    careers = careers_for(config)
    store = PetStore(db_path or os.environ.get("LOCUSTS_DB", "locusts.db"))
    store.adopt(semesters.number_at(clock(), config))
    mailer = mailer or auth.mailer_from_env()
    limits = auth.RateLimiter()
    sky_weather = weather if weather is not None else Weather(
        clock=clock, latitude=config.weather_latitude, longitude=config.weather_longitude,
        timezone=config.timezone.key)
    # Where emailed links point, and the only origin allowed to make unsafe requests. Set it in production
    # (https://...), especially behind a proxy; otherwise it's taken from each request.
    base_url = os.environ.get("LOCUSTS_BASE_URL", "").rstrip("/") or None
    trust_proxy = os.environ.get("LOCUSTS_TRUST_PROXY") == "1"
    # Hosts an emailed link may name when LOCUSTS_BASE_URL isn't set. A request's Host header from anywhere
    # else is not this server: a confirmation link built from it would hand the token to whoever forged it.
    link_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    link_hosts.update(h.strip().lower().strip("[]") for h in os.environ.get("LOCUSTS_ALLOWED_HOSTS", "").split(",")
                      if h.strip())
    app = FastAPI(title="Locusts")
    app.mount("/static", _RevalidatedFiles(directory=_STATIC), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        """A body that doesn't match the route. NaN and Infinity aren't JSON, so the usual error report
        can't be sent back; the value isn't repeated either way."""
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    def site(request: Request) -> str:
        return base_url or f"{request.url.scheme}://{request.url.netloc}"

    def link_site(request: Request) -> str:
        """Origin written into an emailed link. The configured public address, or a host we trust. Never a
        Host header from somewhere else."""
        if base_url:
            return base_url
        host = (request.url.hostname or "").lower().strip("[]")
        if host not in link_hosts:
            raise HTTPException(400, "set LOCUSTS_BASE_URL before this server can send links")
        return site(request)

    def secure(request: Request) -> bool:
        return site(request).startswith("https://")

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if trust_proxy and forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "?"

    def deny(status: int, detail: str) -> JSONResponse:
        return JSONResponse({"detail": detail}, status_code=status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Every request: forged and oversized ones are turned away, the session cookie says who's asking, and
        a pet's routes answer only its owner (as if any other pet didn't exist)."""
        path = request.url.path
        api = path.startswith("/api/")
        if api and request.method not in _SAFE_METHODS:
            if request.headers.get(_CSRF_HEADER) != "1":
                return deny(403, "missing X-Locusts header")
            origin = request.headers.get("origin")
            if origin is not None and origin != site(request):
                return deny(403, "cross-site request")
            if int(request.headers.get("content-length") or 0) > _MAX_BODY:
                return deny(413, "request too large")
        request.state.user = None
        token = request.cookies.get(_COOKIE)
        if token and (api or path.startswith("/auth/")):
            request.state.user = store.session_user(auth.token_hash(token), clock())
        pet = _PET_PATH.match(path)
        if pet:
            if len(pet[1]) > 18:
                return deny(404, "pet not found")
            if request.state.user is None:
                return deny(401, "sign in first")
            if not store.owns(request.state.user.id, int(pet[1])):
                return deny(404, "pet not found")
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        if secure(request):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # --- Accounts ---

    def throttle(request: Request, *keys: tuple[str, int, float]) -> None:
        """A 429 unless every (key, limit, window) still has room; each also counts per IP, with ten times the
        room, so one address can't work through many accounts."""
        now = clock()
        ip = client_ip(request)
        for key, limit, window in keys:
            if not (limits.allow(key, limit, window, now) and limits.allow(f"{key.split(':')[0]}:ip:{ip}",
                                                                           limit * 10, window, now)):
                raise HTTPException(429, "too many attempts; try again later")

    def deliver(to: str, subject: str, body: str) -> None:
        try:
            mailer.send(to, subject, body)
        except Exception:
            log.exception("couldn't send email to %s", to)

    def email_link(background: BackgroundTasks, request: Request, user, purpose: str,
                   password_hash: str | None = None, home: str | None = None) -> None:
        home = link_site(request) if home is None else home
        token = auth.new_token()
        hours = auth.VERIFY_HOURS if purpose == "verify" else auth.RESET_HOURS
        store.add_email_token(user.id, auth.token_hash(token), purpose, clock() + hours * 3600,
                              password_hash=password_hash, replace=purpose != "verify")
        if purpose == "verify":
            link = f"{home}/auth/verify?token={token}"
            subject = "Confirm your Locusts account"
            body = (f"Confirm your email to start playing Locusts:\n\n{link}\n\n"
                    f"The link works for {hours} hours. If you didn't just sign up, ignore this email.")
        else:
            link = f"{home}/?reset={token}"
            subject = "Reset your Locusts password"
            body = (f"Choose a new password for Locusts:\n\n{link}\n\n"
                    f"The link works for {hours} hour. If you didn't ask for it, ignore this email: "
                    "your password stays the same.")
        background.add_task(deliver, user.email, subject, body)

    def sign_in(response: Response, request: Request, user_id: int) -> None:
        token, now = auth.new_token(), clock()
        store.add_session(user_id, auth.token_hash(token), now, now + auth.SESSION_DAYS * 86400)
        response.set_cookie(_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                            secure=secure(request), path="/")

    @app.post("/api/auth/register", status_code=202)
    def register(body: Credentials, request: Request, background: BackgroundTasks):
        """Sign up with an email: the account works once the emailed link is clicked. The answer is the
        same whether or not the email already has an account, so it can't be used to find out."""
        email = auth.normalize_email(body.email)
        if not auth.valid_email(email):
            raise HTTPException(400, "enter an email address")
        problem = auth.password_problem(body.password, email)
        if problem:
            raise HTTPException(400, problem)
        home = link_site(request)
        throttle(request, (f"mail:{email}", 3, 3600))
        password_hash = auth.hash_password(body.password)
        user = store.add_user(email, password_hash, clock()) or store.user_by_email(email)
        if user.verified_at is None:
            email_link(background, request, user, "verify", password_hash, home)
        else:
            background.add_task(deliver, email, "Your Locusts account",
                                "Someone tried to sign up for Locusts with this email, which already has an "
                                f"account. If it was you, sign in at {home}, or reset your password "
                                "there if you've forgotten it.")
        return {"detail": "Check your email for a link to finish signing up."}

    @app.get("/auth/verify")
    def verify(request: Request, token: str = ""):
        """The emailed sign-up link: confirms the email, sets the password chosen with it, and signs in."""
        taken = store.take_email_token(auth.token_hash(token), "verify", clock()) if len(token) <= 128 else None
        if taken is None:
            return RedirectResponse("/?auth=expired", status_code=303)
        user_id, password_hash = taken
        if password_hash:
            store.set_password(user_id, password_hash)
        store.mark_verified(user_id, clock())
        store.end_sessions(user_id)
        response = RedirectResponse("/?auth=verified", status_code=303)
        sign_in(response, request, user_id)
        return response

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response, background: BackgroundTasks):
        email = auth.normalize_email(body.email)
        throttle(request, (f"login:{email}", 10, 900))
        user = store.user_by_email(email)
        if user is None:
            auth.check_nobody(body.password)
            raise HTTPException(401, "wrong email or password")
        if not auth.check_password(body.password, user.password_hash):
            raise HTTPException(401, "wrong email or password")
        if user.verified_at is None:
            throttle(request, (f"mail:{email}", 3, 3600))
            email_link(background, request, user, "verify", user.password_hash)
            # Returned rather than raised, so the new link still goes out.
            return JSONResponse({"detail": "confirm your email first: we've sent you a new link"}, status_code=403,
                                background=background)
        sign_in(response, request, user.id)
        return me(request, user)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(_COOKIE)
        if token:
            store.end_session(auth.token_hash(token))
        response.delete_cookie(_COOKIE, path="/")
        return {"detail": "Signed out."}

    @app.post("/api/auth/forgot", status_code=202)
    def forgot(body: EmailRequest, request: Request, background: BackgroundTasks):
        """Email a password-reset link, if the email has an account (the answer is the same either way)."""
        link_site(request)
        email = auth.normalize_email(body.email)
        throttle(request, (f"mail:{email}", 3, 3600))
        user = store.user_by_email(email)
        if user is not None:
            email_link(background, request, user, "reset")
        return {"detail": "If that email has an account, we've sent it a link to reset the password."}

    @app.post("/api/auth/reset")
    def reset(body: ResetRequest, request: Request, response: Response):
        """Set a new password with an emailed reset link's token, sign out everywhere else, and sign in."""
        throttle(request, (f"reset:{client_ip(request)}", 10, 900))
        taken = store.take_email_token(auth.token_hash(body.token), "reset", clock())
        if taken is None:
            raise HTTPException(400, "that reset link has expired or was already used; ask for a new one")
        user = store.user(taken[0])
        problem = auth.password_problem(body.password, user.email)
        if problem:
            # The link's used up either way; send another, or someone could try passwords on one forever.
            raise HTTPException(400, f"{problem}; ask for a new link and try again")
        store.set_password(user.id, auth.hash_password(body.password))
        store.mark_verified(user.id, clock())
        store.end_sessions(user.id)
        sign_in(response, request, user.id)
        return me(request, store.user(user.id))

    @app.get("/api/me")
    def get_me(request: Request):
        if request.state.user is None:
            raise HTTPException(401, "sign in first")
        return me(request, request.state.user)

    def me(request: Request, user) -> dict:
        """Who's signed in, the semester (counting its break), and their pet in it and in the one before."""
        now = clock()
        term = semesters.semester_at(now, config)
        return {"email": user.email, "pet_id": store.pet_of(user.id, term.number), "semester": semester_view(term),
                "previous_pet_id": store.pet_of(user.id, term.number - 1)}

    def semester_view(term: semesters.Semester) -> dict:
        return {"number": term.number, "starts_at": term.starts_at, "ends_at": term.ends_at,
                "next_starts_at": term.next_starts_at, "over": term.over(clock())}

    def tick() -> float:
        """The request's timestamp, after resolving any job fair whose results are due by then."""
        now = clock()
        fairs.settle(store, now, config)
        return now

    def bring(pet: Pet, now: float) -> tuple[Pet, float]:
        """The pet as it is at `now`, or as it was when its semester ended, and the time that is."""
        t = semesters.pet_clock(pet.semester, pet.updated_at, now, config)
        return advance(pet, t, config)[0], t

    def view(pet: Pet, now: float) -> dict:
        frozen = semesters.frozen(pet.semester, now, config)
        pet, now = bring(pet, now)
        perks = perks_of(pet, config)
        job = pet.internship if interning(pet, now) else None
        return {
            "id": pet.id,
            "name": pet.name,
            "house": pet.house,
            "semester": pet.semester,
            # The semester is over: the pet stays as it was at the end, and nothing can be done with it.
            "frozen": frozen,
            "doordashes": pet.doordashes,
            "fullness": round(pet.fullness, 1),
            "energy": round(pet.energy, 1),
            "max_energy": max_energy(pet, config),
            "fitness": {
                "level": fitness_level(pet, config),
                "hours": round(pet.fitness, 2),
            },
            "items": pet.items,
            "wearing": shop.wearing(pet.items),
            "subscriptions": pet.subscriptions,
            "swipes": pet.swipes,
            "swag": shop.swag(pet.items),
            "money": round(pet.money, 2),
            "invested": round(pet.invested, 2),
            "activity": pet.activity.value,
            "building": pet.building,
            "career": pet.career,
            "path": pet.path,
            # Only the pet's own career is ever sent, so players can't browse the other trees.
            "career_tree": career_tree(careers[pet.career]) if pet.career in careers else None,
            "upgrades": upgrade_status(pet),
            "gpa": round(pet.gpa, 2),
            "event": None if frozen else event_book.describe(pet.event, now, perks, config),
            "rest_multiplier": round(pet.rest_multiplier, 3),
            "sleep_hours_today": round(pet.sleep_hours, 2),
            "work_speed": round(work_speed(pet, now, config, perks), 3),
            "penalized": penalty_strength(pet, now, config) > 0,
            "penalty_start": pet.penalty_start,
            "penalty_end": pet.penalty_end,
            "coffee_active": coffee_active(pet, now),
            "is_night": campus_time.is_night(now, config),
            "experiments": sorted(pet.experiments),
            "perks": perk_view(pet, perks, now),
            "internship": job and {
                "company": job["company"],
                "title": job["title"],
                "pay_per_week": job_pay(job, perks) * WEEK_HOURS,
                "until": job["until"],
                "kept": job["kept"],
                "lost_on_exhaustion": job["lost_on_exhaustion"],
            },
            "income_per_hour": income(pet, now, config, perks),
            # Only while the pet is still walking; the client resumes the hop from here.
            "trip": pet.trip if in_transit(pet, now) and not frozen else None,
            "now": now,
        }

    def perk_view(pet: Pet, perks: Perks, now: float) -> dict:
        """What the pet's perks change, in the numbers the client shows."""
        task_pay = config.task_pay_per_hour * (1 + perks.task_pay)
        market = None
        if perks.market:
            market = {
                **asdict(perks.market),
                "max_invest": round(actions.max_investment(pet, config), 2),
                "last_move": market_move(pet.day_started_at, perks.market, config),
            }
        return {
            "meal_price": meal_price(perks, config, pet, now),
            "task_pay_per_hour": task_pay,
            "task_sites": {
                b: {
                    "name": site.name,
                    "pay_per_hour": config.task_pay_per_hour * (1 + perks.task_pay + site.pay),
                    "xp_per_hour": config.xp_per_hour * site.xp,
                    "path": site.path,
                }
                for b, site in perks.task_sites.items()
            },
            "sleep_pay_per_hour": config.task_pay_per_hour * perks.sleep_pay,
            "idle_pay_per_hour": config.task_pay_per_hour * perks.idle_pay,
            "work_energy": perks.work_energy,
            "training_xp": perks.training_xp,
            "home_meals": perks.home_meals,
            "tired_training": perks.tired_training,
            "delivered_meals": perks.delivered_meals,
            "burnout_immunity": perks.burnout_immunity,
            "billed_hours": perks.billed_hours,
            "experiments": {
                "slots": perks.experiments,
                "bonus": perks.experiment_bonus,
                "hours": config.experiment_hours,
                "buildings": perks.experiment_buildings,
            } if perks.experiments else None,
            "market": market,
        }

    def career_tree(career: Career) -> dict:
        return {
            "id": career.id,
            "name": career.name,
            "summary": career.summary,
            "paths": {
                p.id: {
                    "name": p.name,
                    "focus": p.focus,
                    "buildings": p.buildings,
                    "tiers": [
                        {"name": t.name, "cost": t.cost, "perks": t.perks, "later": t.later,
                         "flavor": t.flavor}
                        for t in p.tiers
                    ],
                }
                for p in career.paths.values()
            },
        }

    def upgrade_status(pet: Pet) -> dict:
        """Path id -> progress, for the pet's own career."""
        career = careers.get(pet.career)
        if career is None:
            return {}
        build = pet.upgrades.get(career.id, {})
        status = {}
        for path in career.paths.values():
            tier = build.get(path.id, 0)
            xp = pet.xp.get(career.id, {}).get(path.id, 0.0)
            nxt = path.tiers[tier] if tier < len(path.tiers) else None
            blocker = upgrade_blocker(career, build, path.id, config)
            status[path.id] = {
                "tier": tier,
                "xp": round(xp, 1),
                "next": nxt and {"name": nxt.name, "cost": nxt.cost},
                "blocked": blocker,
                "can_buy": blocker is None and nxt is not None and xp >= nxt.cost,
            }
        return status

    def running(pet: Pet, now: float) -> None:
        if semesters.frozen(pet.semester, now, config):
            raise HTTPException(409, f"semester {pet.semester} is over")

    def act(pet_id: int, change: Callable[[Pet, float], Pet]) -> dict:
        now = tick()

        def changed(p: Pet) -> Pet:
            running(p, now)
            return change(p, now)

        try:
            pet = store.update(pet_id, changed)
        except actions.ActionError as e:
            raise HTTPException(400, str(e)) from e
        if pet is None:
            raise HTTPException(404, "pet not found")
        return view(pet, now)

    def fair_action(pet_id: int, change: Callable[[Tx, Pet, float], object] | None) -> tuple[object, dict]:
        """Run a fair action for a pet brought up to now, returning its result and the fair view. With no
        `change`, only looks, which a pet whose semester is over can still do (and sees no fair)."""
        now = tick()
        try:
            with store.transaction() as tx:
                pet = tx.pet(pet_id)
                if pet is None:
                    raise HTTPException(404, "pet not found")
                if semesters.frozen(pet.semester, now, config):
                    if change is None:
                        return None, {"fair": None}
                    running(pet, now)
                pet, _ = advance(pet, now, config)
                result = change(tx, pet, now) if change else None
                return result, fairs.fair_view(tx, pet, now, config)
        except fairs.NotFound as e:
            raise HTTPException(404, str(e)) from e
        except fairs.FairError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html", headers=_REVALIDATE)

    @app.get("/walk")
    def walk_page():
        return FileResponse(_STATIC / "walk" / "index.html", headers=_REVALIDATE)

    @app.get("/stacker")
    def stacker_page():
        return FileResponse(_STATIC / "stacker" / "index.html", headers=_REVALIDATE)

    def minigame(game_id: str) -> Game:
        if game_id not in GAMES:
            raise HTTPException(404, "no such minigame")
        return GAMES[game_id]

    def practice_seconds(game: Game, seconds: int | None) -> int:
        seconds = seconds or game.config.seconds
        if seconds not in _PRACTICE_SECONDS:
            raise HTTPException(400, "seconds must be between 10 and 120")
        return seconds

    @app.post("/api/minigames/{game_id}/practice")
    def practice(game_id: str, body: PracticeRequest):
        game = minigame(game_id)
        course = game.generate(new_seed(), practice_seconds(game, body.seconds), game.config)
        return {"course": course.to_dict(), "config": game.config.to_dict()}

    @app.post("/api/minigames/{game_id}/replay")
    def practice_replay(game_id: str, body: ReplayRequest):
        game = minigame(game_id)
        if not 0 <= body.seed <= MASK32:
            raise HTTPException(400, "seed must be a 32-bit unsigned integer")
        course = game.generate(body.seed, practice_seconds(game, body.seconds), game.config)
        try:
            result = game.replay(course, body.inputs, game.config)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return asdict(result)

    @app.get("/api/config")
    def get_config():
        campus = campus_map.load()
        return {
            "buildings": {
                building_id: {
                    "name": b.map_name,
                    "x": campus.anchors[b.map_name][0],
                    "y": campus.anchors[b.map_name][1],
                    "shape": campus.shapes[b.map_name],
                }
                for building_id, b in config.buildings.items()
            },
            "houses": {
                h.id: {
                    "name": h.name,
                    "building": h.map_name,
                    "blurb": h.blurb,
                    "x": campus.anchors[h.map_name][0],
                    "y": campus.anchors[h.map_name][1],
                    "shape": campus.shapes[h.map_name],
                }
                for h in houses().values()
            },
            "default_house": config.default_house,
            "campus_poll_seconds": config.campus_poll_seconds,
            "clock": {
                "timezone": config.timezone.key,
                "night_start": config.night_start.strftime("%H:%M"),
                "night_end": config.night_end.strftime("%H:%M"),
            },
            "xp_per_hour": config.xp_per_hour,
            "task_pay_per_hour": config.task_pay_per_hour,
            "min_energy_to_work": config.min_energy_to_work,
            "map": {
                "image": "/static/map/campus.png",
                "breeze": {"images": ["/static/map/breeze1.png", "/static/map/breeze2.png"],
                           "trees": "/static/map/breeze.json"},
                "traffic": {"data": "/static/map/traffic.json", "cars": "/static/map/cars.png",
                            "occluders": "/static/map/occluders.png"},
                "lights": {"lamps": "/static/map/lights.json", "windows": "/static/map/windows.bin"},
                "width": campus.width,
                "height": campus.height,
                "attribution": campus.attribution,
            },
            "meal_price": config.meal_price,
            "doordash": {"price": config.doordash_price, "fullness": config.doordash_fullness},
            "coffee_price": config.coffee_price,
            "coffee_work_boost": config.coffee_work_boost,
            "gym": {
                "building": config.gym,
                "hours_per_level": config.fitness_hours_per_level,
                "energy_per_level": config.fitness_energy_per_level,
                "max_levels": config.fitness_max_levels,
            },
            "shop": {
                "categories": shop.catalog().categories,
                "items": [asdict(item) for item in shop.catalog().items.values()],
                "subscription_days": config.subscription_days,
            },
        }

    @app.get("/api/weather")
    def get_weather():
        """Philadelphia right now, for the sky: clouds, rain or snow, and today's sunrise and sunset.
        Cached, so the campus shares one forecast."""
        return sky_weather.report()

    @app.get("/api/careers")
    def list_careers():
        """What a new player picks from: each career's pitch, but not its upgrade tiers."""
        return [
            {
                "id": c.id,
                "name": c.name,
                "summary": c.summary,
                "paths": [{"name": p.name, "focus": p.focus} for p in c.paths.values()],
            }
            for c in careers.values()
        ]

    @app.post("/api/pets")
    def create_pet(body: NewPet, request: Request):
        """Hatch the signed-in player's pet for this semester: one each, and none during the break."""
        user = request.state.user
        if user is None:
            raise HTTPException(401, "sign in first")
        name = _pet_name(body.name)
        if body.career not in careers:
            raise HTTPException(400, f"unknown career {body.career}")
        if body.house is not None and body.house not in houses():
            raise HTTPException(400, f"unknown house {body.house}")
        now = tick()
        term = semesters.semester_at(now, config)
        if term.over(now):
            raise HTTPException(409, f"semester {term.number} is over; hatch a pet when the next one starts")
        if store.pet_of(user.id, term.number) is not None:
            raise HTTPException(409, "you already have a pet this semester")
        try:
            pet = store.create(new_pet(name, now, config, body.career, body.house), owner=user.id)
        except sqlite3.IntegrityError as e:     # a second hatch racing the first
            raise HTTPException(409, "you already have a pet this semester") from e
        return view(pet, now)

    def everyone(now: float, number: int | None = None) -> list[Pet]:
        """Every pet of semester `number` (the one `now` falls in by default), brought up to `now` or frozen at
        its end, without saving."""
        number = semesters.number_at(now, config) if number is None else number
        return [bring(p, now)[0] for p in store.read(lambda tx: tx.pets(number))]

    @app.get("/api/campus")
    def get_campus():
        """What anyone can see of every pet on the map: where it is, where it's walking, what it's doing
        there, and what it's wearing. Stats, money, and upgrades stay private."""
        now = tick()
        term = semesters.semester_at(now, config)
        over = term.over(now)
        return {
            "now": now,
            "semester": semester_view(term),
            "pets": [
                {
                    "id": p.id,
                    "name": p.name,
                    "house": p.house,
                    "career": careers[p.career].name if p.career in careers else None,
                    "activity": p.activity.value,
                    "building": p.building,
                    "trip": p.trip if in_transit(p, now) and not over else None,
                    "tired": penalty_strength(p, now, config) > 0 or p.fullness <= 0
                    or p.energy < config.min_energy_to_work,
                    "wearing": shop.wearing(p.items),
                }
                for p in everyone(now)
            ],
        }

    @app.get("/api/leaderboard")
    def get_leaderboard(semester: int | None = None):
        """A semester's pets (this one's by default) ranked by net worth (cash, investments, and what they paid
        for the things they own) plus swag, with the internship offers they won, and houses by their members'
        average, so a small house can beat a big one. Once the semester is over, this is its final ranking."""
        now = tick()
        current = semesters.number_at(now, config)
        if semester is not None and (semester < 1 or semester > current):
            raise HTTPException(404, "that semester hasn't happened yet")
        term = semesters.semester(current if semester is None else semester, config)
        members = everyone(now, term.number)
        won = store.read(lambda tx: tx.wins([p.id for p in members]))
        roles = {rid: role for c in careers.values() for rid, role in c.roles.items()}

        def row(p: Pet) -> dict:
            worth = p.money + p.invested + shop.value(p.items)
            offers = [roles[r] for r in won.get(p.id, []) if r in roles]
            return {
                "id": p.id,
                "name": p.name,
                "house": p.house,
                "career": careers[p.career].name if p.career in careers else None,
                "net_worth": round(worth, 2),
                "swag": shop.swag(p.items),
                "score": round(worth + shop.swag(p.items), 2),
                "interning": interning(p, min(now, term.ends_at)),
                # Best paying first.
                "offers": [{"company": r.company, "title": r.title}
                           for r in sorted(offers, key=lambda r: -r.pay)],
            }

        pets = sorted((row(p) for p in members), key=lambda r: -r["score"])
        board = []
        for house in houses().values():
            members = [row for row in pets if row["house"] == house.id]
            board.append({
                "id": house.id,
                "name": house.name,
                "members": len(members),
                "net_worth": _mean(members, "net_worth"),
                "swag": _mean(members, "swag"),
                "score": _mean(members, "score"),
                "interning": sum(r["interning"] for r in members),
            })
        board.sort(key=lambda row: (row["members"] == 0, -row["score"]))
        return {"semester": semester_view(term), "houses": board, "pets": pets}

    @app.post("/api/pets/{pet_id}/career")
    def declare_career(pet_id: int, body: CareerRequest):
        return act(pet_id, lambda p, now: actions.declare_career(p, now, body.career, config))

    @app.get("/api/pets/{pet_id}")
    def get_pet(pet_id: int):
        now = tick()
        pet = store.get(pet_id)
        if pet is None:
            raise HTTPException(404, "pet not found")
        return view(pet, now)

    @app.post("/api/pets/{pet_id}/feed")
    def feed(pet_id: int):
        return act(pet_id, lambda p, now: actions.feed(p, now, config))

    @app.post("/api/pets/{pet_id}/sleep")
    def sleep(pet_id: int):
        return act(pet_id, lambda p, now: actions.sleep(p, now, config))

    @app.post("/api/pets/{pet_id}/wake")
    def wake(pet_id: int):
        return act(pet_id, lambda p, now: actions.wake(p, now, config))

    @app.post("/api/pets/{pet_id}/train")
    def train(pet_id: int, body: WorkRequest):
        if body.path is None:
            raise HTTPException(400, "path is required")
        return act(pet_id, lambda p, now: actions.train(p, now, body.building, body.path, config))

    @app.post("/api/pets/{pet_id}/upgrade")
    def upgrade(pet_id: int, body: UpgradeRequest):
        return act(pet_id, lambda p, now: actions.buy_tier(p, now, body.path, config))

    @app.post("/api/pets/{pet_id}/task")
    def task(pet_id: int, body: WorkRequest):
        return act(pet_id, lambda p, now: actions.task(p, now, body.building, config))

    @app.post("/api/pets/{pet_id}/workout")
    def workout(pet_id: int):
        return act(pet_id, lambda p, now: actions.work_out(p, now, config))

    @app.post("/api/pets/{pet_id}/buy")
    def buy(pet_id: int, body: ItemRequest):
        return act(pet_id, lambda p, now: actions.buy(p, now, body.item, config))

    @app.post("/api/pets/{pet_id}/cancel")
    def cancel(pet_id: int, body: ItemRequest):
        return act(pet_id, lambda p, now: actions.cancel(p, now, body.item, config))

    @app.post("/api/pets/{pet_id}/recall")
    def recall(pet_id: int):
        return act(pet_id, lambda p, now: actions.recall(p, now, config))

    @app.post("/api/pets/{pet_id}/coffee")
    def coffee(pet_id: int):
        return act(pet_id, lambda p, now: actions.drink_coffee(p, now, config))

    @app.post("/api/pets/{pet_id}/event")
    def answer_event(pet_id: int, body: EventAnswer):
        """Take one of the options of the pet's waiting event. Returns the pet, plus what the choice did."""
        landed = {}

        def answer(p: Pet, now: float) -> Pet:
            p, effects = actions.answer_event(p, now, body.option, config)
            landed.update(effects)
            return p

        return {**act(pet_id, answer), "landed": landed}

    @app.post("/api/pets/{pet_id}/invest")
    def invest(pet_id: int, body: MoneyRequest):
        return act(pet_id, lambda p, now: actions.invest(p, now, body.amount, config))

    @app.post("/api/pets/{pet_id}/withdraw")
    def withdraw(pet_id: int, body: MoneyRequest):
        return act(pet_id, lambda p, now: actions.withdraw(p, now, body.amount, config))

    @app.get("/api/pets/{pet_id}/fair")
    def get_fair(pet_id: int):
        return fair_action(pet_id, None)[1]

    @app.post("/api/pets/{pet_id}/fair/apply")
    def fair_apply(pet_id: int, body: ApplyRequest):
        return fair_action(
            pet_id, lambda tx, p, now: fairs.apply(tx, p, body.role, now, config, body.instead_of))[1]

    @app.post("/api/pets/{pet_id}/fair/withdraw")
    def fair_withdraw(pet_id: int, body: WithdrawRequest):
        return fair_action(pet_id, lambda tx, p, now: fairs.withdraw(tx, p, body.role, now))[1]

    @app.post("/api/pets/{pet_id}/fair/back")
    def fair_back(pet_id: int, body: BackRequest):
        return fair_action(pet_id, lambda tx, p, now: fairs.back(tx, p, body.pet, now, config))[1]

    @app.post("/api/pets/{pet_id}/fair/short")
    def fair_short(pet_id: int, body: ShortRequest):
        return fair_action(pet_id, lambda tx, p, now: fairs.short(tx, p, body.pet, body.amount, now, config))[1]

    @app.post("/api/pets/{pet_id}/fair/runs")
    def fair_start_run(pet_id: int, body: StartRunRequest | None = None):
        expected = body.game if body else None
        (attempt, game, course, run_config), fair = fair_action(
            pet_id, lambda tx, p, now: fairs.start_run(tx, p, now, config, expected))
        return {"run": attempt.id, "game": game.id, "multiplier": attempt.multiplier, "course": course.to_dict(),
                "config": run_config.to_dict(), "fair": fair}

    @app.post("/api/pets/{pet_id}/fair/runs/{run_id}")
    def fair_submit_run(pet_id: int, run_id: int, body: RunRequest):
        attempt, fair = fair_action(
            pet_id, lambda tx, p, now: fairs.submit_run(tx, p, run_id, body.inputs, now, config))
        return {"run": asdict(attempt), "fair": fair}

    return app
