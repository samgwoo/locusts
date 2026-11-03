"""Job fairs: weekly, asynchronous competitions for internships.

A fair opens at night_end on config.fair_weekday and stays open for fair_hours. While it's open, each pet
applies to a few of its career's posted roles in order of preference, then plays scored minigame runs on
server-generated seeds. Fairs take turns through the minigames in minigames.games, one game per fair.
Starting the first scored run locks its applications. Results come out at
`results_at`, a few minutes after closing. Each role goes to the best-scoring applicant who qualifies and
didn't win a role they ranked higher (deferred acceptance with pets proposing), and winning gives an
internship that pays until the next fair's results.

There's no scheduler. `settle` runs before every request: it creates fairs as their weeks come up and
resolves any whose results are due, before any pet can be advanced past that moment.
"""

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from locusts.config import DEFAULT_CONFIG, Config
from locusts.db import Attempt, Entry, Fair, PetStore, Tx
from locusts.minigames.games import GAMES, Game, next_game
from locusts.minigames.rng import new_seed
from locusts.perks import Perks
from locusts.pet import WEEK_HOURS, Pet, advance, exit_bonus, job_pay, perks_of, training_streak
from locusts.semesters import semester_at
from locusts.upgrades import Career, Role, careers_for


class FairError(Exception):
    pass


class NotFound(FairError):
    pass


@dataclass(frozen=True)
class Window:
    opens_at: float
    closes_at: float
    results_at: float


@dataclass(frozen=True)
class RunRules:
    """What a pet's scored runs look like right now."""

    seconds: int
    max_hits: int
    # Labelled score bonuses, as fractions.
    boosts: tuple[tuple[str, float], ...]

    @property
    def multiplier(self) -> float:
        return 1 + sum(value for _, value in self.boosts)


# --- Schedule ---


def window_opening(opens_at: float, config: Config = DEFAULT_CONFIG) -> Window:
    closes_at = opens_at + config.fair_hours * 3600
    return Window(opens_at, closes_at, closes_at + config.fair_grace_minutes * 60)


def first_window(t: float, config: Config = DEFAULT_CONFIG) -> Window:
    """The fair whose results are still to come at `t`: the one open now, or the next."""
    day = datetime.fromtimestamp(t, config.timezone).date()
    back = (day.weekday() - config.fair_weekday) % 7
    window = window_opening(_opening_on(day - timedelta(days=back), config), config)
    return window if window.results_at > t and in_semester(window, config) else next_window(window, config)


def next_window(window: Window, config: Config = DEFAULT_CONFIG) -> Window:
    """The fair after `window`, skipping weeks without one."""
    day = datetime.fromtimestamp(window.opens_at, config.timezone).date()
    while True:
        day += timedelta(days=7)
        window = window_opening(_opening_on(day, config), config)
        if in_semester(window, config):
            return window


def in_semester(window: Window, config: Config = DEFAULT_CONFIG) -> bool:
    """Whether a fair at `window` opens and has its results out within one semester's play."""
    term = semester_at(window.opens_at, config)
    return term.starts_at <= window.opens_at and window.results_at <= term.ends_at


def _opening_on(day, config: Config) -> float:
    return datetime.combine(day, config.night_end, tzinfo=config.timezone).timestamp()


def posted_roles(opens_at: float, config: Config = DEFAULT_CONFIG) -> list[str]:
    """Role ids a fair posts: per career, one open role and a few more regular ones picked by a seeded hash,
    plus every exclusive role."""
    def draw(role: Role) -> bytes:
        return hashlib.sha256(f"{config.fair_seed}:{int(opens_at)}:{role.id}".encode()).digest()

    posted = []
    for career in careers_for(config).values():
        regular = sorted((r for r in career.roles.values() if r.regular), key=draw)
        first = next((r for r in regular if not r.requires), None)
        picks = ([first] if first else []) + [r for r in regular if r is not first]
        posted += [r.id for r in picks[: config.fair_roles_per_career]]
        posted += [r.id for r in career.roles.values() if not r.regular and not r.reserved]
    return posted


def settle(store: PetStore, now: float, config: Config = DEFAULT_CONFIG) -> None:
    """Create fairs up to the one whose results are next, resolving every fair whose results are due."""
    latest = store.read(lambda tx: tx.latest_fair())
    if latest is not None and latest.results_at > now:
        return
    with store.transaction() as tx:
        latest = tx.latest_fair()
        if latest is None:
            window = first_window(now, config)
            tx.add_fair(window.opens_at, window.closes_at, window.results_at, posted_roles(window.opens_at, config),
                        next_game(None))
            return
        while latest.results_at <= now:
            if not latest.resolved:
                resolve(tx, latest, config)
            window = next_window(window_opening(latest.opens_at, config), config)
            latest = tx.add_fair(window.opens_at, window.closes_at, window.results_at,
                                 posted_roles(window.opens_at, config), next_game(latest.game))


# --- Qualifying ---


def build_of(pet: Pet) -> dict[str, int]:
    return pet.upgrades.get(pet.career, {}) if pet.career else {}


def shortfall(role: Role, build: dict[str, int]) -> int:
    """Tiers the pet is missing for the role, summed over its required paths."""
    return sum(max(0, need - build.get(path, 0)) for path, need in role.requires.items())


def can_see(role: Role, perks: Perks) -> bool:
    return not role.reserved and (role.unlock is None or role.unlock in perks.unlocks)


def wins_outright(role: Role, perks: Perks) -> bool:
    return role.unlock is not None and role.unlock in perks.wins_outright


def application_limit(perks: Perks, entry: Entry, others: list[Entry], config: Config = DEFAULT_CONFIG) -> int:
    """How many roles the pet may apply to at this fair. Extra applications last for a set number of fairs,
    counted by the other fairs where the pet used them."""
    base = config.fair_applications
    used = sum(len(e.roles) > base for e in others if e.fair_id != entry.fair_id)
    if perks.extra_applications and (used < perks.extra_application_fairs or len(entry.roles) > base):
        return base + perks.extra_applications
    return base


def run_rules(pet: Pet, perks: Perks, t: float, rejected: bool, config: Config = DEFAULT_CONFIG,
              game: Game = GAMES["locust-walk"], extra: tuple[tuple[str, float], ...] = ()) -> RunRules:
    """Career tiers add hits; perks add time and score bonuses. `rejected` means the pet applied at the
    previous fair and won nothing, and `extra` are boosts from other pets (see amicus_boosts)."""
    tiers = sum(build_of(pet).values())
    boosts = list(extra)
    streak = min(training_streak(pet, t, config), config.streak_max_days)
    if perks.streak_score and streak:
        boosts.append((f"{streak}-day training streak", perks.streak_score * streak))
    if perks.rejection_score and rejected:
        boosts.append(("Rejected last fair", perks.rejection_score))
    return RunRules(
        seconds=round(game.config.seconds * (1 + perks.fair_time)),
        max_hits=game.config.max_hits + tiers // config.tiers_per_extra_hit,
        boosts=tuple(boosts),
    )


def amicus_boosts(tx: Tx, fair: Fair, pet_id: int, config: Config = DEFAULT_CONFIG) -> tuple[tuple[str, float], ...]:
    """Score boosts from Amicus Brief at `fair`: the pet's own for backing someone, and one from each pet
    backing it."""
    boosts = []
    for e in tx.entries(fair.id):
        if e.backing is None:
            continue
        if e.pet_id == pet_id:
            backer, label = tx.pet(pet_id), f"Backing {tx.pet(e.backing).name}"
            boosts.append((label, perks_of(backer, config).amicus_score))
        elif e.backing == pet_id:
            backer = tx.pet(e.pet_id)
            boosts.append((f"Backed by {backer.name}", perks_of(backer, config).amicus_backed_score))
    return tuple(boosts)


def rejected_last_fair(tx: Tx, fair: Fair, pet_id: int) -> bool:
    before = tx.previous_fair(fair)
    entry = before and tx.entry(before.id, pet_id)
    return bool(before and before.resolved and entry and entry.roles and entry.won is None)


# --- Applying and playing ---


def apply(tx: Tx, pet: Pet, role_id: str, now: float, config: Config = DEFAULT_CONFIG,
          instead_of: str | None = None) -> None:
    """Apply to a posted role, or swap `instead_of` for it. After applications lock, only perks that allow
    late changes can swap."""
    fair = _open_fair(tx, now)
    career = _career(pet, config)
    perks = perks_of(pet, config)
    role = career.roles.get(role_id)
    if role is None or role_id not in fair.roles or not can_see(role, perks):
        raise FairError("that role isn't posted at this fair")
    entry = tx.entry(fair.id, pet.id) or Entry(fair.id, pet.id)
    problem = apply_problem(entry, role, pet, perks, tx.pet_entries(pet.id), config, instead_of)
    if problem:
        raise FairError(problem)
    if entry.locked_at is not None:
        entry.late_changes += 1
    if instead_of is not None:
        entry.roles[entry.roles.index(instead_of)] = role_id
        if entry.referral == instead_of:
            entry.referral = None
    else:
        entry.roles.append(role_id)
    if shortfall(role, build_of(pet)) and not committee_letter(pet, perks):
        entry.referral = role_id
    tx.save_entry(entry)


def committee_letter(pet: Pet, perks: Perks) -> bool:
    """Whether the pet's GPA covers any role it's one tier short of, without using a referral."""
    return perks.referral_gpa > 0 and pet.gpa >= perks.referral_gpa


def apply_problem(entry: Entry, role: Role, pet: Pet, perks: Perks, others: list[Entry],
                  config: Config = DEFAULT_CONFIG, instead_of: str | None = None) -> str | None:
    """Why the pet can't apply to `role` (swapping out `instead_of`), or None if it can."""
    if role.id in entry.roles:
        return "already applied"
    if instead_of is not None and instead_of not in entry.roles:
        return "you haven't applied to that role"
    if entry.locked_at is not None:
        if not perks.late_changes:
            return "applications locked when your first scored run started"
        if entry.late_changes >= perks.late_changes or instead_of is None:
            return "you can only swap one application after they lock" if perks.late_changes == 1 \
                else f"you can only swap {perks.late_changes} applications after they lock"
    if role.gpa is not None and pet.gpa < role.gpa:
        return f"needs a {role.gpa:.1f} GPA"
    short = shortfall(role, build_of(pet))
    if short > 1:
        return f"needs {_requirements(role, pet, config)}"
    if short == 1 and not committee_letter(pet, perks):
        if not perks.referrals:
            return f"needs {_requirements(role, pet, config)}"
        if entry.referral is not None and entry.referral != instead_of:
            return "your referral is already used at this fair"
    limit = application_limit(perks, entry, others, config)
    if instead_of is None and len(entry.roles) >= limit:
        return f"you can apply to {limit} roles at this fair"
    return None


def withdraw(tx: Tx, pet: Pet, role_id: str, now: float) -> None:
    fair = _open_fair(tx, now)
    entry = tx.entry(fair.id, pet.id)
    if entry is None or role_id not in entry.roles:
        raise FairError("you haven't applied to that role")
    if entry.locked_at is not None:
        raise FairError("applications locked when your first scored run started")
    entry.roles.remove(role_id)
    if entry.referral == role_id:
        entry.referral = None
    tx.save_entry(entry)


def _rival(tx: Tx, pet: Pet, target_id: int) -> Pet:
    """Another pet of the same semester, for a move on it."""
    target = tx.pet(target_id)
    if target is None or target.id == pet.id or target.semester != pet.semester:
        raise FairError("pick another pet from this semester")
    return target


def back(tx: Tx, pet: Pet, target_id: int, now: float, config: Config = DEFAULT_CONFIG) -> None:
    """Amicus Brief: back another pet at this fair, once, before the pet's own first scored run."""
    fair = _open_fair(tx, now)
    if not perks_of(pet, config).amicus_score:
        raise FairError(f"{pet.name} can't back other pets")
    entry = tx.entry(fair.id, pet.id) or Entry(fair.id, pet.id)
    if entry.backing is not None:
        raise FairError("you already backed a pet at this fair")
    if tx.attempts(fair.id, pet.id):
        raise FairError("back a pet before your first scored run")
    entry.backing = _rival(tx, pet, target_id).id
    tx.save_entry(entry)


def short(tx: Tx, pet: Pet, target_id: int, stake: float, now: float, config: Config = DEFAULT_CONFIG) -> None:
    """Short Seller: bet `stake`, up to half the pet's money, that another pet wins no role at this fair."""
    fair = _open_fair(tx, now)
    if not perks_of(pet, config).short_seller:
        raise FairError(f"{pet.name} can't short other pets")
    entry = tx.entry(fair.id, pet.id) or Entry(fair.id, pet.id)
    if entry.short_on is not None:
        raise FairError("you already placed a bet at this fair")
    target = _rival(tx, pet, target_id)
    if stake <= 0:
        raise FairError("bet a positive amount")
    if stake > max(0.0, pet.money / 2) + 0.005:
        raise FairError(f"you can bet at most ${max(0.0, pet.money / 2):.2f}")
    stake = min(stake, pet.money / 2)
    pet.money -= stake
    tx.save_pet(pet)
    entry.short_on, entry.short_stake = target.id, stake
    tx.save_entry(entry)


def start_run(tx: Tx, pet: Pet, now: float, config: Config = DEFAULT_CONFIG,
              expected_game: str | None = None) -> tuple[Attempt, Game, object, object]:
    """Start a scored run: a fresh seed, the pet's current rules, and its applications lock. Returns the
    attempt, the fair's game, and the course and game config the client plays."""
    fair = _open_fair(tx, now)
    game = GAMES[fair.game]
    if expected_game is not None and expected_game != game.id:
        raise FairError(f"this fair plays {game.name}")
    _career(pet, config)
    perks = perks_of(pet, config)
    entry = tx.entry(fair.id, pet.id)
    if entry is None or not entry.roles:
        raise FairError("apply to a role before playing")
    allowed = 1 + perks.fair_retries
    if len(tx.attempts(fair.id, pet.id)) >= allowed:
        raise FairError("you've used your scored run at this fair" if allowed == 1
                        else f"you've used all {allowed} scored runs at this fair")
    rules = run_rules(pet, perks, now, rejected_last_fair(tx, fair, pet.id), config, game,
                      amicus_boosts(tx, fair, pet.id, config))
    attempt = tx.add_attempt(fair.id, pet.id, new_seed(), rules.seconds, rules.max_hits, rules.multiplier, now)
    if entry.locked_at is None:
        entry.locked_at = now
        tx.save_entry(entry)
    run_config = replace(game.config, max_hits=attempt.max_hits)
    return attempt, game, game.generate(attempt.seed, attempt.seconds, run_config), run_config


def submit_run(tx: Tx, pet: Pet, attempt_id: int, inputs, now: float, config: Config = DEFAULT_CONFIG) -> Attempt:
    """Score a scored run by replaying its logged inputs on the server."""
    attempt = tx.attempt(attempt_id)
    if attempt is None or attempt.pet_id != pet.id:
        raise NotFound("run not found")
    if attempt.submitted_at is not None:
        raise FairError("that run was already submitted")
    fair = tx.fair(attempt.fair_id)
    deadline = min(fair.results_at, attempt.started_at + attempt.seconds + config.fair_submit_seconds)
    if fair.resolved or now > deadline:
        raise FairError("too late to submit that run")
    game = GAMES[fair.game]
    run_config = replace(game.config, max_hits=attempt.max_hits)
    course = game.generate(attempt.seed, attempt.seconds, run_config)
    try:
        result = game.replay(course, inputs, run_config)
    except ValueError as e:
        raise FairError(str(e)) from e
    attempt.submitted_at = now
    attempt.raw_score = result.score
    attempt.score = round(result.score * attempt.multiplier, 1)
    attempt.hits, attempt.stats = result.hits, game.stats(result)
    tx.save_attempt(attempt)
    return attempt


def _open_fair(tx: Tx, now: float) -> Fair:
    fair = tx.latest_fair()
    if fair is None or not fair.opens_at <= now < fair.closes_at:
        raise FairError("no job fair is open right now")
    return fair


def _career(pet: Pet, config: Config) -> Career:
    career = careers_for(config).get(pet.career)
    if career is None:
        raise FairError(f"{pet.name} needs to pick a career first")
    return career


def _requirements(role: Role, pet: Pet, config: Config) -> str:
    paths = careers_for(config)[pet.career].paths
    return " and ".join(f"{paths[p].name} tier {n}" for p, n in role.requires.items())


# --- Results ---


def best_runs(attempts: list[Attempt]) -> dict[int, Attempt]:
    """Each pet's best submitted run; an earlier run wins a tie."""
    best: dict[int, Attempt] = {}
    for a in attempts:
        if a.score is not None and (a.pet_id not in best or a.score > best[a.pet_id].score):
            best[a.pet_id] = a
    return best


def raw_ranks(best: dict[int, Attempt]) -> dict[int, int]:
    """Each pet's place by raw score among everyone who played, with ties sharing a place."""
    scores = [a.raw_score for a in best.values()]
    return {pid: 1 + sum(s > a.raw_score for s in scores) for pid, a in best.items()}


def match(prefs: dict[int, list[str]], ranking: Callable[[int, str], tuple]) -> dict[int, str]:
    """Deferred acceptance with pets proposing. `prefs` lists the roles each pet may win, best first, and
    `ranking(pet, role)` orders applicants for a role. Each role takes one pet, and the result is stable:
    no pet and role would both rather have each other."""
    next_choice = dict.fromkeys(prefs, 0)
    holder: dict[str, int] = {}
    free = sorted(prefs, reverse=True)
    while free:
        pid = free.pop()
        while next_choice[pid] < len(prefs[pid]):
            rid = prefs[pid][next_choice[pid]]
            next_choice[pid] += 1
            current = holder.get(rid)
            if current is None or ranking(pid, rid) > ranking(current, rid):
                holder[rid] = pid
                if current is not None:
                    free.append(current)
                break
    return {pid: rid for rid, pid in holder.items()}


def resolve(tx: Tx, fair: Fair, config: Config = DEFAULT_CONFIG) -> dict[int, str]:
    """Hand out the fair's roles and start the winners' internships. Returns pet id -> role id."""
    careers = careers_for(config)
    roles = {rid: role for c in careers.values() for rid, role in c.roles.items()}
    term = semester_at(fair.opens_at, config)
    # An internship lasts until the next fair's results, or the end of the semester if that's sooner.
    until = min(next_window(window_opening(fair.opens_at, config), config).results_at, term.ends_at)
    # Pay up to the results is settled under the old terms before anything changes.
    pets = {p.id: advance(p, fair.results_at, config)[0] if p.updated_at < fair.results_at else p
            for p in tx.pets() if p.semester in (None, term.number)}
    perks = {pid: perks_of(p, config) for pid, p in pets.items()}
    best = best_runs(tx.attempts(fair.id))
    ranks = raw_ranks(best)
    entries = {e.pet_id: e for e in tx.entries(fair.id) if e.pet_id in pets}
    wreckers = [pid for pid, e in entries.items() if perks[pid].curve_wrecker and e.roles]

    def cut(pid: int, rid: str) -> bool:
        """Curve Wrecker: a pet with a higher GPA applied to the same role and cuts everyone below it."""
        return any(w != pid and rid in entries[w].roles and pets[pid].gpa < pets[w].gpa for w in wreckers)

    def eligible(pid: int, rid: str) -> bool:
        role = roles.get(rid)
        if role is None or rid not in fair.roles or cut(pid, rid):
            return False
        if wins_outright(role, perks[pid]):
            return True
        return pid in best and (role.top is None or ranks[pid] <= role.top + perks[pid].top_cutoff)

    def ranking(pid: int, rid: str) -> tuple:
        run = best.get(pid)
        return (wins_outright(roles[rid], perks[pid]), run.score if run else -math.inf,
                -run.submitted_at if run else -math.inf, -pid)

    won = match({pid: [r for r in e.roles if eligible(pid, r)] for pid, e in entries.items()}, ranking)
    for pid, pet in pets.items():
        if pid in won or not perks[pid].reserved_roles or pet.career not in careers:
            continue
        held = [r for r in careers[pet.career].roles.values()
                if r.reserved and r.unlock in perks[pid].unlocks and not shortfall(r, build_of(pet))]
        if held:
            won[pid] = held[0].id
            entries.setdefault(pid, Entry(fair.id, pid))

    for pid, pet in pets.items():
        old = pet.internship
        ended_now = old is not None and old["until"] == fair.results_at
        renewed = ended_now and won.get(pid) == old["role"]
        if ended_now and not renewed:
            pet.money += exit_bonus(old, perks[pid])
        if pid in won:
            role = roles[won[pid]]
            pay = role.pay * config.internship_pay_per_week / WEEK_HOURS
            pet.internship = {
                "role": role.id, "company": role.company, "title": role.title, "pay": pay,
                "start": fair.results_at, "until": until, "kept": old["kept"] + 1 if renewed else 0,
                "lost_on_exhaustion": role.lost_on_exhaustion, "fair": fair.id,
            }
            pet.best_internship_pay = max(pet.best_internship_pay, pay)

    total = sum(job_pay(pets[pid].internship, perks[pid]) for pid in won)
    for pid, pet in pets.items():
        if perks[pid].partner_share and total:
            pet.bonuses.append({"name": "Equity Partner", "per_hour": perks[pid].partner_share * total,
                                "start": fair.results_at, "until": until})
    # Short bets: double back if the target applied and won nothing, the stake back if it never applied.
    for pid, entry in entries.items():
        if entry.short_on is None:
            continue
        target = entries.get(entry.short_on)
        applied = target is not None and bool(target.roles)
        entry.short_payout = 0.0 if entry.short_on in won else 2 * entry.short_stake if applied else entry.short_stake
        pets[pid].money += entry.short_payout
    for pet in pets.values():
        tx.save_pet(pet)
    for pid, entry in entries.items():
        entry.won = won.get(pid)
        tx.save_entry(entry)
    tx.mark_resolved(fair)
    return won


# --- What a player sees ---


def fair_view(tx: Tx, pet: Pet, now: float, config: Config = DEFAULT_CONFIG) -> dict:
    """The current or next fair from one pet's side. Who applied stays hidden unless a perk shows it."""
    fair = tx.latest_fair()
    career = careers_for(config).get(pet.career)
    if fair is None or career is None:
        return {"fair": None}
    perks = perks_of(pet, config)
    entry = tx.entry(fair.id, pet.id) or Entry(fair.id, pet.id)
    others = tx.pet_entries(pet.id)
    entries = tx.entries(fair.id)
    names = {p.id: p for p in tx.pets()}
    state = ("upcoming" if now < fair.opens_at else "open" if now < fair.closes_at else "closed")
    build = build_of(pet)
    path_names = {p.id: p.name for p in career.paths.values()}

    roles = []
    for rid in fair.roles:
        role = career.roles.get(rid)
        if role is None or not can_see(role, perks):
            continue
        applied = [e for e in entries if rid in e.roles]
        mine = rid in entry.roles
        problem = None if state == "open" else "the fair isn't open"
        problem = problem or apply_problem(entry, role, pet, perks, others, config)
        roles.append({
            "id": rid,
            "company": role.company,
            "title": role.title,
            "pay_per_week": role.pay * config.internship_pay_per_week * (1 + perks.internship_pay),
            "requires": [{"path": path_names[p], "tier": n, "have": build.get(p, 0)} for p, n in role.requires.items()],
            "gpa": None if role.gpa is None else {"needs": role.gpa, "have": round(pet.gpa, 2)},
            "top": role.top + perks.top_cutoff if role.top else None,
            "exclusive": not role.regular,
            "outright": wins_outright(role, perks),
            "referral": shortfall(role, build) == 1,
            "choice": entry.roles.index(rid) + 1 if mine else None,
            "problem": None if mine else problem,
            "applicants": len(applied) if perks.see_counts or perks.see_applicants else None,
            "names": [names[e.pet_id].name for e in applied] if perks.see_applicants else None,
            "rivals": [
                {"name": names[e.pet_id].name,
                 "tiers": {path_names[p]: n for p, n in build_of(names[e.pet_id]).items() if n and p in path_names}}
                for e in applied if e.pet_id != pet.id
            ] if perks.see_rivals and mine else None,
        })

    attempts = tx.attempts(fair.id, pet.id)
    best = best_runs(attempts).get(pet.id)
    pending = next((a for a in attempts if a.submitted_at is None
                    and now <= min(fair.results_at, a.started_at + a.seconds + config.fair_submit_seconds)), None)
    game = GAMES[fair.game]
    rules = run_rules(pet, perks, now, rejected_last_fair(tx, fair, pet.id), config, game,
                      amicus_boosts(tx, fair, pet.id, config))
    return {
        "fair": {"id": fair.id, "state": state, "opens_at": fair.opens_at, "closes_at": fair.closes_at,
                 "results_at": fair.results_at},
        "game": {"id": game.id, "name": game.name, "page": game.page},
        "roles": roles,
        "applications": {
            "limit": application_limit(perks, entry, others, config),
            "used": len(entry.roles),
            "locked": entry.locked_at is not None,
            "late_changes_left": max(0, perks.late_changes - entry.late_changes),
            "referral": perks.referrals > 0 and entry.referral is None,
        },
        "runs": {
            "allowed": 1 + perks.fair_retries,
            "used": len(attempts),
            "best": best and _run_view(best),
            "pending": pending.id if pending else None,
        },
        "rules": {"seconds": rules.seconds, "max_hits": rules.max_hits, "multiplier": rules.multiplier,
                  "boosts": [{"label": label, "value": value} for label, value in rules.boosts]},
        "committee_letter": committee_letter(pet, perks),
        "moves": {
            "amicus": perks.amicus_score > 0 and {
                "backing": names[entry.backing].name if entry.backing in names else None,
                "mine": perks.amicus_score, "theirs": perks.amicus_backed_score,
                "too_late": bool(attempts),
            },
            "short": perks.short_seller and {
                "on": names[entry.short_on].name if entry.short_on in names else None,
                "stake": entry.short_stake, "max": round(max(0.0, pet.money / 2), 2),
            },
        },
        "last": _last_results(tx, pet, career, perks, names),
    }


def _run_view(a: Attempt) -> dict:
    return {"score": a.score, "raw_score": a.raw_score, "hits": a.hits, "stats": a.stats}


def _last_results(tx: Tx, pet: Pet, career: Career, perks: Perks, pets: dict[int, Pet]) -> dict | None:
    fair = tx.last_resolved_fair()
    if fair is None:
        return None
    entries = tx.entries(fair.id)
    mine = next((e for e in entries if e.pet_id == pet.id), None)
    winners = {e.won: pets[e.pet_id].name for e in entries if e.won and e.pet_id in pets}
    won = mine and mine.won and career.roles.get(mine.won)
    best = best_runs(tx.attempts(fair.id, pet.id)).get(pet.id)
    return {
        "id": fair.id,
        "game": GAMES[fair.game].name,
        "results_at": fair.results_at,
        "applied": bool(mine and mine.roles),
        "won": {"company": won.company, "title": won.title} if won else None,
        "run": best and _run_view(best),
        "short": mine and mine.short_on is not None and {
            "on": pets[mine.short_on].name if mine.short_on in pets else "a pet",
            "stake": mine.short_stake, "payout": mine.short_payout,
        },
        "placements": [
            {"company": career.roles[rid].company, "title": career.roles[rid].title, "winner": winners.get(rid)}
            for rid in fair.roles if rid in career.roles and can_see(career.roles[rid], perks)
        ],
    }
