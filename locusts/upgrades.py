"""Career upgrade paths, loaded from locusts/careers/*.toml.

A build is how many tiers a pet owns on each path of one career, like {"grind": 5, "industry": 2}.
Legal builds follow the Bloons TD 6 crosspath rule (see Config).
"""

import tomllib
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path

from locusts.config import DEFAULT_CONFIG, Config
from locusts.perks import effect_errors

CAREERS_DIR = Path(__file__).parent / "careers"

Build = dict[str, int]


@dataclass(frozen=True)
class Tier:
    name: str
    cost: float
    flavor: str
    # What the tier does now, backed by `effects` (see locusts/perks.py), and what it will do once the
    # systems it needs (task events and GPA, items, pets interacting) exist.
    perks: tuple[str, ...]
    later: tuple[str, ...]
    effects: dict


@dataclass(frozen=True)
class UpgradePath:
    id: str
    name: str
    focus: str
    buildings: tuple[str, ...]
    tiers: tuple[Tier, ...]


@dataclass(frozen=True)
class Role:
    """A job fair role. Winning it gives an internship until the next fair's results."""

    id: str
    company: str
    title: str
    # Weekly pay as a multiple of config.internship_pay_per_week.
    pay: float
    # Path id -> lowest tier an applicant needs.
    requires: dict[str, int] = field(default_factory=dict)
    # Only pets with a perk that unlocks this key can see and apply to the role.
    unlock: str | None = None
    # Applicants need a raw minigame score in the top `top` of everyone who played at the fair.
    top: int | None = None
    # The internship ends if the pet gets exhausted.
    lost_on_exhaustion: bool = False
    # Never posted. Held for pets whose perks reserve a role, and given to them when they win nothing else.
    reserved: bool = False
    # The lowest GPA an applicant can have.
    gpa: float | None = None

    @property
    def regular(self) -> bool:
        return self.unlock is None


@dataclass(frozen=True)
class Career:
    id: str
    name: str
    # The career's signature mechanic, shown when a new player picks a career.
    summary: str
    paths: dict[str, UpgradePath]
    item: str = ""
    energy_drain_multiplier: float = 1.0
    task_pay_multiplier: float = 1.0
    roles: dict[str, Role] = field(default_factory=dict)


class CareerError(ValueError):
    pass


def is_legal(career: Career, build: Build, config: Config = DEFAULT_CONFIG) -> bool:
    if any(p not in career.paths for p in build):
        return False
    tiers = [t for t in build.values() if t != 0]
    return (
        all(0 < t <= config.upgrade_tiers for t in tiers)
        and len(tiers) <= config.max_upgraded_paths
        and sum(t > config.crosspath_max_tier for t in tiers) <= 1
    )


def can_upgrade(career: Career, build: Build, path_id: str, config: Config = DEFAULT_CONFIG) -> bool:
    return is_legal(career, {**build, path_id: build.get(path_id, 0) + 1}, config)


def upgrade_blocker(
    career: Career, build: Build, path_id: str, config: Config = DEFAULT_CONFIG
) -> str | None:
    """Why `path_id` can't take its next tier under the crosspath rule, or None if it can."""
    if path_id not in career.paths:
        return f"{career.name} has no path {path_id}"
    name = career.paths[path_id].name
    tier = build.get(path_id, 0)
    others = {p: t for p, t in build.items() if p != path_id and t > 0}
    if tier >= config.upgrade_tiers:
        return f"{name} is maxed out"
    if tier == 0 and len(others) >= config.max_upgraded_paths:
        return f"only {config.max_upgraded_paths} {career.name} paths can be upgraded"
    deep = [p for p, t in others.items() if t > config.crosspath_max_tier]
    if tier >= config.crosspath_max_tier and deep:
        return (f"{name} is capped at tier {config.crosspath_max_tier} because "
                f"{career.paths[deep[0]].name} is past it")
    return None


_loaded: dict[int, tuple[Config, dict[str, Career]]] = {}


def careers_for(config: Config = DEFAULT_CONFIG) -> dict[str, Career]:
    """The careers with tier costs from `config`, loaded once per config."""
    hit = _loaded.get(id(config))
    if hit is None or hit[0] is not config:
        hit = _loaded[id(config)] = (config, load_all(config))
    return hit[1]


def build_cost(career: Career, build: Build) -> float:
    return sum(sum(t.cost for t in career.paths[p].tiers[:n]) for p, n in build.items())


def deepest_builds(career: Career, config: Config = DEFAULT_CONFIG) -> dict[tuple[str, str], float]:
    """XP cost of every maxed build, keyed by (main path, side path)."""
    return {
        (main, side): build_cost(
            career, {main: config.upgrade_tiers, side: config.crosspath_max_tier}
        )
        for main, side in permutations(career.paths, 2)
    }


def load_career(path: Path, config: Config = DEFAULT_CONFIG) -> Career:
    data = tomllib.loads(path.read_text())
    paths: dict[str, UpgradePath] = {}
    for raw in data.get("path", []):
        if raw["id"] in paths:
            raise CareerError(f"{path.name}: duplicate path {raw['id']}")
        raw_tiers = raw.get("tier", [])
        if any("cost" in t for t in raw_tiers):
            raise CareerError(f"{path.name}: tier costs are set by tier_sessions in config.py")
        paths[raw["id"]] = UpgradePath(
            id=raw["id"],
            name=raw["name"],
            focus=raw.get("focus", ""),
            buildings=tuple(raw.get("buildings", [])),
            tiers=tuple(
                Tier(
                    name=t["name"],
                    cost=config.tier_sessions[i] * config.xp_per_session
                    if i < len(config.tier_sessions) else 0.0,
                    flavor=t.get("flavor", ""),
                    perks=tuple(t.get("perks", [])),
                    later=tuple(t.get("later", [])),
                    effects=t.get("effects", {}),
                )
                for i, t in enumerate(raw_tiers)
            ),
        )
    roles: dict[str, Role] = {}
    for raw in data.get("role", []):
        unknown = set(raw) - _ROLE_FIELDS
        if unknown:
            raise CareerError(f"{path.name}: role {raw.get('id')} has unknown fields {sorted(unknown)}")
        if raw["id"] in roles:
            raise CareerError(f"{path.name}: duplicate role {raw['id']}")
        roles[raw["id"]] = Role(**raw)
    career = Career(paths=paths, roles=roles, **data["career"])
    _validate(career, path, config)
    return career


_ROLE_FIELDS = {"id", "company", "title", "pay", "requires", "unlock", "top", "lost_on_exhaustion", "reserved",
                "gpa"}


def load_all(config: Config = DEFAULT_CONFIG, careers_dir: Path = CAREERS_DIR) -> dict[str, Career]:
    careers: dict[str, Career] = {}
    owners: dict[str, str] = {}
    role_owners: dict[str, str] = {}
    for file in sorted(careers_dir.glob("*.toml")):
        career = load_career(file, config)
        if career.id in careers:
            raise CareerError(f"{file.name}: duplicate career {career.id}")
        for role_id in career.roles:
            if role_id in role_owners:
                raise CareerError(f"{file.name}: role {role_id} is already a {role_owners[role_id]} role")
            role_owners[role_id] = career.id
        for p in career.paths.values():
            for building in p.buildings:
                if building in owners:
                    raise CareerError(
                        f"{file.name}: {building} already trains {owners[building]}, so players "
                        "couldn't tell the careers apart"
                    )
                owners[building] = career.id
        careers[career.id] = career
    return careers


def _validate(career: Career, path: Path, config: Config) -> None:
    if len(config.tier_sessions) != config.upgrade_tiers:
        raise CareerError(f"config.tier_sessions needs {config.upgrade_tiers} costs")
    if len(career.paths) != config.upgrade_paths:
        raise CareerError(
            f"{path.name}: has {len(career.paths)} paths, expected {config.upgrade_paths}"
        )
    claimed: dict[str, str] = {}
    for p in career.paths.values():
        if len(p.tiers) != config.upgrade_tiers:
            raise CareerError(
                f"{path.name}: path {p.id} has {len(p.tiers)} tiers, expected {config.upgrade_tiers}"
            )
        if not p.buildings:
            raise CareerError(f"{path.name}: path {p.id} needs at least one building")
        for n, tier in enumerate(p.tiers, 1):
            where = f"{path.name}: {p.id} tier {n}"
            errors = effect_errors(tier.effects, config.buildings)
            if errors:
                raise CareerError(f"{where}: {'; '.join(errors)}")
            if bool(tier.perks) != bool(tier.effects):
                raise CareerError(
                    f"{where}: perks describe what effects do, so a tier needs both or neither "
                    "(put descriptions of perks that don't work yet in later)"
                )
            if not tier.perks and not tier.later:
                raise CareerError(f"{where}: needs perks or later")
        for building in p.buildings:
            if building not in config.buildings:
                raise CareerError(f"{path.name}: path {p.id} trains at unknown building {building}")
            if building in claimed:
                raise CareerError(
                    f"{path.name}: paths {claimed[building]} and {p.id} both train at {building}, "
                    "so players couldn't tell them apart"
                )
            claimed[building] = p.id
    unlocked = {key for p in career.paths.values() for t in p.tiers for key in t.effects.get("unlocks", [])}
    for role in career.roles.values():
        where = f"{path.name}: role {role.id}"
        if isinstance(role.pay, bool) or not isinstance(role.pay, int | float) or role.pay <= 0:
            raise CareerError(f"{where}: pay must be a positive number")
        for path_id, tier in role.requires.items():
            if path_id not in career.paths:
                raise CareerError(f"{where}: requires unknown path {path_id}")
            if isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= config.upgrade_tiers:
                raise CareerError(f"{where}: requires {path_id} at a tier from 1 to {config.upgrade_tiers}")
        if role.unlock is not None and role.unlock not in unlocked:
            raise CareerError(f"{where}: no {career.name} tier unlocks {role.unlock}")
        if role.reserved and role.unlock is None:
            raise CareerError(f"{where}: a reserved role needs an unlock")
        if role.top is not None and (isinstance(role.top, bool) or not isinstance(role.top, int) or role.top < 1):
            raise CareerError(f"{where}: top must be a positive whole number")
        if role.gpa is not None and (isinstance(role.gpa, bool) or not isinstance(role.gpa, int | float)
                                     or not 0 < role.gpa <= config.gpa_max):
            raise CareerError(f"{where}: gpa must be above 0 and at most {config.gpa_max}")
    if not any(r.regular and not r.requires for r in career.roles.values()) and career.roles:
        raise CareerError(f"{path.name}: needs a regular role with no requirements, so new pets can apply")
