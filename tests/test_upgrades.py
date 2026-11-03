from dataclasses import replace

import pytest

from locusts.config import DEFAULT_CONFIG
from locusts.upgrades import (
    CareerError,
    build_cost,
    can_upgrade,
    deepest_builds,
    is_legal,
    load_all,
    load_career,
    upgrade_blocker,
)


@pytest.fixture(scope="module")
def tech():
    return load_all()["tech"]


@pytest.mark.parametrize(
    "build",
    [
        {},
        {"grind": 5, "industry": 2},
        {"research": 5, "grind": 2},
        {"industry": 3, "research": 1},
        {"grind": 2, "industry": 2},
        {"grind": 5, "industry": 0, "research": 2},
    ],
)
def test_legal_builds(tech, build):
    assert is_legal(tech, build)


@pytest.mark.parametrize(
    "build",
    [
        {"grind": 3, "industry": 3},
        {"grind": 5, "industry": 3},
        {"grind": 1, "industry": 1, "research": 1},
        {"grind": 6},
        {"grind": -1},
        {"networking": 1},
    ],
)
def test_illegal_builds(tech, build):
    assert not is_legal(tech, build)


def test_can_upgrade_follows_the_crosspath_rule(tech):
    assert can_upgrade(tech, {"grind": 4, "industry": 2}, "grind")
    assert not can_upgrade(tech, {"grind": 5, "industry": 2}, "grind")
    assert not can_upgrade(tech, {"grind": 5, "industry": 2}, "industry")
    assert not can_upgrade(tech, {"grind": 5, "industry": 2}, "research")
    assert can_upgrade(tech, {"grind": 2, "industry": 2}, "industry")
    assert not can_upgrade(tech, {"grind": 3, "industry": 2}, "industry")


def test_blocker_agrees_with_the_legality_check_for_every_build(tech):
    paths = list(tech.paths)
    for a in range(6):
        for b in range(6):
            for c in range(6):
                build = dict(zip(paths, (a, b, c)))
                if not is_legal(tech, build):
                    continue
                for path in paths:
                    blocked = upgrade_blocker(tech, build, path) is not None
                    assert blocked != can_upgrade(tech, build, path), (build, path)


def test_tier_costs_come_from_config(tech):
    for path in tech.paths.values():
        assert [t.cost for t in path.tiers] == [45, 90, 135, 180, 270]
    assert set(deepest_builds(tech).values()) == {855}
    assert build_cost(tech, {"grind": 4, "industry": 2}) == 585


def test_tier_costs_scale_with_config(tmp_path):
    config = replace(DEFAULT_CONFIG, xp_per_session=10.0, tier_sessions=(1, 1, 1, 1, 1))
    path = tmp_path / "x.toml"
    path.write_text(_career("x") + _three(buildings='["chem"]'))
    career = load_career(path, config)
    assert build_cost(career, {"a": 5, "b": 2}) == 70


def test_every_career_loads_with_its_own_buildings():
    careers = load_all()
    assert set(careers) == {"tech", "premed", "consulting", "finance", "prelaw"}
    owners = [b for c in careers.values() for p in c.paths.values() for b in p.buildings]
    assert len(owners) == len(set(owners))
    assert all(c.summary for c in careers.values())


def _career(career_id: str) -> str:
    return (
        f'[career]\nid = "{career_id}"\nname = "{career_id.upper()}"\nsummary = "s"\n'
        'item = "hat"\nenergy_drain_multiplier = 1.0\ntask_pay_multiplier = 1.0\n'
    )


def _path(path_id: str, buildings: str = "", tiers: int = 5, extra: str = "") -> str:
    buildings = buildings or f'["{path_id}"]'
    tier = f'[[path.tier]]\nname = "t"\nlater = ["someday"]\n{extra}'
    return f'[[path]]\nid = "{path_id}"\nname = "{path_id}"\nbuildings = {buildings}\n' + tier * tiers


def _three(**c_overrides) -> str:
    return _path("a", '["towne"]') + _path("b", '["levine"]') + _path("c", **c_overrides)


def _role(role_id: str = "r", extra: str = "") -> str:
    return f'[[role]]\nid = "{role_id}"\ncompany = "Co"\ntitle = "Intern"\npay = 1.0\n{extra}'


OPEN_ROLE = _role("open")


@pytest.mark.parametrize(
    ("paths", "error"),
    [
        (_path("a", '["towne"]') + _path("b", '["levine"]'), "has 2 paths"),
        (_three(buildings='["chem"]', tiers=4), "has 4 tiers"),
        (_path("a", '["towne"]') + _path("a", '["levine"]') + _path("c", '["chem"]'), "duplicate path"),
        (_three(buildings='["moon"]'), "unknown building"),
        (_three(buildings="[]"), "at least one building"),
        (_three(buildings='["chem", "towne"]'), "both train at towne"),
        (_three(buildings='["chem"]', extra="cost = 10\n"), "set by tier_sessions"),
        (_three(buildings='["chem"]', extra='perks = ["free money"]\n'), "needs both or neither"),
        (_three(buildings='["chem"]', extra="effects = { task_pay = 0.1 }\n"), "needs both or neither"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { free_money = 1 }\n'),
         "unknown effect free_money"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { task_pay = "lots" }\n'),
         "task_pay must be a non-negative number"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { billed_hours = 1 }\n'),
         "billed_hours can only be true"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { market = { leverage = 2 } }\n'),
         "market has no field leverage"),
        (_three(buildings='["chem"]',
                extra='perks = ["p"]\neffects = { task_site = { building = "moon", name = "x" } }\n'),
         "task_site is at unknown building moon"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { fair_retries = 0.5 }\n'),
         "fair_retries must be a non-negative whole number"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { unlocks = "quant" }\n'),
         "unlocks must be a list of names"),
        (_three(buildings='["chem"]') + _role(extra="requires = { a = 1 }\n"), "needs a regular role with no"),
        (_three(buildings='["chem"]') + OPEN_ROLE + _role(extra="requires = { z = 1 }\n"),
         "requires unknown path z"),
        (_three(buildings='["chem"]') + OPEN_ROLE + _role(extra="requires = { a = 6 }\n"),
         "requires a at a tier from 1 to 5"),
        (_three(buildings='["chem"]') + OPEN_ROLE + _role(extra='unlock = "quant"\n'), "no X tier unlocks quant"),
        (_three(buildings='["chem"]') + OPEN_ROLE + _role(extra="salary = 5\n"), "unknown fields"),
        (_three(buildings='["chem"]') + OPEN_ROLE + _role("open"), "duplicate role open"),
        (_three(buildings='["chem"]') + _role("open").replace("pay = 1.0", "pay = 0"),
         "pay must be a positive number"),
        (_three(buildings='["chem"]', extra='perks = ["p"]\neffects = { unlocks = ["q"] }\n') + OPEN_ROLE
         + _role(extra='unlock = "q"\ntop = 0\n'), "top must be a positive whole number"),
        (_three(buildings='["chem"]') + OPEN_ROLE + _role(extra="reserved = true\n"),
         "a reserved role needs an unlock"),
    ],
)
def test_validation_errors(tmp_path, paths, error):
    path = tmp_path / "bad.toml"
    path.write_text(_career("x") + paths)
    with pytest.raises(CareerError, match=error):
        load_career(path)


def test_two_careers_cannot_share_a_building(tmp_path):
    (tmp_path / "x.toml").write_text(_career("x") + _three(buildings='["chem"]'))
    (tmp_path / "y.toml").write_text(
        _career("y") + _path("a", '["moore"]') + _path("b", '["irvine"]') + _path("c", '["towne"]')
    )
    with pytest.raises(CareerError, match="towne already trains x"):
        load_all(careers_dir=tmp_path)


def test_role_ids_are_unique_across_careers(tmp_path):
    (tmp_path / "x.toml").write_text(_career("x") + _three(buildings='["chem"]') + OPEN_ROLE)
    (tmp_path / "y.toml").write_text(
        _career("y") + _path("a", '["moore"]') + _path("b", '["irvine"]') + _path("c", '["gittis"]') + OPEN_ROLE
    )
    with pytest.raises(CareerError, match="role open is already a x role"):
        load_all(careers_dir=tmp_path)


def test_every_career_has_roles_and_its_unlocks_lead_somewhere():
    for career in load_all().values():
        assert any(r.regular and not r.requires for r in career.roles.values()), career.id
        unlocks = {k for p in career.paths.values() for t in p.tiers for k in t.effects.get("unlocks", [])}
        assert unlocks == {r.unlock for r in career.roles.values() if r.unlock}, career.id


def test_two_files_cannot_declare_the_same_career(tmp_path):
    (tmp_path / "x.toml").write_text(_career("x") + _three(buildings='["chem"]'))
    (tmp_path / "y.toml").write_text(
        _career("x") + _path("a", '["moore"]') + _path("b", '["irvine"]') + _path("c", '["gittis"]')
    )
    with pytest.raises(CareerError, match="duplicate career x"):
        load_all(careers_dir=tmp_path)
